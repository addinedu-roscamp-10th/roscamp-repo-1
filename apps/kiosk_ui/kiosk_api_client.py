"""
kiosk_api_client.py
====================
키오스크 전용 FastAPI 클라이언트 모듈.

모든 HTTP 호출을 이 모듈 한 곳에서 관리한다.
각 메서드는 백그라운드 스레드에서 urllib로 동기 호출하고,
결과를 Qt 시그널(finished_signal)로 메인 스레드에 돌려준다.

사용법 (PageManager에서 인스턴스 생성 후 각 페이지에 주입):
    api = KioskApiClient()

    # 상품 전체 조회 (category_brand)
    api.fetch_all_shoes(callback=lambda data: ...)

    # 키워드 검색 (search → search_result)
    api.search(keyword="러닝화", accumulated_tags={}, callback=lambda data: ...)

    # 상품 상세 (tryon)
    api.fetch_shoe_information(shoe_id="NK-AF1", callback=lambda data: ...)

    # 좌석 현황 (tryon)
    api.fetch_seat_status(callback=lambda data: ...)

콜백은 항상 메인 스레드에서 실행된다 (QMetaObject.invokeMethod 사용).
실패 시 콜백에 None이 전달된다.

엔드포인트 매핑:
    GET  /find_shoe?data={"shoe_id":""}         → fetch_all_shoes()
    POST /find_shoe?data={"shoe_id":"..."}      → (내부 사용)
    POST /search    body={keyword, accumulated_tags}  → search()
    POST /find_shoe_information?data={"shoe_id":"..."} → fetch_shoe_information()
    GET  /kiosk/seat/status                    → fetch_seat_status()
"""

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal

load_dotenv()

# ── 서버 주소 ────────────────────────────────────────────────
_HOST = os.environ.get("MOOSINSA_SERVICE_HOST", "localhost")
_PORT = os.environ.get("MOOSINSA_SERVICE_PORT", "8000")
BASE_URL = f"http://{_HOST}:{_PORT}"

# ── 타임아웃 (초) ────────────────────────────────────────────
_TIMEOUT = 10.0
_SEARCH_TIMEOUT = 60.0  # [검색타임아웃] M_LLM 파이프라인 최대 35초 이상 소요


# ── Qt 시그널 브리지 ─────────────────────────────────────────
class _SignalBridge(QObject):
    """백그라운드 스레드 → 메인 스레드 결과 전달용 Qt 시그널 래퍼."""
    done = Signal(object, object)   # (callback, result)


# ── API 클라이언트 ────────────────────────────────────────────
class KioskApiClient:
    """
    키오스크 전용 FastAPI HTTP 클라이언트.

    모든 요청은 daemon 스레드에서 실행되므로 UI가 블로킹되지 않는다.
    결과는 Qt 시그널을 통해 메인 스레드의 콜백으로 전달된다.
    """

    def __init__(self):
        self._bridge = _SignalBridge()
        self._bridge.done.connect(self._dispatch)

    # ════════════════════════════════════════════════════════
    # Public API
    # ════════════════════════════════════════════════════════

    def fetch_all_shoes(self, callback: Callable[[Optional[list]], None]):
        """
        전체 상품 목록 조회.
        POST /find_shoe?data={"shoe_id":""}

        callback 인자:
            list[dict] — 상품 목록 (shoe_id, brand, model, price, colors, sizes, tags, image_url)
            None       — 실패
        """
        self._run(self._req_find_shoe, args=("",), callback=callback)

    def search(
        self,
        keyword: str,
        accumulated_tags: dict,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        키워드 기반 LLM 상품 검색.
        POST /search  body={keyword, accumulated_tags}

        callback 인자:
            dict — {results: list[ShoeItem], count, accumulated_tags, debug}
            None — 실패
        """
        self._run(
            self._req_search,
            args=(keyword, accumulated_tags),
            callback=callback,
        )

    def fetch_shoe_information(
        self,
        shoe_id: str,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        특정 상품의 색상·사이즈·재고 조회 (shoe_inventory 기반).
        POST /find_shoe_information?data={"shoe_id":"..."}

        callback 인자:
            dict — {shoe_id, brand, model, colors, sizes, price, ...}
                   colors: [{label, hex, stock}, ...]
                   sizes:  [{label, stock}, ...]
            None — 실패
        """
        self._run(self._req_find_shoe_information, args=(shoe_id,), callback=callback)

    def fetch_shoe_full(
        self,
        shoe_id: str,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        [상품상세재고연동] 상품 상세 + 재고 통합 조회.
        /find_shoe (이름/가격/이미지) + /find_shoe_information (색상/사이즈/재고) 합산.

        callback 인자:
            dict — shoes 행 + inventory 키(list[{size, color, stock}])
            None — 실패
        """
        self._run(self._req_shoe_full, args=(shoe_id,), callback=callback)  # [상품상세재고연동]

    def check_stock(
        self,
        shoe_id: str,
        color: str,
        size: str,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        [시착요청연동] 시착 요청 직전 재고 확인.
        POST /kiosk/stock/check

        callback 인자:
            dict — {"in_stock": bool, "stock": int}
            None — 통신 실패
        """
        self._run(self._req_stock_check, args=(shoe_id, color, size), callback=callback)

    def request_tryon(
        self,
        shoe_id: str,
        color: str,
        size: str,
        seat_id: int,
        robot_id: str,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        [시착요청연동] 시착 요청 (로봇 배송 시작).
        POST /tryon/request

        callback 인자:
            dict — {"success": True, "robot_id": ..., "seat_id": ..., "product_id": ...}
            dict — {"success": False, "detail": "..."} (409 좌석/로봇 충돌)
            None — 통신 실패
        """
        self._run(
            self._req_tryon_request,
            args=(shoe_id, color, size, seat_id, robot_id),
            callback=callback,
        )

    def poll_tryon_progress(
        self,
        robot_id: str,
        callback: Callable[[Optional[dict]], None],
    ):
        """
        [시착요청연동] 배송 진행률 단발 폴링.
        POST /kiosk/tryon/progress

        callback 인자:
            dict — {"robot_id": str, "stage": int, "progress_pct": float,
                    "arrived": bool, "seat_id": int|None}
            None — 통신 실패
        """
        self._run(self._req_tryon_progress, args=(robot_id,), callback=callback)

    def fetch_seat_status(self, callback: Callable[[Optional[dict]], None]):
        """
        시착 좌석 현황 조회.
        GET /kiosk/seat/status

        callback 인자:
            dict — {"seats": {"1": bool, "2": bool, "3": bool, "4": bool}}
                   True = 점유, False = 빈 자리
            None — 실패
        """
        self._run(self._req_seat_status, args=(), callback=callback)

    # ════════════════════════════════════════════════════════
    # 내부: 스레드 실행
    # ════════════════════════════════════════════════════════

    def _run(self, func, args: tuple, callback: Callable):
        """func을 daemon 스레드에서 실행하고 결과를 Qt 시그널로 돌려준다."""
        def _worker():
            try:
                result = func(*args)
            except Exception as e:
                print(f"[KioskApiClient] 예외 ({func.__name__}): {e}")
                result = None
            self._bridge.done.emit(callback, result)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    @staticmethod
    def _dispatch(callback: Callable, result):
        """Qt 시그널 수신 → 메인 스레드에서 콜백 실행."""
        try:
            callback(result)
        except Exception as e:
            print(f"[KioskApiClient] 콜백 예외: {e}")

    # ════════════════════════════════════════════════════════
    # 내부: 실제 HTTP 요청
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _post_json(url: str, body: dict, timeout: float = _TIMEOUT) -> dict:  # [검색타임아웃]
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # [검색타임아웃]
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _get_json(url: str) -> dict:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── 개별 엔드포인트 ──────────────────────────────────────

    def _req_find_shoe(self, shoe_id: str) -> Optional[list]:
        """POST /find_shoe?data={"shoe_id":"..."} → list[dict]"""
        query = json.dumps({"shoe_id": shoe_id}, ensure_ascii=False)
        import urllib.parse
        url = f"{BASE_URL}/find_shoe?data={urllib.parse.quote(query)}"
        raw = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(raw, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # 서버가 단일 dict 또는 list 반환 가능 — 항상 list로 정규화
        if isinstance(data, dict):
            return [data]
        return data if isinstance(data, list) else []

    def _req_search(self, keyword: str, accumulated_tags: dict) -> Optional[dict]:
        """POST /search → {results, count, accumulated_tags, debug}"""
        url = f"{BASE_URL}/search"
        return self._post_json(url, {  # [검색타임아웃]
            "keyword": keyword,
            "accumulated_tags": accumulated_tags,
        }, _SEARCH_TIMEOUT)

    def _req_find_shoe_information(self, shoe_id: str) -> Optional[list]:
        """POST /find_shoe_information?data={"shoe_id":"..."} → list[dict]"""
        import urllib.parse
        query = json.dumps({"shoe_id": shoe_id}, ensure_ascii=False)
        url = f"{BASE_URL}/find_shoe_information?data={urllib.parse.quote(query)}"
        raw = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(raw, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # [상품상세재고연동] shoes_inventory fetchall() → 전체 inventory 행 반환
        if isinstance(data, list):
            return data
        return [data] if data else []

    def _req_shoe_full(self, shoe_id: str) -> Optional[dict]:
        """[상품상세재고연동] /find_shoe + /find_shoe_information 통합 조회"""
        shoe_list = self._req_find_shoe(shoe_id)
        shoe = shoe_list[0] if shoe_list else {}
        inventory = self._req_find_shoe_information(shoe_id) or []
        if not shoe:
            return None
        shoe["inventory"] = inventory  # [상품상세재고연동] inventory 행 리스트 첨부
        return shoe

    def _req_seat_status(self) -> Optional[dict]:
        """GET /kiosk/seat/status → {"seats": {...}}"""
        url = f"{BASE_URL}/kiosk/seat/status"
        return self._get_json(url)

    def _req_stock_check(self, shoe_id: str, color: str, size: str) -> Optional[dict]:
        """[시착요청연동] POST /kiosk/stock/check → {"in_stock": bool, "stock": int}"""
        url = f"{BASE_URL}/kiosk/stock/check"
        return self._post_json(url, {"shoe_id": shoe_id, "color": color, "size": size})

    def _req_tryon_request(
        self, shoe_id: str, color: str, size: str, seat_id: int, robot_id: str
    ) -> Optional[dict]:
        """[시착요청연동] POST /tryon/request → success dict 또는 {"success": False, "detail": str} on 409"""
        import urllib.error
        url = f"{BASE_URL}/tryon/request"
        body = {
            "product_id": shoe_id,
            "color":      color,
            "size":       size,
            "seat_id":    int(seat_id),
            "robot_id":   robot_id,
        }
        try:
            return self._post_json(url, body)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = err.get("detail", "시착 요청에 실패했습니다.")
            except Exception:
                detail = "시착 요청에 실패했습니다."
            return {"success": False, "detail": detail}

    def _req_tryon_progress(self, robot_id: str) -> Optional[dict]:
        """[시착요청연동] POST /kiosk/tryon/progress → {robot_id, stage, progress_pct, arrived, seat_id}"""
        url = f"{BASE_URL}/kiosk/tryon/progress"
        return self._post_json(url, {"robot_id": robot_id})


# ════════════════════════════════════════════════════════════
# 서버 응답 → 각 페이지 내부 dict 포맷 변환 유틸
# ════════════════════════════════════════════════════════════

def normalize_shoes_for_category(raw: list) -> dict[str, list]:
    """
    /find_shoe 전체 목록 → CategoryBrandPage MOCK_DB 형식으로 변환.

    반환:
        {
            "런닝":    [{"name": ..., "price": "₩...", "shoe_id": ...}, ...],
            "스니커즈": [...],
            ...
            "나이키":  [...],   # brand 기준
            ...
        }

    tags 필드의 키워드를 보고 카테고리 버킷에 넣는다.
    tags가 없으면 brand 버킷에만 들어간다.
    """
    CAT_KEYWORDS = {
        "런닝":    ["런닝", "조깅", "running", "run"],
        "스니커즈": ["스니커즈", "스니커", "casual", "캐주얼", "sneaker"],
        "구두":    ["구두", "드레스", "dress", "oxford", "derby", "loafer", "로퍼"],
        "단화":    ["단화", "플랫", "flat", "slip"],
    }

    cat_buckets: dict[str, list] = {k: [] for k in CAT_KEYWORDS}
    brand_buckets: dict[str, list] = {}

    for item in raw:
        entry = {
            "name":     item.get("model", ""),
            "price":    f"₩{int(item.get('price', 0)):,}",
            "shoe_id":  item.get("shoe_id", ""),
            "brand":    item.get("brand", ""),
            "image_url": item.get("image_url", None),
        }

        # 카테고리 분류
        tags_str = (item.get("tags") or "").lower()
        matched_cat = False
        for cat, keywords in CAT_KEYWORDS.items():
            if any(kw in tags_str for kw in keywords):
                cat_buckets[cat].append(entry)
                matched_cat = True
                break

        # 브랜드 분류
        brand = item.get("brand", "기타")
        if brand not in brand_buckets:
            brand_buckets[brand] = []
        brand_buckets[brand].append(entry)

    result = {}
    result.update(cat_buckets)
    result.update(brand_buckets)
    return result


def normalize_search_results(raw: dict) -> list:
    """
    /search 응답 → SearchResultPage MOCK_RESULTS 형식으로 변환.

    반환:
        [{"rank": 1, "name": ..., "brand": ..., "price": int,
          "tag": ..., "shoe_id": ..., "image_url": ...}, ...]
    """
    results = raw.get("results", [])
    normalized = []
    for i, item in enumerate(results):
        normalized.append({
            "rank":      i + 1,
            "name":      item.get("model", ""),
            "brand":     item.get("brand", ""),
            "price":     int(item.get("price", 0)),
            "tag":       item.get("tags", ""),
            "shoe_id":   item.get("shoe_id", ""),
            "image_url": item.get("image_url", None),
            "score":     item.get("score", 0.0),
        })
    return normalized


def normalize_shoe_for_tryon(raw: dict) -> dict:
    """
    fetch_shoe_full 응답 → TryonPage MOCK_PRODUCT 형식으로 변환.

    raw 구조 (fetch_shoe_full 반환값):
        {
            "shoe_id": "NK-AF1",  "brand": "나이키",  "model": "에어포스 1 '07",
            "price": 129000,  "image_url": "...",  "tags": "...",
            "inventory": [                           # shoes_inventory 행 리스트
                {"shoe_id": "NK-AF1", "size": 255.0, "color": "Black", "stock": 5},
                ...
            ]
        }

    반환 (TryonPage MOCK_PRODUCT 형식):
        {
            "name": "에어포스 1 '07",  "brand": "나이키",  "price": 129000,
            "shoe_id": "NK-AF1",  "image_url": "...",
            "colors": [{"label": "Black", "hex": "#1C1C1C", "stock": True}, ...],
            "sizes":  [{"label": "255", "stock": True}, ...],
        }
    """
    COLOR_HEX_MAP = {
        "black":  "#1C1C1C", "블랙": "#1C1C1C",
        "white":  "#F5F1EC", "화이트": "#F5F1EC",
        "red":    "#C0392B", "레드": "#C0392B",
        "brown":  "#5C4A3A", "브라운": "#5C4A3A",
        "navy":   "#1A2A4A", "네이비": "#1A2A4A",
        "grey":   "#888888", "그레이": "#888888", "gray": "#888888",
        "beige":  "#D4C5A9", "베이지": "#D4C5A9",
        "green":  "#2C5F2E", "그린": "#2C5F2E",
        "blue":   "#1E4D8C", "블루": "#1E4D8C",
        "yellow": "#F5C518", "옐로우": "#F5C518",
        "pink":   "#F4A0B0", "핑크": "#F4A0B0",
        "orange": "#E8751A", "오렌지": "#E8751A",
    }

    def _color_entry(label: str, in_stock: bool):
        return {
            "label": label,
            "hex":   COLOR_HEX_MAP.get(label.lower(), "#888888"),
            "stock": in_stock,
        }

    def _fmt_size(v) -> str:
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)
        except Exception:
            return str(v)

    # [상품상세재고연동] inventory 행 기반으로 colors/sizes 빌드
    inventory = raw.get("inventory", [])
    if inventory:
        from collections import defaultdict
        color_stock: dict = defaultdict(int)
        size_stock: dict  = defaultdict(int)
        # 색상/사이즈 삽입 순서 유지를 위해 별도 리스트 사용
        color_order: list = []
        size_order:  list = []
        color_sizes: dict = {}   # [크로스필터] color → [size_str, ...]
        size_colors: dict = {}   # [크로스필터] size_str → [color, ...]
        for row in inventory:
            color = (row.get("color") or "").strip()
            size  = row.get("size")
            stock = int(row.get("stock") or 0)
            if color:
                color_stock[color] += stock
                if color not in color_order:
                    color_order.append(color)
            if size is not None:
                sk = _fmt_size(size)
                size_stock[sk] += stock
                if sk not in size_order:
                    size_order.append(sk)
            if color and size is not None:
                sk = _fmt_size(size)
                color_sizes.setdefault(color, [])
                if sk not in color_sizes[color]:
                    color_sizes[color].append(sk)
                size_colors.setdefault(sk, [])
                if color not in size_colors[sk]:
                    size_colors[sk].append(color)

        colors = [_color_entry(c, color_stock[c] > 0) for c in color_order]
        sizes  = sorted(
            [{"label": s, "stock": size_stock[s] > 0} for s in size_order],
            key=lambda x: float(x["label"]) if x["label"].replace(".", "", 1).isdigit() else 0,
        )
    else:
        color_sizes = {}
        size_colors = {}
        # inventory 없으면 shoes.colors 필드 사용 (재고 정보 없음)
        raw_colors = raw.get("colors", [])
        if isinstance(raw_colors, str):
            try:
                raw_colors = json.loads(raw_colors)
            except Exception:
                raw_colors = []
        colors = [
            _color_entry(c.get("label", str(c)) if isinstance(c, dict) else str(c), True)
            for c in raw_colors
        ]

        raw_sizes = raw.get("sizes", [])
        if isinstance(raw_sizes, str):
            try:
                raw_sizes = json.loads(raw_sizes)
            except Exception:
                raw_sizes = []
        sizes = [
            {"label": str(s.get("label", s)), "stock": s.get("stock", True)}
            if isinstance(s, dict) else {"label": _fmt_size(s), "stock": True}
            for s in raw_sizes
        ]

    return {
        "name":        raw.get("model", ""),
        "brand":       raw.get("brand", ""),
        "price":       int(raw.get("price", 0)),
        "shoe_id":     raw.get("shoe_id", ""),
        "image_url":   raw.get("image_url", None),
        "colors":      colors,
        "sizes":       sizes,
        "color_sizes": color_sizes,
        "size_colors": size_colors,
    }


def normalize_seat_status(raw: dict) -> dict:
    """
    /kiosk/seat/status 응답 → TryonPage SEAT_STATUS 형식으로 변환.

    입력:  {"seats": {"1": true, "2": false, ...}}
    반환:  {"1": True, "2": False, ...}
    """
    seats = raw.get("seats", {})
    return {str(k): bool(v) for k, v in seats.items()}
