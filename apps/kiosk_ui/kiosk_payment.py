import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QScrollArea, QSizePolicy, QGridLayout,
    QMessageBox                                        # ★ NEW ★
)
from PySide6.QtCore import Qt, QByteArray, QEvent
from PySide6.QtSvgWidgets import QSvgWidget

# ── Reference resolution ─────────────────────────────────────
REF_W, REF_H = 1080, 1920

# ── Palette ──────────────────────────────────────────────────
C_BG     = "#EDE9E3"
C_DARK   = "#1C1C1C"
C_BROWN  = "#5C4A3A"
C_BROWN_H= "#6E5A48"
C_BORDER = "#D6D1C9"
C_SUB    = "#999999"
C_RED    = "#C0392B"
C_RED_H  = "#A93226"

# ── SVG icons ────────────────────────────────────────────────
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
</svg>"""

SVG_BARCODE = """<svg viewBox="0 0 48 32" fill="none"
  xmlns="http://www.w3.org/2000/svg">
  <rect x="2"  y="2" width="3"  height="28" fill="{color}"/>
  <rect x="7"  y="2" width="1.5" height="28" fill="{color}"/>
  <rect x="10" y="2" width="3"  height="28" fill="{color}"/>
  <rect x="15" y="2" width="1.5" height="28" fill="{color}"/>
  <rect x="18" y="2" width="4"  height="28" fill="{color}"/>
  <rect x="24" y="2" width="1.5" height="28" fill="{color}"/>
  <rect x="27" y="2" width="3"  height="28" fill="{color}"/>
  <rect x="32" y="2" width="1.5" height="28" fill="{color}"/>
  <rect x="35" y="2" width="4"  height="28" fill="{color}"/>
  <rect x="41" y="2" width="1.5" height="28" fill="{color}"/>
  <rect x="44" y="2" width="2"  height="28" fill="{color}"/>
</svg>"""

SVG_TRASH = """<svg viewBox="0 0 24 24" fill="none"
  stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <polyline points="3 6 5 6 21 6"/>
  <path d="M19 6l-1 14H6L5 6"/>
  <path d="M10 11v6M14 11v6"/>
  <path d="M9 6V4h6v2"/>
</svg>"""


def make_svg(tpl: str, color: str, w: int, h: int = 0) -> QSvgWidget:
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ── Mock cart data ────────────────────────────────────────────
MOCK_CART = [
    {"name": "에어포스 1 (270mm)", "price": 129000},
    {"name": "울트라부스트 22 (265mm)", "price": 219000},
]


# ── Touch scroll ─────────────────────────────────────────────
class TouchScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_y = None
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


# ── Top bar (identical to other pages) ───────────────────────
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

        icon_sz = max(round(54 * s), 20)   # enlarged home icon
        self._home_icon.setFixedSize(icon_sz, icon_sz)
        btn_sz = max(round(100 * s), 36)
        self._home_btn.setFixedSize(btn_sz, btn_sz)

        self._brand.setStyleSheet(f"""color: {C_BG};
            font-size: {max(round(44 * s), 14)}px;
            font-family: 'Georgia', serif; font-weight: 500;
            letter-spacing: {max(round(12 * s), 3)}px; background: transparent;""")

        bsz = max(round(100 * s), 36)
        self._back_btn.setFixedSize(bsz, bsz)
        bisz = max(round(44 * s), 18)
        self._back_icon.setFixedSize(bisz, bisz)


# ── Scan prompt banner ────────────────────────────────────────
class ScanBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""QFrame {{
            background-color: {C_BG};
            border-bottom: 1px solid {C_BORDER};
        }}""")
        self._lo = QHBoxLayout(self)

        # Barcode icon
        self._icon_container = QWidget()
        self._icon_container.setStyleSheet("background: transparent;")
        self._icon_lo = QVBoxLayout(self._icon_container)
        self._icon_lo.setContentsMargins(0, 0, 0, 0)
        self._barcode = make_svg(SVG_BARCODE, C_DARK, 80, 52)
        self._icon_lo.addWidget(self._barcode, alignment=Qt.AlignCenter)

        # Text
        self._text = QLabel("신발 박스의 바코드를 스캔해 주세요.")
        self._text.setWordWrap(False)   # controlled via font size
        self._text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._lo.addWidget(self._icon_container)
        self._lo.addSpacing(10)
        self._lo.addWidget(self._text, stretch=1)

    def apply_scale(self, s: float):
        h = max(round(220 * s), 72)
        self.setFixedHeight(h)
        hm = max(round(50 * s), 16)
        self._lo.setContentsMargins(hm, 0, hm, 0)
        self._lo.setSpacing(max(round(28 * s), 8))

        bw = max(round(80 * s), 30)
        bh = max(round(52 * s), 20)
        self._barcode.setFixedSize(bw, bh)

        fs = max(round(36 * s), 13)
        self._text.setStyleSheet(f"""color: {C_DARK};
            font-size: {fs}px;
            font-family: 'Georgia', serif;
            font-weight: 400;
            background: transparent;""")


# ── Cart item row ─────────────────────────────────────────────
class CartItemRow(QFrame):
    def __init__(self, item: dict, on_remove, parent=None):
        super().__init__(parent)
        self._item = item
        self._on_remove = on_remove
        self.setStyleSheet(f"""QFrame {{
            background-color: {C_BG};
            border-bottom: 1px dashed {C_BORDER};
        }}""")
        self._lo = QHBoxLayout(self)

        self._name_lbl  = QLabel(item["name"])
        self._price_lbl = QLabel(f"₩{item['price']:,}")
        self._del_btn   = QPushButton()
        self._del_btn.setCursor(Qt.PointingHandCursor)
        self._del_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            f"QPushButton:hover {{ background-color: #F0EBE4; border-radius: 4px; }}")
        self._del_btn.clicked.connect(lambda: on_remove(item))
        self._trash_icon = make_svg(SVG_TRASH, C_SUB, 22)
        dlo = QHBoxLayout(self._del_btn)
        dlo.setContentsMargins(0, 0, 0, 0)
        dlo.addWidget(self._trash_icon, alignment=Qt.AlignCenter)

        self._lo.addWidget(self._name_lbl, stretch=1)
        self._lo.addWidget(self._price_lbl)
        self._lo.addSpacing(8)
        self._lo.addWidget(self._del_btn)

    def apply_scale(self, s: float):
        h = max(round(110 * s), 38)
        self.setFixedHeight(h)
        hm = max(round(50 * s), 16)
        vm = max(round(10 * s), 4)
        self._lo.setContentsMargins(hm, vm, hm, vm)
        self._lo.setSpacing(max(round(16 * s), 6))

        fs_name  = max(round(28 * s), 10)
        fs_price = max(round(26 * s), 10)
        icon_sz  = max(round(36 * s), 14)

        self._name_lbl.setStyleSheet(f"""color: {C_DARK};
            font-size: {fs_name}px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: 400; background: transparent;""")
        self._price_lbl.setStyleSheet(f"""color: {C_SUB};
            font-size: {fs_price}px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: 300; background: transparent;""")
        self._trash_icon.setFixedSize(icon_sz, icon_sz)
        self._del_btn.setFixedSize(icon_sz + max(round(16*s),6),
                                   icon_sz + max(round(16*s),6))


# ── Cart list widget ──────────────────────────────────────────
class CartList(QWidget):
    def __init__(self, items: list, on_remove, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C_BG};")
        self._lo = QVBoxLayout(self)
        self._lo.setContentsMargins(0, 0, 0, 0)
        self._lo.setSpacing(0)
        self._rows: list[CartItemRow] = []
        for item in items:
            row = CartItemRow(item, on_remove)
            self._rows.append(row)
            self._lo.addWidget(row)
        self._lo.addStretch()

    def apply_scale(self, s: float):
        for row in self._rows:
            row.apply_scale(s)


# ── Summary footer ────────────────────────────────────────────
class SummaryFooter(QFrame):
    """Bottom area: quantity/total info + cancel + pay buttons."""

    def __init__(self, cart: list, on_cancel, on_pay, parent=None):
        super().__init__(parent)
        self._cart     = cart
        self._on_cancel = on_cancel
        self._on_pay    = on_pay
        self.setStyleSheet(f"""QFrame {{
            background-color: {C_BG};
            border-top: 1.5px solid {C_BORDER};
        }}""")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Info row (수량 / 합계금액) ──
        self._info_frame = QFrame()
        self._info_frame.setStyleSheet(
            f"background-color: {C_BG}; border: none;"
            f"border-bottom: 1px solid {C_BORDER};")
        self._info_lo = QVBoxLayout(self._info_frame)

        self._qty_lbl   = QLabel()
        self._total_lbl = QLabel()
        for lbl in (self._qty_lbl, self._total_lbl):
            lbl.setStyleSheet(f"background: transparent;")
        self._info_lo.addWidget(self._qty_lbl)
        self._info_lo.addWidget(self._total_lbl)
        outer.addWidget(self._info_frame)

        # ── Button row (전체취소 / 결제) ──
        self._btn_frame = QFrame()
        self._btn_frame.setStyleSheet(f"background-color: {C_BG}; border: none;")
        self._btn_lo = QHBoxLayout(self._btn_frame)
        self._btn_lo.setContentsMargins(0, 0, 0, 0)
        self._btn_lo.setSpacing(0)

        self._cancel_btn = QPushButton("전체취소")
        self._pay_btn    = QPushButton("결제")
        for btn in (self._cancel_btn, self._pay_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._cancel_btn.clicked.connect(on_cancel)
        self._pay_btn.clicked.connect(on_pay)

        self._btn_lo.addWidget(self._cancel_btn)   # 50 %
        self._btn_lo.addWidget(self._pay_btn)       # 50 %
        outer.addWidget(self._btn_frame, stretch=1)

        self._update_info()

    def _update_info(self):
        qty   = len(self._cart)
        total = sum(i["price"] for i in self._cart)
        self._qty_lbl.setText(f"수량  {qty}개")
        self._total_lbl.setText(f"합계금액  ₩{total:,}")

    def refresh(self, cart: list):
        self._cart = cart
        self._update_info()

    def apply_scale(self, s: float):
        total_h  = max(round(420 * s), 140)
        info_h   = max(round(260 * s), 88)
        self.setFixedHeight(total_h)
        self._info_frame.setFixedHeight(info_h)

        hm = max(round(50 * s), 16)
        vm = max(round(40 * s), 14)
        self._info_lo.setContentsMargins(hm, vm, hm, vm)
        self._info_lo.setSpacing(max(round(32 * s), 10))

        fs_info = max(round(40 * s), 14)
        for lbl in (self._qty_lbl, self._total_lbl):
            lbl.setStyleSheet(f"""color: {C_DARK};
                font-size: {fs_info}px;
                font-family: 'Helvetica Neue', Arial, sans-serif;
                font-weight: 400; background: transparent;""")

        fs_btn = max(round(32 * s), 12)
        self._cancel_btn.setStyleSheet(f"""QPushButton {{
            background-color: {C_BROWN}; color: {C_BG}; border: none;
            font-size: {fs_btn}px; font-family: 'Helvetica Neue', Arial;
            font-weight: 400; }}
            QPushButton:hover {{ background-color: {C_BROWN_H}; }}""")
        self._pay_btn.setStyleSheet(f"""QPushButton {{
            background-color: {C_DARK}; color: {C_BG}; border: none;
            font-size: {fs_btn}px; font-family: 'Georgia', serif;
            font-weight: 500; letter-spacing: {max(round(4*s),1)}px; }}
            QPushButton:hover {{ background-color: #2E2E2E; }}""")


# ── Main window ───────────────────────────────────────────────
class PaymentCartPage(QWidget):               # ★ CHANGED: QMainWindow → QWidget ★
    def __init__(self, on_home=None, on_back=None, on_pay=None):
        super().__init__()                         # ★ CHANGED ★

        self._on_home  = on_home  or (lambda: None)
        self._on_back  = on_back  or (lambda: None)
        self._on_pay   = on_pay   or (lambda: None)
        self._cart     = list(MOCK_CART)
        self._s        = 0.5

        self.setStyleSheet(f"background-color: {C_BG};")  # ★ CHANGED ★

        root = QVBoxLayout(self)                   # ★ CHANGED ★
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        self._topbar = TopBar(on_home=self._go_home, on_back=self._on_back)
        root.addWidget(self._topbar)

        # Scan banner
        self._banner = ScanBanner()
        root.addWidget(self._banner)

        # Scroll area for cart items
        self._scroll = TouchScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background-color: {C_BG}; }}
            QScrollBar:vertical {{ width: 0px; background: transparent; }}""")
        root.addWidget(self._scroll, stretch=1)

        # Summary footer
        self._footer = SummaryFooter(
            cart=self._cart,
            on_cancel=self._cancel_all,
            on_pay=self._do_pay,
        )
        root.addWidget(self._footer)

        self._rebuild_cart()

    # ── actions ──
    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()

    def _remove_item(self, item: dict):
        if item in self._cart:
            self._cart.remove(item)
        self._rebuild_cart()
        self._footer.refresh(self._cart)

    def _cancel_all(self):
        self._cart.clear()
        self._rebuild_cart()
        self._footer.refresh(self._cart)

    def _do_pay(self):                                    # ★ CHANGED ★
        if not self._cart:                                 # ★ NEW ★
            msg = QMessageBox(self)                        # ★ NEW ★
            msg.setWindowTitle("추가 상품 없음")             # ★ NEW ★
            msg.setText("추가된 상품이 없습니다.\n상품을 추가한 후 결제해 주세요.")  # ★ NEW ★
            msg.setIcon(QMessageBox.Icon.Warning)          # ★ NEW ★
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)  # ★ NEW ★
            # 키오스크 터치 환경에 맞게 폰트 크기 키우기       # ★ NEW ★
            fs = max(round(28 * self._s), 14)              # ★ NEW ★
            msg.setStyleSheet(f"QLabel {{ font-size: {fs}px; color: {C_DARK};}}"
                              f"QPushButton {{ font-size: {fs}px; color: {C_DARK}; "
                              f"min-width: {max(round(160*self._s),80)}px; "
                              f"min-height: {max(round(60*self._s),32)}px; }}")  # ★ NEW ★
            msg.exec()                                     # ★ NEW ★
            return                                         # ★ NEW ★
        self._on_pay()
        print(f"[결제] {len(self._cart)}개 ₩{sum(i['price'] for i in self._cart):,}")

    def _rebuild_cart(self):
        self._cart_widget = CartList(self._cart, self._remove_item)
        self._cart_widget.apply_scale(self._s)
        self._scroll.setWidget(self._cart_widget)

    # ── resize ──
    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._banner.apply_scale(s)
        self._footer.apply_scale(s)
        if hasattr(self, '_cart_widget'):
            self._cart_widget.apply_scale(s)


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = PaymentCartPage(
        on_home=lambda: print("→ Home"),
        on_back=lambda: print("→ Back"),
        on_pay=lambda: print("→ Pay"),
    )
    win.show()
    sys.exit(app.exec())