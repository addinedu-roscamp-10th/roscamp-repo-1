"""
kiosk_search_result.py
- 검색 결과를 연관도 순으로 세로 나열
- 터치 스크롤
- 하단 '다시 검색하기' 버튼 → kiosk_search 복귀
"""
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, QByteArray, QEvent
from PySide6.QtSvgWidgets import QSvgWidget
from kiosk_category_brand import _load_image_async  # [ProductRow이미지]

# ── Reference resolution ─────────────────────────────────────
REF_W, REF_H = 1080, 1920

# ── Palette ──────────────────────────────────────────────────
C_BG      = "#EDE9E3"
C_DARK    = "#1C1C1C"
C_FOREST  = "#2C3D30"
C_FOREST_T= "#C8DDB8"
C_BROWN   = "#5C4A3A"
C_BROWN_H = "#6E5A48"
C_BORDER  = "#D6D1C9"
C_SUB     = "#999999"

# ── SVG ──────────────────────────────────────────────────────
SVG_HOME = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M4 14L16 4l12 10"/>
  <path d="M6 12v14h7v-7h6v7h7V12"/>
</svg>"""

SVG_BACK = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M20 6L8 16l12 10"/>
</svg>"""  # ★ NEW ★

SVG_SEARCH_AGAIN = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="14" cy="14" r="8"/>
  <line x1="20" y1="20" x2="27" y2="27"/>
</svg>"""

SVG_ARROW_RIGHT = """<svg viewBox="0 0 20 20" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M5 10h10M11 6l4 4-4 4"/>
</svg>"""


def make_svg(tpl: str, color: str, w: int, h: int = 0) -> QSvgWidget:
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ── Mock search results (연관도 순) ──────────────────────────
# 실제 구현 시 query를 받아 DB에서 조회한 결과로 교체
MOCK_RESULTS = [
    {"rank": 1, "name": "에어포스 1 '07",      "brand": "나이키",   "price": 129000, "tag": "검색어 일치"},
    {"rank": 2, "name": "에어맥스 270",         "brand": "나이키",   "price": 189000, "tag": "브랜드 일치"},
    {"rank": 3, "name": "에어 모나크 IV",       "brand": "나이키",   "price": 109000, "tag": "브랜드 일치"},
    {"rank": 4, "name": "에어 줌 페가수스 40",  "brand": "나이키",   "price": 139000, "tag": "스타일 유사"},
]
# ↑ 이 리스트의 항목 수를 바꾸기만 하면 표시 상품 수가 바뀜


# ── Touch scroll ─────────────────────────────────────────────
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


# ── Product row card (세로 나열용) ────────────────────────────
# [ProductRow렌더링] QPushButton → QFrame: styled QPushButton 내 자식 위젯 렌더링 불가 문제 수정
class ProductRow(QFrame):
    """
    가로로 긴 카드: [순위] [이미지] [이름 + 브랜드 + 태그] [가격] [>]
    """
    def __init__(self, product: dict, s: float, on_click, parent=None):
        super().__init__(parent)
        self._product  = product
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._build(product, s)

    def mousePressEvent(self, event):  # [ProductRow렌더링]
        if event.button() == Qt.LeftButton:
            self._on_click(self._product)
        super().mousePressEvent(event)

    def _build(self, p: dict, s: float):
        outer = QHBoxLayout(self)
        pad_h = max(round(40 * s), 14)
        pad_v = max(round(24 * s), 8)
        outer.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        outer.setSpacing(max(round(28 * s), 10))

        # ── 순위 번호 ──
        rank_lbl = QLabel(str(p["rank"]))
        rank_lbl.setFixedWidth(max(round(40 * s), 14))
        rank_lbl.setAlignment(Qt.AlignCenter)
        rank_lbl.setStyleSheet(
            f"color:{C_SUB}; font-size:{max(round(24*s),9)}px;"
            f"font-family:'Georgia',serif; font-weight:400; background:transparent;")
        outer.addWidget(rank_lbl, alignment=Qt.AlignVCenter)

        # ── 이미지 placeholder ──
        img_sz = max(round(160 * s), 54)
        img_frame = QFrame()
        img_frame.setFixedSize(img_sz, img_sz)
        img_frame.setStyleSheet(
            f"QFrame{{background:{C_BG}; border:1px solid {C_BORDER};"
            f"border-radius:{max(round(12*s),4)}px;}}")
        img_lbl = QLabel("IMG", img_frame)
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setGeometry(0, 0, img_sz, img_sz)
        img_lbl.setStyleSheet(
            f"color:{C_BORDER}; font-size:{max(round(16*s),6)}px;"
            f"font-family:'Helvetica Neue',Arial; background:transparent; border:none;")
        _load_image_async(p.get("image_url", ""), img_lbl, img_sz)  # [ProductRow이미지]
        outer.addWidget(img_frame, alignment=Qt.AlignVCenter)

        # ── 텍스트 블록 ──
        text_block = QWidget()
        text_block.setStyleSheet("background:transparent;")
        text_lo = QVBoxLayout(text_block)
        text_lo.setContentsMargins(0, 0, 0, 0)
        text_lo.setSpacing(max(round(6 * s), 2))

        # 태그 (검색어 일치 등)
        tag_lbl = QLabel(p.get("tag", ""))
        tag_lbl.setStyleSheet(
            f"color:{C_FOREST}; font-size:{max(round(20*s),7)}px;"
            f"font-family:'Helvetica Neue',Arial; font-weight:400;"
            f"background:transparent;")
        text_lo.addWidget(tag_lbl)

        # 상품명
        name_lbl = QLabel(p["name"])
        name_lbl.setWordWrap(True)
        name_lbl.setStyleSheet(
            f"color:{C_DARK}; font-size:{max(round(30*s),11)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:500; background:transparent;")
        text_lo.addWidget(name_lbl)

        # 브랜드
        brand_lbl = QLabel(p["brand"])
        brand_lbl.setStyleSheet(
            f"color:{C_SUB}; font-size:{max(round(22*s),8)}px;"
            f"font-family:'Helvetica Neue',Arial; font-weight:300;"
            f"background:transparent;")
        text_lo.addWidget(brand_lbl)

        outer.addWidget(text_block, stretch=1, alignment=Qt.AlignVCenter)

        # ── 가격 ──
        price_lbl = QLabel(f"₩{p['price']:,}")
        price_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        price_lbl.setStyleSheet(
            f"color:{C_DARK}; font-size:{max(round(26*s),9)}px;"
            f"font-family:'Helvetica Neue',Arial; font-weight:500;"
            f"background:transparent;")
        outer.addWidget(price_lbl, alignment=Qt.AlignVCenter)

        # ── 화살표 ──
        arrow = make_svg(SVG_ARROW_RIGHT, C_BORDER, max(round(28*s),10))
        outer.addWidget(arrow, alignment=Qt.AlignVCenter)

        # Card style  # [ProductRow렌더링]
        self.setObjectName("product_row")
        self.setStyleSheet(
            f"QFrame#product_row{{background:{C_BG}; border:none;"
            f"border-bottom:1px solid {C_BORDER};}}"
            f"QFrame#product_row:hover{{background:#E8E3DC;}}")


# ── Result list widget ────────────────────────────────────────
class ResultList(QWidget):
    def __init__(self, results: list, s: float, on_click, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_BG};")
        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        for item in results:
            lo.addWidget(ProductRow(item, s, on_click))
        lo.addStretch()


# ── Top bar ───────────────────────────────────────────────────
class TopBar(QFrame):
    def __init__(self, on_home, on_back, parent=None):  # ★ CHANGED ★
        super().__init__(parent)
        self.setStyleSheet(f"background-color:{C_DARK}; border:none;")
        self._lo = QHBoxLayout(self)
        self._home_btn = QPushButton()
        self._home_btn.setCursor(Qt.PointingHandCursor)
        self._home_btn.clicked.connect(on_home)
        self._home_btn.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self._home_icon = make_svg(SVG_HOME, C_BG, 28)
        hl = QHBoxLayout(self._home_btn)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self._home_icon, alignment=Qt.AlignCenter)
        self._brand = QLabel("MOOSINSA")
        self._brand.setAlignment(Qt.AlignCenter)
        self._back_btn = QPushButton()                     # ★ CHANGED ★
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(on_back)
        self._back_btn.setStyleSheet("QPushButton{background:transparent;border:none;}")
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
        icon_sz = max(round(54 * s), 20)
        self._home_icon.setFixedSize(icon_sz, icon_sz)
        btn_sz = max(round(100 * s), 36)
        self._home_btn.setFixedSize(btn_sz, btn_sz)
        self._brand.setStyleSheet(
            f"color:{C_BG}; font-size:{max(round(44*s),14)}px;"
            f"font-family:'Georgia',serif; font-weight:500;"
            f"letter-spacing:{max(round(12*s),3)}px; background:transparent;")
        r=max(round(32*s),10); fs=max(round(22*s),8)
        px=max(round(28*s),8); py=max(round(10*s),3)
        self._back_btn.setStyleSheet(
            f"QPushButton{{background:transparent; color:{C_BG};"
            f"border:1.5px solid rgba(255,255,255,0.4); border-radius:{r}px;"
            f"font-size:{fs}px; font-family:'Helvetica Neue',Arial;"
            f"font-weight:300; padding:{py}px {px}px;}}"
            f"QPushButton:hover{{border:1.5px solid {C_BG};}}")


# ── Main window ───────────────────────────────────────────────
class SearchResultPage(QWidget):              # ★ CHANGED: QMainWindow → QWidget ★
    def __init__(
        self,
        query: str = "",
        results: list = None,
        on_home=None,
        on_back=None,   # ★ CHANGED ★
        on_retry_search=None,
        on_product_click=None,
    ):
        super().__init__()                         # ★ CHANGED ★

        self._query      = query
        self._results    = results  # None=로딩 중, []=결과 없음, list=결과
        self._on_home    = on_home          or (lambda: None)
        self._on_back    = on_back          or (lambda: None)  # ★ CHANGED ★
        self._on_retry   = on_retry_search  or (lambda: None)
        self._on_product = on_product_click or (lambda p: print(f"상품: {p['name']}"))
        self._s          = 0.5

        self.setStyleSheet(f"background:{C_BG};")  # ★ CHANGED ★
        self._root = QVBoxLayout(self)             # ★ CHANGED ★
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ── Top bar ──
        self._topbar = TopBar(on_home=self._go_home, on_back=self._on_back)  # ★ CHANGED ★
        self._root.addWidget(self._topbar)

        # ── 안내 문구 ──
        self._info_frame = QFrame()
        self._info_frame.setStyleSheet(
            f"QFrame{{background:{C_BG}; border-bottom:1px solid {C_BORDER};}}")
        self._info_lo = QVBoxLayout(self._info_frame)

        self._query_lbl = QLabel()
        self._query_lbl.setAlignment(Qt.AlignLeft)

        self._desc_lbl = QLabel("검색어와 연관성이 높은 순으로 상품을 표시합니다.\n상품을 선택하거나 다시 검색해 주세요.")
        self._desc_lbl.setAlignment(Qt.AlignLeft)
        self._desc_lbl.setWordWrap(True)

        self._info_lo.addWidget(self._query_lbl)
        self._info_lo.addWidget(self._desc_lbl)
        self._root.addWidget(self._info_frame)

        # ── 스크롤 결과 영역 ──
        self._scroll = TouchScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:none; background:{C_BG};}}"
            f"QScrollBar:vertical{{width:0px; background:transparent;}}")
        self._root.addWidget(self._scroll, stretch=1)

        self._load_results()

        # ── 하단 '다시 검색하기' 버튼 ──
        self._retry_btn = QPushButton()
        self._retry_btn.setCursor(Qt.PointingHandCursor)
        self._retry_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._retry_btn.clicked.connect(self._retry_search)

        # 버튼 내부: 돋보기 아이콘 + 텍스트
        self._retry_icon = make_svg(SVG_SEARCH_AGAIN, C_BG, 28)
        self._retry_lbl  = QLabel("다시 검색하기")
        retry_lo = QHBoxLayout(self._retry_btn)
        retry_lo.setContentsMargins(0, 0, 0, 0)
        retry_lo.setSpacing(max(round(12 * self._s), 4))
        retry_lo.addStretch()
        retry_lo.addWidget(self._retry_icon)
        retry_lo.addWidget(self._retry_lbl)
        retry_lo.addStretch()
        self._retry_lo = retry_lo

        self._root.addWidget(self._retry_btn)

    def update_results(self, query: str, results):  # [검색로딩] list | None
        """
        PageManager가 검색 실행 후 결과를 주입할 때 호출.
        query/results를 갱신하고 화면을 다시 그린다.
        """
        self._query   = query
        self._results = results
        self._load_results()
        self._apply_scale(self._s)

    # ── actions ──────────────────────────────────────────────
    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()

    def _retry_search(self):                              # ★ CHANGED ★
        self._on_retry()

    def _make_msg_widget(self, msg: str) -> QWidget:  # [검색로딩]
        w = QWidget()
        w.setStyleSheet(f"background:{C_BG};")
        lo = QVBoxLayout(w)
        lo.setAlignment(Qt.AlignCenter)
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color:{C_SUB}; font-size:{max(round(32*self._s),12)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300; background:transparent;")
        lo.addWidget(lbl)
        return w

    def _load_results(self):  # [검색로딩]
        if self._results is None:
            widget = self._make_msg_widget("검색 중...")
        elif not self._results:
            widget = self._make_msg_widget("검색 결과가 없습니다.")
        else:
            widget = ResultList(self._results, self._s, self._on_product)
        self._scroll.setWidget(widget)

    # ── resize ───────────────────────────────────────────────
    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_scale(s)
        self._load_results()

    def _apply_scale(self, s: float):
        # 안내 영역
        hm = max(round(80 * s), 20)
        vm = max(round(50 * s), 20)
        self._info_lo.setContentsMargins(hm, vm, hm, vm)
        self._info_lo.setSpacing(max(round(40 * s), 20))

        # 검색어 표시
        q_text = f"'{self._query}' 검색 결과" if self._query else "검색 결과"
        self._query_lbl.setText(q_text)
        self._query_lbl.setStyleSheet(
            f"color:{C_DARK}; font-size:{max(round(42*s),21)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:600; background:transparent;")

        # [검색로딩] 상태별 안내 문구
        if self._results is None:
            desc_text = "잠시만 기다려 주세요."
        elif not self._results:
            desc_text = "다른 검색어로 다시 시도해 보세요."
        else:
            desc_text = "검색어와 연관성이 높은 순으로 상품을 표시합니다.\n상품을 선택하거나 다시 검색해 주세요."
        self._desc_lbl.setText(desc_text)
        self._desc_lbl.setStyleSheet(
            f"color:{C_SUB}; font-size:{max(round(34*s),19)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300; background:transparent;")

        # 하단 버튼
        btn_h  = max(round(140 * s), 48)
        fs_btn = max(round(30 * s), 11)
        icon_sz = max(round(30 * s), 12)
        self._retry_btn.setFixedHeight(btn_h)
        self._retry_icon.setFixedSize(icon_sz, icon_sz)
        self._retry_lbl.setStyleSheet(
            f"color:{C_BG}; font-size:{fs_btn}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:400; background:transparent;")
        self._retry_lo.setSpacing(max(round(12 * s), 4))
        self._retry_btn.setStyleSheet(
            f"QPushButton{{background:{C_BROWN}; border:none;}}"
            f"QPushButton:hover{{background:{C_BROWN_H};}}")


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = SearchResultPage(
        query="나이키",
        results=MOCK_RESULTS,   # 항목 수 조절 시 이 리스트만 수정
        on_home=lambda: print("→ Home"),
        on_back=lambda: print("→ Back"),     # ★ CHANGED ★
        on_retry_search=lambda: print("→ 다시 검색"),
        on_product_click=lambda p: print(f"→ 상품: {p['name']}"),
    )
    win.show()
    sys.exit(app.exec())
