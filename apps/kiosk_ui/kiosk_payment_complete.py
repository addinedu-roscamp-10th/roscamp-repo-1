"""
kiosk_payment_complete.py
결제 완료 화면. PaymentCartPage에서 결제 버튼 클릭 시 PageManager가 전환.
"""
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QSizePolicy, QSpacerItem
)
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtSvgWidgets import QSvgWidget

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

SVG_CHECK = """<svg viewBox="0 0 64 64" fill="none"
  stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="32" cy="32" r="30"/>
  <polyline points="18,33 27,43 46,22"/>
</svg>"""


def make_svg(tpl: str, color: str, w: int, h: int = 0) -> QSvgWidget:
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ── Top bar ───────────────────────────────────────────────────
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

        icon_sz = max(round(54 * s), 20)
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


# ── Main window ───────────────────────────────────────────────
class PaymentCompletePage(QWidget):           # ★ CHANGED: QMainWindow → QWidget ★
    def __init__(self, on_home=None, on_back=None):
        super().__init__()

        self._on_home  = on_home  or (lambda: None)
        self._on_back  = on_back  or (lambda: None)
        self._s        = 0.5

        self.setStyleSheet(f"background-color: {C_BG};")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ── Top bar ──
        self._topbar = TopBar(on_home=self._go_home, on_back=self._on_back)
        self._root.addWidget(self._topbar)

        # ── Content area ──
        self._content = QWidget()
        self._content.setStyleSheet(f"background-color: {C_BG};")
        self._content_lo = QVBoxLayout(self._content)
        self._root.addWidget(self._content, stretch=1)

        # Check icon
        self._check_icon = make_svg(SVG_CHECK, C_FOREST, 80)
        self._content_lo.addWidget(self._check_icon, alignment=Qt.AlignHCenter)

        # "결제가 완료되었습니다!" — large, prominent
        self._title = QLabel("결제가 완료되었습니다!")
        self._title.setAlignment(Qt.AlignCenter)
        self._content_lo.addWidget(self._title)

        # Divider
        self._divider = QFrame()
        self._divider.setFrameShape(QFrame.HLine)
        self._divider.setStyleSheet(f"color: {C_BORDER}; background: {C_BORDER};")
        self._content_lo.addWidget(self._divider)

        # Notice block
        self._notice = QLabel(
            "착용 후 구매하지 않으신 상품은\n"
            "반드시 회수 공간에\n"
            "반납하여 주시기 바랍니다.\n"
            "\n"
            "이용해 주셔서 감사드립니다."
        )
        self._notice.setAlignment(Qt.AlignCenter)
        self._notice.setWordWrap(True)
        self._content_lo.addWidget(self._notice)

        self._content_lo.addStretch(1)

        # ── Bottom CTA button ──
        self._home_cta = QPushButton("처음화면으로")
        self._home_cta.setCursor(Qt.PointingHandCursor)
        self._home_cta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._home_cta.clicked.connect(self._go_home)
        self._root.addWidget(self._home_cta)

    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()

    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_content_scale(s)

    def _apply_content_scale(self, s: float):
        hm = max(round(80 * s), 24)
        self._content_lo.setContentsMargins(hm, max(round(100 * s), 32), hm, max(round(60 * s), 20))
        self._content_lo.setSpacing(max(round(48 * s), 16))

        # Check icon
        icon_sz = max(round(120 * s), 40)
        self._check_icon.setFixedSize(icon_sz, icon_sz)

        # Title — largest text on screen
        self._title.setStyleSheet(f"""
            color: {C_DARK};
            font-size: {max(round(64 * s), 22)}px;
            font-family: 'Georgia', serif;
            font-weight: 600;
            letter-spacing: {max(round(2 * s), 1)}px;
            background: transparent;
        """)

        # Divider thickness
        self._divider.setFixedHeight(max(round(2 * s), 1))

        # Notice — secondary, readable but clearly subordinate
        self._notice.setStyleSheet(f"""
            color: {C_DARK};
            font-size: {max(round(34 * s), 12)}px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: 300;
            line-height: 1.7;
            letter-spacing: {max(round(1 * s), 0)}px;
            background: transparent;
        """)

        # CTA button
        btn_h = max(round(160 * s), 54)
        fs_btn = max(round(38 * s), 14)
        self._home_cta.setFixedHeight(btn_h)
        self._home_cta.setStyleSheet(f"""
            QPushButton {{
                background-color: {C_FOREST};
                color: {C_FOREST_T};
                border: none;
                font-size: {fs_btn}px;
                font-family: 'Georgia', serif;
                font-weight: 500;
                letter-spacing: {max(round(6 * s), 2)}px;
            }}
            QPushButton:hover {{
                background-color: #364D3A;
            }}
        """)


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = PaymentCompletePage(
        on_home=lambda: print("→ Home"),
        on_back=lambda: print("→ Back"),
    )
    win.show()
    sys.exit(app.exec())