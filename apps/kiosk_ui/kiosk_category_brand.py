import sys
import math
import os
import threading
import queue as _queue
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QScrollArea, QGridLayout, QSizePolicy,
    QButtonGroup
)
from PySide6.QtCore import Qt, QSize, QByteArray, QEvent, Slot, QObject, Signal, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtSvgWidgets import QSvgWidget
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Reference resolution ─────────────────────────────────────
REF_W, REF_H = 1080, 1920

# ── Palette ──────────────────────────────────────────────────
C_BG        = "#EDE9E3"
C_DARK      = "#1C1C1C"
C_BROWN     = "#5C4A3A"   # bottom search button
C_BROWN_H   = "#6E5A48"   # hover
C_BORDER    = "#D6D1C9"
C_SUB       = "#999999"
C_PRICE     = "#444444"   # 가격 텍스트 전용 색상 (C_SUB보다 진함)

_SHOE_BASE_URL = "http://{}:{}".format(
    os.environ.get("MOOSINSA_SERVICE_HOST", "localhost"),
    os.environ.get("MOOSINSA_SERVICE_PORT", "8000"),
)

# ── SVG icons ────────────────────────────────────────────────
SVG_HOME = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M4 14L16 4l12 10"/>
  <path d="M6 12v14h7v-7h6v7h7V12"/>
</svg>"""

SVG_SEARCH = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="14" cy="14" r="8"/>
  <line x1="20" y1="20" x2="27" y2="27"/>
</svg>"""

SVG_BACK = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M20 6L8 16l12 10"/>
</svg>"""


def make_svg(tpl: str, color: str, size: int) -> QSvgWidget:
    w = QSvgWidget()
    w.load(QByteArray(tpl.format(color=color).encode()))
    w.setFixedSize(size, size)
    w.setStyleSheet("background: transparent;")
    return w


# ── Mock DB ──────────────────────────────────────────────────
# ── 카테고리 / 브랜드 필터 기본값 (API 응답 전 칩 표시용) ────
# API 데이터가 로드되면 실제 DB의 tags/brand 값으로 교체된다.
# [카테고리/브랜드리스트] 5개 카테고리 확정 + 브랜드는 API 로드 전 placeholder
CAT_FILTERS   = ["스니커즈", "스포츠", "구두", "샌들", "부츠"]
BRAND_FILTERS = ["나이키", "아디다스", "뉴발란스", "퓨마", "리복", "아식스",
                 "살로몬", "스케처스", "컨버스", "반스", "크록스", "버켄스탁"]


def normalize_shoes(raw: list) -> tuple:
    # [카테고리분류] tags 컬럼 "키:값, 키:값" 형식 파싱 → 5개 카테고리 + 브랜드별 분류
    import json as _json

    CATS = ["스니커즈", "스포츠", "구두", "샌들", "부츠"]
    db: dict = {cat: [] for cat in CATS}
    brand_buckets: dict = {}
    brands: list = []

    for row in raw:
        if not row.get("shoe_id"):
            continue
        item = {
            "shoe_id"  : row.get("shoe_id", ""),
            "name"     : row.get("model", ""),
            "price"    : f"₩{int(row.get('price', 0)):,}",
            "brand"    : row.get("brand", ""),
            "sizes"    : row.get("sizes", []),
            "colors"   : row.get("colors", []),
            "tags"     : row.get("tags", ""),
            "image_url": row.get("image_url", ""),
        }

        tags_str   = item["tags"] if isinstance(item["tags"], str) else ""
        model_lower = item["name"].lower()
        tags_lower  = tags_str.lower()

        def _tag_val(key, _tags=tags_str):
            for part in _tags.split(","):
                part = part.strip()
                if part.startswith(key + ":"):
                    return part[len(key) + 1:].strip().lower()
            return ""

        활동 = _tag_val("활동")
        기능 = _tag_val("기능")
        계절 = _tag_val("계절")
        스타일 = _tag_val("스타일")

        if (any(k in tags_lower for k in ["물놀이", "배수성"]) or
                any(k in model_lower for k in ["슬라이드", "샌들", "클로그"])):
            cat = "샌들"
        elif (("발목지지" in 기능 and "겨울" in 계절) or
              ("보온성" in 기능 and "겨울" in 계절) or
              any(k in model_lower for k in ["부츠", "첼시부츠"])):
            cat = "부츠"
        elif ("버클스트랩" in 기능 or "출근/격식" in 활동 or
              any(k in model_lower for k in ["메리제인", "더비"])):
            cat = "구두"
        elif (any(k in 활동 for k in ["러닝", "운동", "트레이닝", "피트니스",
                                       "축구", "풋살", "하이킹", "트레킹", "등산", "트레일"]) or
              "아웃도어" in 스타일):
            cat = "스포츠"
        else:
            cat = "스니커즈"

        db[cat].append(item)

        brand = item["brand"]
        if brand:
            if brand not in brand_buckets:
                brand_buckets[brand] = []
                brands.append(brand)
            brand_buckets[brand].append(item)

    db.update(brand_buckets)
    return db, CATS, (brands or BRAND_FILTERS)



# ── Async image loader ───────────────────────────────────────
class _ImageBridge(QObject):
    # [이미지재클릭수정] Signal(object,...) + QueuedConnection은 PySide6에서 Python 객체 직렬화 실패
    # → 인자 없는 Signal() + Python queue로 대체: Qt 직렬화 불필요, 스레드 안전
    _notify = Signal()

    def __init__(self):
        super().__init__()
        self._q: _queue.Queue = _queue.Queue()
        self._notify.connect(self._drain)   # AutoConnection: 워커 스레드 emit → QueuedConnection

    @Slot()
    def _drain(self):
        while True:
            try:
                label, data, size = self._q.get_nowait()
                _apply_image(label, data, size)
            except _queue.Empty:
                break

    def post(self, label: QLabel, data: bytes, size: int):
        self._q.put((label, data, size))
        self._notify.emit()

_img_bridge: _ImageBridge | None = None

def _get_img_bridge() -> _ImageBridge:
    global _img_bridge
    if _img_bridge is None:
        _img_bridge = _ImageBridge()
    return _img_bridge

_img_cache: dict[str, bytes] = {}   # url → raw bytes 캐시 (프로세스 수명 동안 유지)

def _resolve_image_url(image_url: str) -> str:
    if not image_url:
        return ""
    if image_url.startswith(("http://", "https://")):
        return image_url
    if image_url.startswith("/"):
        return f"{_SHOE_BASE_URL}{image_url}"
    return f"{_SHOE_BASE_URL}/shoes_images/{image_url}"

def _apply_image(label: QLabel, data: bytes, size: int):
    """캐시 데이터 또는 네트워크 응답을 QLabel에 직접 적용 (메인 스레드 전용)."""
    pix = QPixmap()
    pix.loadFromData(QByteArray(data))
    if not pix.isNull():
        pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        # [이미지재클릭수정] setText("")는 setPixmap() 이후 pixmap을 지울 수 있으므로 제거
        label.setPixmap(pix)

def _load_image_async(image_url: str, label: QLabel, size: int):
    url = _resolve_image_url(image_url)
    if not url:
        return
    if url in _img_cache:
        # [이미지재클릭수정] 캐시 히트: QTimer로 다음 이벤트 루프에서 실행 → 카드 완전 구성 후 setPixmap
        data = _img_cache[url]
        QTimer.singleShot(0, lambda: _apply_image(label, data, size))
        return

    # [이미지재클릭수정] 메인 스레드에서 미리 생성 → QObject가 메인 스레드에 소속돼야
    # 워커 스레드 emit 시 AutoConnection이 QueuedConnection으로 동작함
    bridge = _get_img_bridge()

    def _worker():
        try:
            import urllib.request as _ur
            data = _ur.urlopen(url, timeout=5).read()
            _img_cache[url] = data
            bridge.post(label, data, size)
        except Exception as e:
            print(f"[ProductCard] 이미지 로드 실패 ({url}): {e}")

    threading.Thread(target=_worker, daemon=True).start()


# ── Touch scroll area ────────────────────────────────────────
class TouchScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_y    = None
        self._scroll_y0 = None
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.viewport():
            t = event.type()
            if t == QEvent.MouseButtonPress:
                self._drag_y    = event.pos().y()
                self._scroll_y0 = self.verticalScrollBar().value()
                return True
            elif t == QEvent.MouseMove and self._drag_y is not None:
                self.verticalScrollBar().setValue(
                    self._scroll_y0 - (event.pos().y() - self._drag_y))
                return True
            elif t == QEvent.MouseButtonRelease:
                self._drag_y = None
                return True
        return super().eventFilter(obj, event)


# ── Filter chip ───────────────────────────────────────────────
class FilterChip(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        # Expand horizontally so 4 chips fill the full bar width equally
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggled.connect(lambda _: self._restyle())
        self._s = 0.5

    def _restyle(self):
        self.apply_scale(self._s)

    def apply_scale(self, s: float):
        self._s = s
        active = self.isChecked()
        r  = max(round(36 * s), 12)
        fs = max(round(26 * s), 10)
        py = max(round(16 * s), 5)
        if active:
            self.setStyleSheet(f"""QPushButton {{
                background-color: {C_DARK}; color: {C_BG};
                border: 2px solid {C_DARK}; border-radius: {r}px;
                font-size: {fs}px; font-family: 'Helvetica Neue', Arial, sans-serif;
                font-weight: 500; padding: {py}px 0px; }}""")
        else:
            self.setStyleSheet(f"""QPushButton {{
                background-color: transparent; color: {C_DARK};
                border: 1.5px solid {C_BORDER}; border-radius: {r}px;
                font-size: {fs}px; font-family: 'Helvetica Neue', Arial, sans-serif;
                font-weight: 400; padding: {py}px 0px; }}
                QPushButton:hover {{ border: 1.5px solid {C_DARK}; }}""")


# ── Product card ─────────────────────────────────────────────
# [ProductCard] QPushButton → QFrame: styled QPushButton 내 자식 위젯 렌더링 불가 문제 수정
class ProductCard(QFrame):
    def __init__(self, product: dict, s: float, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click
        self._product  = product
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

        layout = QVBoxLayout(self)
        pad = max(round(16 * s), 6)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(max(round(10 * s), 3))
        layout.setAlignment(Qt.AlignTop)

        img_size = max(round(190 * s), 60)
        img_frame = QFrame()
        img_frame.setFixedSize(img_size, img_size)
        img_frame.setStyleSheet(f"""QFrame {{
            background-color: {C_BG};
            border: 1px solid {C_BORDER};
            border-radius: {max(round(10 * s), 4)}px; }}""")
        img_lbl = QLabel("IMG", img_frame)
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setGeometry(0, 0, img_size, img_size)
        img_lbl.setStyleSheet(f"""color: {C_BORDER};
            font-size: {max(round(18 * s), 7)}px;
            font-family: 'Helvetica Neue', Arial;
            background: transparent; border: none;""")
        layout.addWidget(img_frame, alignment=Qt.AlignHCenter)
        _load_image_async(product.get("image_url", ""), img_lbl, img_size)

        name = QLabel(product.get("name", ""))
        name.setWordWrap(True)
        name.setAlignment(Qt.AlignLeft)
        name.setStyleSheet(f"""color: {C_DARK};
            font-size: {max(round(22 * s), 8)}px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: 500; background: transparent; border: none;""")
        layout.addWidget(name)

        price = QLabel(product.get("price", ""))
        price.setAlignment(Qt.AlignLeft)
        price.setStyleSheet(f"""color: {C_PRICE};
            font-size: {max(round(20 * s), 7)}px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: 400; background: transparent; border: none;""")
        layout.addWidget(price)
        layout.addStretch()

        r = max(round(16 * s), 6)
        self.setFixedWidth(max(round(235 * s), 76))
        self.setObjectName("product_card")
        self.setStyleSheet(f"""QFrame#product_card {{
            background-color: {C_BG};
            border: 1px solid {C_BORDER};
            border-radius: {r}px; }}
            QFrame#product_card:hover {{ border: 1.5px solid {C_DARK}; }}""")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click(self._product)
        super().mousePressEvent(event)


# ── Product grid ─────────────────────────────────────────────
class ProductGrid(QWidget):
    def __init__(self, products: list, s: float, on_click, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C_BG};")
        grid = QGridLayout(self)
        pad = max(round(40 * s), 12)
        gap = max(round(18 * s), 6)
        grid.setContentsMargins(pad, pad, pad, pad)
        grid.setSpacing(gap)
        cols = 4
        for i, p in enumerate(products):
            r, c = divmod(i, cols)
            grid.addWidget(ProductCard(p, s, on_click), r, c)
        rem = len(products) % cols
        if rem:
            for c in range(rem, cols):
                sp = QWidget()
                sp.setFixedWidth(max(round(235 * s), 76))
                sp.setStyleSheet("background: transparent;")
                grid.addWidget(sp, len(products) // cols, c)
        # [카드늘어남방지] 마지막 행 이후 빈 행에 stretch → 상품 행 높이가 고정됨
        n_rows = math.ceil(len(products) / cols) if products else 0
        grid.setRowStretch(n_rows, 1)


# ── Top bar ──────────────────────────────────────────────────
class TopBar(QFrame):
    def __init__(self, on_home, on_back, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C_DARK}; border: none;")
        self._lo = QHBoxLayout(self)

        self._home_btn = QPushButton()
        self._home_btn.setCursor(Qt.PointingHandCursor)
        self._home_btn.clicked.connect(on_home)
        self._home_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._home_icon = make_svg(SVG_HOME, C_BG, 28)
        hl = QHBoxLayout(self._home_btn)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self._home_icon, alignment=Qt.AlignCenter)

        self._brand = QLabel("MOOSINSA")
        self._brand.setAlignment(Qt.AlignCenter)

        self._back_btn = QPushButton()
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(on_back)
        self._back_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._back_icon = make_svg(SVG_BACK, C_BG, 28)
        bl = QHBoxLayout(self._back_btn)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self._back_icon, alignment=Qt.AlignCenter)

        self._lo.addWidget(self._home_btn)
        self._lo.addStretch()
        self._lo.addWidget(self._brand)
        self._lo.addStretch()
        self._lo.addWidget(self._back_btn)

    def apply_scale(self, s: float):
        self.setFixedHeight(max(round(130 * s), 44))
        hm = max(round(40 * s), 12)
        self._lo.setContentsMargins(hm, 0, hm, 0)

        # icon_sz = max(round(36 * s), 16)
        icon_sz = max(round(54 * s), 20)
        self._home_icon.setFixedSize(icon_sz, icon_sz)
        btn_sz  = max(round(100 * s), 36)
        self._home_btn.setFixedSize(btn_sz, btn_sz)

        self._brand.setStyleSheet(f"""color: {C_BG};
            font-size: {max(round(44 * s), 14)}px;
            font-family: 'Georgia', serif; font-weight: 500;
            letter-spacing: {max(round(12 * s), 3)}px; background: transparent;""")

        self._back_icon.setFixedSize(icon_sz, icon_sz)
        self._back_btn.setFixedSize(btn_sz, btn_sz)


# ── Tab bar ───────────────────────────────────────────────────
class TabBar(QFrame):
    def __init__(self, labels: list, on_change, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C_BG}; border: none;")
        self._lo = QHBoxLayout(self)
        self._lo.setContentsMargins(0, 0, 0, 0)
        self._lo.setSpacing(0)
        self._on_change = on_change
        self._btns: list[QPushButton] = []
        self._s = 0.5

        for i, lbl in enumerate(labels):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            # Expand equally
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            btn.clicked.connect(lambda _, i=i: self._select(i))
            self._btns.append(btn)
            self._lo.addWidget(btn)

        # Visual init only — no callback yet
        self._btns[0].setChecked(True)
        self._restyle()

    def _select(self, idx: int):
        for i, b in enumerate(self._btns):
            b.setChecked(i == idx)
        self._restyle()
        self._on_change(idx)

    def _restyle(self):
        s  = self._s
        fs = max(round(30 * s), 11)
        h  = max(round(120 * s), 38)   # taller than before
        self.setFixedHeight(h)
        bw = max(round(3 * s), 1)
        for btn in self._btns:
            active = btn.isChecked()
            bb = f"border-bottom: {bw}px solid {C_DARK};" if active else f"border-bottom: {bw}px solid {C_BORDER};"
            btn.setStyleSheet(f"""QPushButton {{
                background-color: {C_BG};
                color: {C_DARK if active else C_SUB};
                border: none; {bb}
                font-size: {fs}px;
                font-family: 'Helvetica Neue', Arial, sans-serif;
                font-weight: {'600' if active else '300'}; }}""")

    def apply_scale(self, s: float):
        self._s = s
        self._restyle()


# ── Bottom nav ────────────────────────────────────────────────
class BottomNav(QFrame):
    def __init__(self, on_search, on_purchase, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background-color: {C_BG}; border: none; }}")
        self._lo = QHBoxLayout(self)
        self._lo.setContentsMargins(0, 0, 0, 0)
        self._lo.setSpacing(0)

        self._search_btn   = QPushButton()
        self._purchase_btn = QPushButton("구매")
        for btn in (self._search_btn, self._purchase_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._search_btn.clicked.connect(on_search)
        self._purchase_btn.clicked.connect(on_purchase)

        self._lo.addWidget(self._search_btn)   # 50%
        self._lo.addWidget(self._purchase_btn) # 50%

    def apply_scale(self, s: float):
        h       = max(round(140 * s), 50)
        fs      = max(round(32 * s), 13)
        icon_sz = max(round(32 * s), 13)
        self.setFixedHeight(h)

        # Rebuild search button interior cleanly
        old = self._search_btn.layout()
        if old:
            while old.count():
                item = old.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
            # 기존 레이아웃을 새 임시 위젯에 이전해 Qt가 안전하게 해제하도록 함
            QWidget().setLayout(old)

        slo = QHBoxLayout(self._search_btn)
        slo.setContentsMargins(0, 0, 0, 0)
        slo.setSpacing(max(round(10 * s), 4))
        icon = make_svg(SVG_SEARCH, C_BG, icon_sz)  # white icon on brown bg
        lbl  = QLabel("검색")
        lbl.setStyleSheet(f"""color: {C_BG};
            font-size: {fs}px; font-family: 'Helvetica Neue', Arial;
            font-weight: 400; background: transparent;""")
        slo.addStretch()
        slo.addWidget(icon)
        slo.addWidget(lbl)
        slo.addStretch()

        # Search: warm dark brown — visible against cream background
        self._search_btn.setStyleSheet(f"""QPushButton {{
            background-color: {C_BROWN}; border: none; }}
            QPushButton:hover {{ background-color: {C_BROWN_H}; }}""")

        # Purchase: near-black
        self._purchase_btn.setStyleSheet(f"""QPushButton {{
            background-color: {C_DARK}; color: {C_BG}; border: none;
            font-size: {fs}px; font-family: 'Georgia', serif;
            font-weight: 500; letter-spacing: {max(round(4*s),1)}px; }}
            QPushButton:hover {{ background-color: #2E2E2E; }}""")


# ── Main window ───────────────────────────────────────────────
class CategoryBrandPage(QWidget):              # ★ CHANGED: QMainWindow → QWidget ★
    def __init__(self, on_home=None, on_search=None, on_purchase=None, on_back=None,
                 on_product=None, api_client=None):
        super().__init__()                         # ★ CHANGED ★

        self._on_home     = on_home     or (lambda: None)
        self._on_search   = on_search   or (lambda: None)
        self._on_purchase = on_purchase or (lambda: None)
        self._on_back     = on_back     or (lambda: None)
        self._on_product  = on_product  or (lambda p: None)   # ★ NEW ★
        self._api         = api_client  # KioskApiClient 인스턴스 (None이면 빈 화면)
        self._api_db: dict = {}         # normalize_shoes()로 채워지는 { 키: [상품, ...] }
        self._cat_keys:   list = list(CAT_FILTERS)    # 카테고리 탭 칩 목록
        self._brand_keys: list = list(BRAND_FILTERS)  # 브랜드 탭 칩 목록

        self._tab        = 0
        self._cur_filter = CAT_FILTERS[0]
        self._s          = 0.5

        self.setStyleSheet(f"background-color: {C_BG};")  # ★ CHANGED ★

        root = QVBoxLayout(self)                   # ★ CHANGED: self가 곧 루트 위젯 ★
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        self._topbar = TopBar(on_home=self._go_home, on_back=self._on_back)
        root.addWidget(self._topbar)

        # Tab bar
        self._tabbar = TabBar(["카테고리", "브랜드"], self._tab_changed)
        root.addWidget(self._tabbar)

        # Filter chip grid — max 4 per row, equal column widths, minimal side margins
        self._chip_bar = QFrame()
        self._chip_bar.setStyleSheet(f"background-color: {C_BG}; border: none;")
        self._chip_lo = QGridLayout(self._chip_bar)   # [칩레이아웃] QHBoxLayout → QGridLayout
        self._chip_lo.setAlignment(Qt.AlignTop)
        root.addWidget(self._chip_bar)

        self._chips: list[FilterChip] = []
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        self._rebuild_chips(CAT_FILTERS)

        # Scroll area
        self._scroll = TouchScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background-color: {C_BG}; }}
            QScrollBar:vertical {{ width: 0px; background: transparent; }}""")
        root.addWidget(self._scroll, stretch=1)

        # Bottom nav
        self._botnav = BottomNav(on_search=self._on_search, on_purchase=self._on_purchase)
        root.addWidget(self._botnav)

        self._load_products(self._cur_filter)

    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()   # PageManager.go() 가 QStackedWidget 전환 처리

    def _tab_changed(self, idx: int):
        self._tab = idx
        filters = self._brand_keys if idx == 1 else self._cat_keys
        self._rebuild_chips(filters)
        self._load_products(filters[0] if filters else "")

    def _rebuild_chips(self, filters: list):
        # [칩레이아웃] 기존 칩 전부 삭제 후 QGridLayout에 재배치 (최대 4열)
        for chip in self._chips:
            self._chip_group.removeButton(chip)
            chip.deleteLater()
        self._chips.clear()
        while self._chip_lo.count():
            item = self._chip_lo.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        COLS = 4
        s    = self._s
        fpad = max(round(24 * s), 8)
        gap  = max(round(10 * s), 4)
        self._chip_lo.setContentsMargins(fpad, max(round(8 * s), 3), fpad, max(round(8 * s), 3))
        self._chip_lo.setHorizontalSpacing(gap)
        self._chip_lo.setVerticalSpacing(gap)
        for col in range(COLS):
            self._chip_lo.setColumnStretch(col, 1)

        for i, lbl in enumerate(filters):
            chip = FilterChip(lbl)
            chip.apply_scale(s)
            self._chip_group.addButton(chip, i)
            row, col = divmod(i, COLS)
            self._chip_lo.addWidget(chip, row, col)
            self._chips.append(chip)
            chip.clicked.connect(lambda _, l=lbl: self._load_products(l))
        if self._chips:
            self._chips[0].setChecked(True)
        self._update_chip_bar_height()

    def showEvent(self, event):
        """페이지가 표시될 때 API에서 상품 목록을 로드한다."""
        super().showEvent(event)
        if self._api is not None and not self._api_db:
            self._fetch_products_from_api()

    def _fetch_products_from_api(self):
        # [상품정보연동] KioskApiClient.fetch_all_shoes() 사용 — 직접 HTTP 요청 제거
        # (기존 코드는 self._api 객체를 URL 문자열로 잘못 취급해 URL이 깨지는 버그 있었음)
        def _on_result(raw):
            if not raw:
                print("[CategoryBrandPage] /find_shoe 응답 없음")
                return
            db, cats, brands = normalize_shoes(raw)
            self._api_db     = db
            self._cat_keys   = cats
            self._brand_keys = brands
            from PySide6.QtCore import QMetaObject, Qt as _Qt
            QMetaObject.invokeMethod(self, "_on_api_loaded", _Qt.QueuedConnection)

        self._api.fetch_all_shoes(callback=_on_result)

    def _load_products(self, key: str):
        self._cur_filter = key
        products = self._api_db.get(key, [])
        grid = ProductGrid(products, self._s, self._product_clicked)
        self._scroll.setWidget(grid)

    @Slot()
    def _on_api_loaded(self):
        """백그라운드 fetch 완료 후 메인 스레드에서 칩·그리드 갱신"""
        filters = self._cat_keys if self._tab == 0 else self._brand_keys
        self._cur_filter = filters[0] if filters else ""
        self._rebuild_chips(filters)
        self._load_products(self._cur_filter)

    def _update_chip_bar_height(self):
        # [칩레이아웃] 행 수에 따라 chip_bar 높이 동적 조정
        s        = self._s
        n_rows   = max(math.ceil(len(self._chips) / 4), 1)
        chip_h   = max(round(68 * s), 24)    # 칩 한 줄 높이 (padding 포함)
        gap      = max(round(10 * s), 4)
        vpad     = max(round(8 * s), 3) * 2  # 상하 여백
        total_h  = chip_h * n_rows + gap * (n_rows - 1) + vpad
        self._chip_bar.setFixedHeight(max(total_h, chip_h + vpad))

    def _product_clicked(self, product: dict):            # ★ CHANGED ★
        self._on_product(product)                             # ★ NEW ★

    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s

        self._topbar.apply_scale(s)
        self._tabbar.apply_scale(s)

        fpad = max(round(24 * s), 8)
        fgap = max(round(10 * s), 4)
        self._chip_lo.setContentsMargins(fpad, max(round(8*s),3), fpad, max(round(8*s),3))
        self._chip_lo.setHorizontalSpacing(fgap)   # [칩레이아웃] QGridLayout spacing
        self._chip_lo.setVerticalSpacing(fgap)
        for chip in self._chips:
            chip.apply_scale(s)
        self._update_chip_bar_height()

        self._botnav.apply_scale(s)
        self._load_products(self._cur_filter)


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = CategoryBrandPage(
        on_home=lambda: print("→ Home"),
        on_search=lambda: print("→ Search"),
        on_purchase=lambda: print("→ Purchase"),
        on_back=lambda: print("→ Back"),
    )
    win.show()
    sys.exit(app.exec())
