"""
kiosk_home.py
=============
키오스크 홈 화면 + 전체 페이지 네비게이션 허브.

[이 파일의 역할]
  1. MoosinsaKiosk   — 홈 UI (타일 4개)
  2. KioskApiClient  — FastAPI 서버(/kiosk/page_event)에 페이지 이벤트 비동기 전송  ★ NEW ★
  3. PageManager     — 페이지 show/hide 전환, 콜백 주입, 이벤트 전송 통합 관리     ★ NEW ★

[페이지 흐름]
  Home ──┬──► CategoryBrandPage
         ├──► ShoeSearchPage  ──► SearchResultPage
         ├──► InformationPage
         └──► PaymentCartPage ──► PaymentCompletePage

  모든 서브 페이지 TopBar:
    홈 버튼  → PageManager.go_home()
    이용안내 → PageManager.go_information(prev="현재페이지")

  InformationPage 닫기(✕) → PageManager.go_back()  (직전 페이지로 복귀)

[통신]
  키오스크 PC → POST http://{SERVICE_HOST}:8000/kiosk/page_event
  실패해도 UI 동작에 영향 없음 (fire-and-forget).
  SERVICE_HOST 는 환경변수 MOOSINSA_SERVICE_HOST 로 주입,
  없으면 기본값 "localhost" 사용.
"""

import os
import sys
import threading

from dotenv import load_dotenv          # ★ NEW ★
load_dotenv()                           # ★ NEW ★  .env 파일을 os.environ에 로드

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLabel, QFrame, QSizePolicy,
    QStackedWidget, QMessageBox                           # ★ NEW ★
)
from PySide6.QtCore import Qt, QByteArray, QTimer
from PySide6.QtSvgWidgets import QSvgWidget

# ── 페이지 임포트 ─────────────────────────────────────────────
from kiosk_category_brand    import CategoryBrandPage
from kiosk_search            import ShoeSearchPage
from kiosk_payment           import PaymentCartPage
from kiosk_information       import InformationPage
from kiosk_search_result     import SearchResultPage      # ★ NEW ★
from kiosk_payment_complete  import PaymentCompletePage   # ★ NEW ★
from kiosk_tryon             import TryonPage               # ★ NEW ★
from kiosk_tryon_delivery    import TryonDeliveryPage        # ★ NEW ★
from kiosk_tryon_arrive      import TryonArrivePage          # ★ NEW ★
from kiosk_tryon_another     import TryonAnotherPage         # ★ NEW ★
from kiosk_api_client import (                               # ★ NEW ★
    KioskApiClient as ShoeApiClient,
    normalize_search_results,
)

# ── Design reference resolution ──────────────────────────────
REF_W = 1080
REF_H = 1920

# ── Color palette ────────────────────────────────────────────
C_BG        = "#EDE9E3"
C_DARK      = "#1C1C1C"
C_FOREST    = "#2C3D30"
C_FOREST_T  = "#C8DDB8"
C_SUB_LIGHT = "#999999"
C_SUB_DARK  = "rgba(255,255,255,0.38)"

# ── Press overlay colors ──────────────────────────────────────
PRESS_COLOR = {
    C_BG:     "#D4CFC8",
    C_DARK:   "#383838",
    C_FOREST: "#3E5444",
}

# ── SVG templates ────────────────────────────────────────────
SVG_GRID = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <rect x="4" y="4" width="10" height="10" rx="2"/>
  <rect x="18" y="4" width="10" height="10" rx="2"/>
  <rect x="4" y="18" width="10" height="10" rx="2"/>
  <rect x="18" y="18" width="10" height="10" rx="2"/>
</svg>"""

SVG_SEARCH = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.5" stroke-linecap="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="14" cy="14" r="8"/>
  <line x1="20" y1="20" x2="27" y2="27"/>
</svg>"""

SVG_USER = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="16" cy="10" r="5"/>
  <path d="M6 27c0-5.523 4.477-10 10-10s10 4.477 10 10"/>
</svg>"""

SVG_CART = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M6 8h20l-2 12H8L6 8z"/>
  <circle cx="12" cy="26" r="2"/>
  <circle cx="22" cy="26" r="2"/>
  <path d="M10 12h12M10 16h8"/>
</svg>"""


def make_svg_widget(svg_template: str, color: str) -> QSvgWidget:
    svg_bytes = QByteArray(svg_template.format(color=color).encode())
    w = QSvgWidget()
    w.load(svg_bytes)
    w.setStyleSheet("background: transparent;")
    return w


# ══════════════════════════════════════════════════════════════
# KioskApiClient                                    ★ NEW ★
# ══════════════════════════════════════════════════════════════
class KioskApiClient:                                     # ★ NEW ★
    """
    FastAPI 서버의 /kiosk/page_event 엔드포인트에
    페이지 전환 이벤트를 fire-and-forget으로 전송한다.

    - 별도 데몬 스레드에서 urllib 로 POST (외부 의존성 없음)
    - 네트워크 실패 시 경고 출력 후 무시 → UI 블로킹 없음
    - SERVICE_HOST: 환경변수 MOOSINSA_SERVICE_HOST (기본 "localhost")
    - SERVICE_PORT: 환경변수 MOOSINSA_SERVICE_PORT (기본 "8000")
    - KIOSK_ID    : 환경변수 MOOSINSA_KIOSK_ID     (기본 "kiosk_1")
    """

    def __init__(self):
        host      = os.environ.get("MOOSINSA_SERVICE_HOST", "localhost")
        port      = os.environ.get("MOOSINSA_SERVICE_PORT", "8000")
        self._url = f"http://{host}:{port}/kiosk/page_event"
        self._id  = os.environ.get("MOOSINSA_KIOSK_ID", "kiosk_1")

    def send(self, page: str, prev: str | None = None):   # ★ NEW ★
        """
        페이지 전환 이벤트를 백그라운드 스레드로 전송한다.

        page : 전환된 페이지 식별자
               "home" | "category_brand" | "search" | "search_result"
               | "payment" | "payment_complete" | "information"
        prev : 직전 페이지 식별자 (최초 진입 시 None)
        """
        payload = {
            "page"    : page,
            "prev"    : prev,
            "kiosk_id": self._id,
        }
        t = threading.Thread(target=self._post, args=(payload,), daemon=True)
        t.start()

    def _post(self, payload: dict):                       # ★ NEW ★
        """실제 HTTP POST — 데몬 스레드에서 실행."""
        import json
        import urllib.request
        import urllib.error
        body = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            self._url,
            data    = body,
            headers = {"Content-Type": "application/json"},
            method  = "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3):
                pass  # 응답 본문 불필요
        except urllib.error.URLError as e:
            print(f"[KioskApiClient] 서버 전송 실패 (무시): {e.reason}")
        except Exception as e:
            print(f"[KioskApiClient] 예외 (무시): {e}")


# ══════════════════════════════════════════════════════════════
# PageManager                                       ★ CHANGED ★
# ══════════════════════════════════════════════════════════════
class PageManager:                                        # ★ CHANGED ★
    """
    QStackedWidget 기반 페이지 전환 매니저.

    책임:
      - 단일 QMainWindow 안의 QStackedWidget에 모든 페이지를 위젯으로 등록
      - setCurrentWidget() 으로 창 깜빡임 없이 화면 전환
      - 각 페이지에 on_home / on_guide / on_close 콜백 주입
      - 페이지 전환 시 KioskApiClient.send() 호출 (fire-and-forget)
      - InformationPage 닫기(✕) 는 _prev_page 로 복귀

    사용법:
      pm = PageManager()
      pm.start()   ← QMainWindow를 show() 하고 홈 페이지를 표시

    페이지 식별자:
      "home" | "category_brand" | "search" | "payment" | "information"
    """

    HOME             = "home"
    CATEGORY         = "category_brand"
    SEARCH           = "search"
    SEARCH_RESULT    = "search_result"    # ★ NEW ★
    PAYMENT          = "payment"
    PAYMENT_COMPLETE = "payment_complete" # ★ NEW ★
    INFORMATION      = "information"
    TRYON            = "tryon"             # ★ NEW ★
    TRYON_DELIVERY   = "tryon_delivery"    # ★ NEW ★
    TRYON_ARRIVE     = "tryon_arrive"      # ★ NEW ★
    TRYON_ANOTHER    = "tryon_another"     # ★ NEW ★

    def __init__(self):
        self._api       = KioskApiClient()
        self._shoe_api  = ShoeApiClient()     # ★ NEW ★ 상품 데이터 전용 API 클라이언트
        self._cur_page  = None
        self._prev_page = None   # information 닫기 시 복귀 대상
        self._accumulated_tags: dict = {}    # ★ NEW ★ 검색 누적 태그

        # ── 단일 QMainWindow + QStackedWidget ────────────────
        self._window = QMainWindow()                      # ★ CHANGED ★
        self._window.setWindowTitle("MOOSINSA Kiosk")
        self._window.resize(540, 960)

        self._stack = QStackedWidget()                    # ★ CHANGED ★
        self._window.setCentralWidget(self._stack)

        # ── 페이지 인스턴스 생성 (QWidget 상속) ──────────────
        self._home_page = MoosinsaKiosk(                  # ★ CHANGED ★
            on_category    = lambda: self._go(self.CATEGORY),
            on_search      = lambda: self._go(self.SEARCH),
            on_information = lambda: self._go(self.INFORMATION),
            on_payment     = lambda: self._go(self.PAYMENT),
        )
        self._category_page = CategoryBrandPage(          # ★ CHANGED ★
            on_home     = lambda: self._go(self.HOME),
            on_search   = lambda: self._go(self.SEARCH),
            on_purchase = lambda: self._go(self.PAYMENT),
            on_back     = lambda: self._go(self.HOME),
            on_product  = lambda p: self._go_tryon(p),
            api_client  = self._shoe_api,                  # ★ NEW ★
        )
        self._search_page = ShoeSearchPage(               # ★ CHANGED ★
            on_home   = lambda: self._go(self.HOME),
            on_back   = lambda: self._go(self.HOME),
            on_search = lambda q: self._go_search_result(q),
        )
        self._payment_page = PaymentCartPage(             # ★ CHANGED ★
            on_home  = lambda: self._go(self.HOME),
            on_back  = lambda: self._go(self.HOME),
            on_pay   = lambda: self._go(self.PAYMENT_COMPLETE),
        )
        self._info_page = InformationPage(                # ★ CHANGED ★
            on_home  = lambda: self._go(self.HOME),
            on_close = lambda: self._go_back(),
        )
        self._search_result_page = SearchResultPage(          # ★ NEW ★
            on_home          = lambda: self._go(self.HOME),
            on_back          = lambda: self._go(self.SEARCH),
            on_retry_search  = lambda: self._go(self.SEARCH),
            on_product_click = lambda p: self._go_tryon(p),
        )
        self._payment_complete_page = PaymentCompletePage(    # ★ NEW ★
            on_home  = lambda: self._go(self.HOME),
            on_back  = lambda: self._go(self.HOME),                 # ★ CHANGED ★
        )

        # ── 시착 시퀀스 페이지들 ──────────────────────────────
        self._tryon_page = TryonPage(                         # ★ NEW ★
            on_home          = lambda: self._go(self.HOME),
            on_back          = lambda: self._go(self.CATEGORY),
            on_tryon_request = lambda sel: self._go_tryon_delivery(sel),
            api_client       = self._shoe_api,                 # ★ NEW ★
        )
        self._tryon_delivery_page = TryonDeliveryPage(        # ★ NEW ★
            on_arrived = lambda: self._go_tryon_arrive(),
            api_client = self._shoe_api,                      # [시착요청연동]
        )
        self._tryon_arrive_page = TryonArrivePage(            # ★ NEW ★
            on_confirmed = lambda: self._go_tryon_another(),
        )
        self._tryon_another_page = TryonAnotherPage(          # ★ NEW ★
            on_home  = lambda: self._go(self.HOME),
            on_retry = lambda: self._go(self.TRYON),
        )

        # 식별자 → 인스턴스 매핑 & 스택에 등록
        self._pages: dict[str, QWidget] = {               # ★ CHANGED ★
            self.HOME             : self._home_page,
            self.CATEGORY         : self._category_page,
            self.SEARCH           : self._search_page,
            self.SEARCH_RESULT    : self._search_result_page,    # ★ NEW ★
            self.PAYMENT          : self._payment_page,
            self.PAYMENT_COMPLETE : self._payment_complete_page, # ★ NEW ★
            self.INFORMATION      : self._info_page,
            self.TRYON            : self._tryon_page,          # ★ NEW ★
            self.TRYON_DELIVERY   : self._tryon_delivery_page, # ★ NEW ★
            self.TRYON_ARRIVE     : self._tryon_arrive_page,   # ★ NEW ★
            self.TRYON_ANOTHER    : self._tryon_another_page,  # ★ NEW ★
        }
        for page in self._pages.values():
            self._stack.addWidget(page)                   # ★ CHANGED ★

    def start(self):                                      # ★ CHANGED ★
        """QMainWindow를 show() 하고 홈 페이지를 표시한다."""
        self._window.show()
        self._go(self.HOME)

    # ── 내부 전환 메서드 ─────────────────────────────────────

    def _go(self, target: str):                           # ★ CHANGED ★
        """
        QStackedWidget.setCurrentWidget() 으로 페이지를 전환한다.
        창 자체는 그대로 유지되므로 깜빡임이 없다.
        """
        prev = self._cur_page

        # information 이 아닌 곳으로 이동할 때 _prev_page 초기화.          # ★ CHANGED ★
        # 이용안내 복귀 대상이 잔류하면 엉뚱한 페이지로 튀는 버그 방지.     # ★ CHANGED ★
        # _go_info_from() 이 _prev_page 를 직접 설정하므로                 # ★ CHANGED ★
        # information 으로 갈 때만 유지, 나머지는 항상 초기화.              # ★ CHANGED ★
        if target != self.INFORMATION:                                     # ★ CHANGED ★
            self._prev_page = None                                         # ★ CHANGED ★

        # search 페이지에서 이용안내가 아닌 곳으로 이동하면 입력창 초기화
        if prev == self.SEARCH and target != self.INFORMATION:
            self._search_page.reset()

        self._stack.setCurrentWidget(self._pages[target])
        self._cur_page = target
        self._api.send(page=target, prev=prev)

    def _go_info_from(self, caller: str):                 # ★ CHANGED ★
        """이용안내로 이동하면서 복귀 대상을 저장한다."""
        self._prev_page = caller
        self._go(self.INFORMATION)

    def _go_back(self):                                   # ★ CHANGED ★
        """InformationPage 닫기(✕) → _prev_page 또는 홈으로 복귀."""
        target = self._prev_page or self.HOME
        self._prev_page = None
        self._go(target)

    def _go_tryon(self, product: dict):                       # ★ NEW ★
        """
        CategoryBrandPage 상품 클릭 → TryonPage 전환.
        product: category_brand의 ProductCard가 넘겨주는 dict
                 {name, price} → TryonPage.update_product()에 주입.
        TODO: 실제 상품 상세(colors, sizes)는 /find_shoe_information
              API로 조회 후 주입 필요.
        """
        self._tryon_page.update_product(product)
        self._go(self.TRYON)

    def _go_tryon_delivery(self, selection: dict):            # ★ NEW ★
        """
        TryonPage 요청 버튼 → TryonDeliveryPage 전환.
        selection: {product, color, size, seat}
        ■ 연동: POST /tryon/request 는 TryonPage._on_request_clicked()
          에서 직접 호출한 뒤 on_tryon_request 콜백으로 selection을 넘긴다.
          실제 API 호출은 TryonPage 내부에서 처리하고,
          성공 응답의 robot_id를 selection에 포함시켜 넘기는 것을 권장.
        """
        self._current_order = selection                        # 시퀀스 전반에 order 유지
        self._tryon_delivery_page.update_order(selection)
        self._go(self.TRYON_DELIVERY)

    def _go_tryon_arrive(self):                               # ★ NEW ★
        """
        TryonDeliveryPage on_arrived → TryonArrivePage 전환.
        타이머/confirmed 상태를 reset() 으로 초기화한 뒤 전환.
        ■ 연동: TryonDeliveryPage.notify_arrived() 가 WS /ws/kiosk/amr
          의 KIOSK_AMR_ARRIVE 수신 시 호출되고, 그 안에서 on_arrived()
          콜백이 실행되어 이 메서드로 연결된다.
        """
        self._tryon_arrive_page.reset()
        self._go(self.TRYON_ARRIVE)

    def _go_tryon_another(self):                              # ★ NEW ★
        """
        TryonArrivePage 수령완료/타임아웃 → TryonAnotherPage 전환.
        ■ 연동: POST /pickup/complete 는 TryonArrivePage._confirm() 에서
          직접 호출한다. 성공 후 on_confirmed() 콜백이 이 메서드로 연결.
        """
        order = getattr(self, '_current_order', {})
        self._tryon_another_page.update_order(order)
        self._go(self.TRYON_ANOTHER)

    def _go_search_result(self, query: str):
        """
        검색 실행 → API 호출 → SearchResultPage 전환.
        먼저 빈 결과로 페이지를 전환(로딩 상태)하고,
        API 응답이 오면 update_results()로 갱신한다.
        """
        # 누적 태그는 대화형 다중 검색에서 유지되나,
        # 키오스크는 단발 검색이므로 매번 초기화
        self._accumulated_tags = {}

        # 즉시 페이지 전환 (로딩 상태)
        self._search_result_page.update_results(query=query, results=None)  # [검색로딩]
        self._go(self.SEARCH_RESULT)

        # 백그라운드에서 API 호출 → 결과 주입
        def _on_result(data):
            if data:
                results = normalize_search_results(data)
                # 누적 태그 갱신 (다음 검색에 활용)
                self._accumulated_tags = data.get("accumulated_tags", {})
            else:
                results = []
            # 현재 search_result 페이지에 있는 경우에만 갱신
            self._search_result_page.update_results(query=query, results=results)

        self._shoe_api.search(
            keyword=query,
            accumulated_tags=self._accumulated_tags,
            callback=_on_result,
        )


# ══════════════════════════════════════════════════════════════
# MenuTile
# ══════════════════════════════════════════════════════════════
class MenuTile(QFrame):
    """
    터치/클릭 피드백: 배경색을 직접 교체 (밝은 타일 → 어둡게, 어두운 타일 → 밝게).
    최소 120ms 유지 후 원래 색으로 복원.
    """
    def __init__(
        self,
        bg_color: str,
        icon_svg: str,
        icon_color: str,
        sub_text: str,
        sub_color: str,
        main_text: str,
        main_color: str,
        on_click=None,
        border: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._bg_color    = bg_color
        self._press_color = PRESS_COLOR.get(bg_color, bg_color)
        self._border      = border
        self._sub_color   = sub_color
        self._main_color  = main_color
        self._radius      = 40
        self._is_pressed  = False
        self._on_click    = on_click or (lambda: None)

        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_bg)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._outer = QVBoxLayout(self)
        self._outer.addStretch(1)

        self._icon_svg = make_svg_widget(icon_svg, icon_color)
        self._outer.addWidget(self._icon_svg, alignment=Qt.AlignCenter)

        self._icon_spacer = QWidget()
        self._icon_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._outer.addWidget(self._icon_spacer)

        self._sub = QLabel(sub_text)
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._outer.addWidget(self._sub)

        self._mid_spacer = QWidget()
        self._mid_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._outer.addWidget(self._mid_spacer)

        self._main = QLabel(main_text)
        self._main.setWordWrap(True)
        self._main.setAlignment(Qt.AlignCenter)
        self._main.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._outer.addWidget(self._main)

        self._outer.addStretch(1)

    # ── press feedback ───────────────────────────────────────
    def mousePressEvent(self, event):
        self._is_pressed = True
        self._restore_timer.stop()
        self._apply_bg(self._press_color)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_pressed:
            self._is_pressed = False
            # 120ms 유지 후 복원 → 빠른 탭에도 피드백이 눈에 보임
            self._restore_timer.start(120)
            # 터치가 타일 안에서 끝났을 때만 클릭 콜백 실행
            if self.rect().contains(event.position().toPoint()):
                self._on_click()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_pressed and not self.rect().contains(event.position().toPoint()):
            self._is_pressed = False
            self._restore_timer.stop()
            self._restore_bg()
        super().mouseMoveEvent(event)

    def _apply_bg(self, color: str):
        border_style = (
            "border: 1.5px solid rgba(0,0,0,0.18);"
            if self._border else "border: none;"
        )
        self.setStyleSheet(f"""
            MenuTile {{
                background-color: {color};
                border-radius: {self._radius}px;
                {border_style}
            }}
        """)

    def _restore_bg(self):
        self._apply_bg(self._bg_color)

    # ── layout / style ──────────────────────────────────────
    def apply_scale(self, s: float):
        m = max(round(52 * s), 16)
        self._outer.setContentsMargins(m, 0, m, 0)
        self._outer.setSpacing(0)

        self._icon_svg.setFixedSize(max(round(72 * s), 24), max(round(72 * s), 24))
        self._icon_spacer.setFixedHeight(max(round(32 * s), 8))
        self._mid_spacer.setFixedHeight(max(round(10 * s), 4))

        self._sub.setStyleSheet(f"""
            color: {self._sub_color};
            font-size: {max(round(22 * s), 8)}px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: 300;
            letter-spacing: {max(round(3 * s), 1)}px;
            background: transparent;
            border: none;
        """)

        self._main.setStyleSheet(f"""
            color: {self._main_color};
            font-size: {max(round(60 * s), 16)}px;
            font-family: 'Georgia', 'Times New Roman', serif;
            font-weight: 600;
            letter-spacing: {max(round(2 * s), 1)}px;
            background: transparent;
            border: none;
        """)

        self._radius = round(40 * s)
        self._restore_bg()

    def resizeEvent(self, event):
        super().resizeEvent(event)


# ══════════════════════════════════════════════════════════════
# TitleBar
# ══════════════════════════════════════════════════════════════
class TitleBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C_DARK}; border: none;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._brand = QLabel("MOOSINSA")
        self._brand.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._brand)

    def apply_scale(self, s: float):
        self.setFixedHeight(max(round(130 * s), 40))
        self._brand.setStyleSheet(f"""
            color: {C_BG};
            font-size: {max(round(48 * s), 14)}px;
            font-family: 'Georgia', 'Times New Roman', serif;
            font-weight: 500;
            letter-spacing: {max(round(14 * s), 4)}px;
            background: transparent;
        """)


# ══════════════════════════════════════════════════════════════
# MoosinsaKiosk (홈 화면)
# ══════════════════════════════════════════════════════════════
class MoosinsaKiosk(QWidget):                 # ★ CHANGED: QMainWindow → QWidget ★
    """
    홈 화면.

    콜백 파라미터 (PageManager 가 주입):           ★ CHANGED ★
      on_category    : 카테고리/브랜드 타일 클릭
      on_search      : 검색/추천 타일 클릭
      on_information : 이용안내 타일 클릭
      on_payment     : 구매 타일 클릭

    기존 버전은 콜백 없이 단독 실행만 가능했으나,
    이제 PageManager 를 통해 각 타일에 실제 동작이 연결된다.
    """
    def __init__(                                         # ★ CHANGED ★
        self,
        on_category    = None,
        on_search      = None,
        on_information = None,
        on_payment     = None,
    ):
        super().__init__()

        # 콜백 저장 (None 이면 no-op 람다로 대체)
        _on_category    = on_category    or (lambda: None)
        _on_search      = on_search      or (lambda: None)
        _on_information = on_information or (lambda: None)
        _on_payment     = on_payment     or (lambda: None)

        self.setStyleSheet(f"background-color: {C_BG};")  # ★ CHANGED ★

        self._root = QVBoxLayout(self)             # ★ CHANGED ★
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._titlebar = TitleBar()
        self._root.addWidget(self._titlebar)

        self._body = QWidget()
        self._body.setStyleSheet(f"background-color: {C_BG};")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setAlignment(Qt.AlignCenter)

        self._grid = QGridLayout()
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self._grid.setRowStretch(0, 1)
        self._grid.setRowStretch(1, 1)

        # on_click 콜백을 각 타일에 직접 연결           ★ CHANGED ★
        tile_defs = [
            (0, 0, C_BG,     SVG_GRID,   C_DARK,     "CATEGORY / BRAND",   C_SUB_LIGHT, "카테고리\n/ 브랜드", C_DARK,   True,  _on_category),
            (0, 1, C_DARK,   SVG_SEARCH, C_BG,       "SEARCH / RECOMMEND", C_SUB_DARK,  "검색\n/ 추천",      C_BG,     False, _on_search),
            (1, 0, C_FOREST, SVG_USER,   C_FOREST_T, "GUIDE",              C_SUB_DARK,  "이용\n안내",        C_FOREST_T, False, _on_information),
            (1, 1, C_BG,     SVG_CART,   C_DARK,     "PURCHASE",           C_SUB_LIGHT, "구매",              C_DARK,   True,  _on_payment),
        ]

        self._tiles: list[MenuTile] = []
        for row, col, bg, svg, ic, sub, sc, main, mc, border, cb in tile_defs:
            tile = MenuTile(
                bg_color=bg, icon_svg=svg, icon_color=ic,
                sub_text=sub, sub_color=sc,
                main_text=main, main_color=mc,
                on_click=cb, border=border,
            )
            self._tiles.append(tile)
            self._grid.addWidget(tile, row, col)

        self._body_layout.addLayout(self._grid)
        self._root.addWidget(self._body, stretch=1)

    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._apply_scale()

    def _apply_scale(self):
        w = self.width()
        h = self.height()
        s = min(w / REF_W, h / REF_H)

        self._titlebar.apply_scale(s)

        margin = max(round(70 * s), 12)
        self._body_layout.setContentsMargins(margin, margin, margin, margin)
        self._grid.setSpacing(max(round(30 * s), 8))

        for tile in self._tiles:
            tile.apply_scale(s)


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════
def _server_reachable() -> bool:
    """서버 연결 가능 여부를 동기 방식으로 확인 (시작 시 1회 호출)."""
    import urllib.request
    host = os.environ.get("MOOSINSA_SERVICE_HOST", "localhost")
    port = os.environ.get("MOOSINSA_SERVICE_PORT", "8000")
    try:
        urllib.request.urlopen(f"http://{host}:{port}/health", timeout=3)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    if not _server_reachable():
        dlg = QMessageBox()
        dlg.setWindowTitle("서버 연결 실패")
        dlg.setIcon(QMessageBox.Critical)
        dlg.setText(
            "서버에 연결할 수 없습니다.\n\n"
            "moosinsa_service를 먼저 실행한 후\n"
            "다시 시도해 주세요."
        )
        dlg.setStandardButtons(QMessageBox.Ok)
        dlg.exec()
        sys.exit(1)

    manager = PageManager()                               # ★ CHANGED ★
    manager.start()   # QMainWindow.show() + 홈 페이지 표시  # ★ CHANGED ★

    sys.exit(app.exec())
