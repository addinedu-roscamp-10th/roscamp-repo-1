import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtSvgWidgets import QSvgWidget

REF_W, REF_H = 1080, 1920

C_BG       = "#EDE9E3"
C_DARK     = "#1C1C1C"
C_FOREST   = "#2C3D30"
C_FOREST_T = "#C8DDB8"
C_BORDER   = "#D6D1C9"
C_SUB      = "#999999"

SVG_HOME = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M4 14L16 4l12 10"/>
  <path d="M6 12v14h7v-7h6v7h7V12"/>
</svg>"""

SVG_CLOSE = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round"
  xmlns="http://www.w3.org/2000/svg">
  <line x1="8" y1="8" x2="24" y2="24"/>
  <line x1="24" y1="8" x2="8" y2="24"/>
</svg>"""


def make_svg(tpl, color, w, h=0):
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background:transparent;")
    return wgt


# ── TopBar ────────────────────────────────────────────────────
class TopBar(QFrame):
    def __init__(self, on_home, on_close, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_DARK};border:none;")
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

        self._close_btn = QPushButton()
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(on_close)
        self._close_btn.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self._close_icon = make_svg(SVG_CLOSE, C_BG, 24)
        cl = QHBoxLayout(self._close_btn)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._close_icon, alignment=Qt.AlignCenter)

        self._lo.addWidget(self._home_btn)
        self._lo.addStretch()
        self._lo.addWidget(self._brand)
        self._lo.addStretch()
        self._lo.addWidget(self._close_btn)

    def apply_scale(self, s):
        self.setFixedHeight(max(round(130*s), 44))
        hm = max(round(40*s), 12)
        self._lo.setContentsMargins(hm, 0, hm, 0)
        isz = max(round(54*s), 20); self._home_icon.setFixedSize(isz, isz)
        bsz = max(round(100*s), 36); self._home_btn.setFixedSize(bsz, bsz)
        csz = max(round(40*s), 16);  self._close_icon.setFixedSize(csz, csz)
        self._close_btn.setFixedSize(bsz, bsz)
        self._brand.setStyleSheet(
            f"color:{C_BG};font-size:{max(round(44*s),14)}px;"
            f"font-family:'Georgia',serif;font-weight:500;"
            f"letter-spacing:{max(round(12*s),3)}px;background:transparent;")


# ── SectionTitle ──────────────────────────────────────────────
class SectionTitle(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)

    def apply_scale(self, s):
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(44*s),15)}px;"
            f"font-family:'Georgia',serif;font-weight:600;background:transparent;")


# ── StepRow ───────────────────────────────────────────────────
class StepRow(QLabel):
    """번호+텍스트를 rich text로 한 QLabel에 담아 완전한 가운데 정렬."""
    def __init__(self, number: str, text: str, parent=None):
        super().__init__(parent)
        self._number = number
        self._text   = text
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:transparent;")

    def apply_scale(self, s):
        fs = max(round(34*s), 12)
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{fs}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;")
        self.setText(
            f'<span style="color:{C_FOREST};font-family:Georgia,serif;font-weight:600;">'
            f'{self._number}</span>'
            f'{self._text}'
        )


# ── InfoRow ───────────────────────────────────────────────────
class InfoRow(QLabel):
    """유니코드 아이콘 + 텍스트를 rich text로 한 QLabel에 담아 완전한 가운데 정렬."""
    _ICON_MAP = {
        "phone": "📞",
        "mail":  "✉",
        "insta": "📷",
    }

    def __init__(self, icon_key: str, text: str, parent=None):
        super().__init__(parent)
        self._icon_key = icon_key
        self._text     = text
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:transparent;")

    def apply_scale(self, s):
        fs = max(round(34*s), 12)
        icon = self._ICON_MAP.get(self._icon_key, "")
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{fs}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;")
        self.setText(f'{icon}  {self._text}')


# ── TextRow ───────────────────────────────────────────────────
class TextRow(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)

    def apply_scale(self, s, bold=False):
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(34*s),12)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:{'400' if bold else '300'};background:transparent;")


# ── Divider ───────────────────────────────────────────────────
class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)

    def apply_scale(self, s):
        self.setFixedHeight(max(round(2*s), 1))
        self.setStyleSheet(f"background:{C_BORDER};color:{C_BORDER};border:none;")


# ── Main page ─────────────────────────────────────────────────
class InformationPage(QWidget):               # ★ CHANGED: QMainWindow → QWidget ★
    def __init__(self, on_home=None, on_close=None):
        super().__init__()                         # ★ CHANGED ★

        self._on_home  = on_home  or (lambda: None)
        self._on_close = on_close or (lambda: None)
        self._s        = 0.5

        self.setStyleSheet(f"background:{C_BG};")  # ★ CHANGED ★

        root = QVBoxLayout(self)                   # ★ CHANGED ★
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._topbar = TopBar(on_home=self._go_home, on_close=self._close)
        root.addWidget(self._topbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{C_BG};}}"
            f"QScrollBar:vertical{{width:0px;}}")
        root.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet(f"background:{C_BG};")
        self._content_lo = QVBoxLayout(content)
        self._content_lo.setAlignment(Qt.AlignHCenter)
        scroll.setWidget(content)

        self._scalables: list = []

        def add(w):
            self._content_lo.addWidget(w)
            self._scalables.append(w)
            return w

        def gap(ref_px: int):
            sp = QWidget()
            sp.setStyleSheet("background:transparent;")
            sp._ref = ref_px
            self._content_lo.addWidget(sp)
            self._scalables.append(sp)

        # ── 이용방법 ──────────────────────────────────────────
        gap(40)
        add(SectionTitle("이용방법"))
        gap(28)

        self._steps: list[StepRow] = []
        for num, txt in [
            ("①", "  스마트폰으로 진열대의 상품 QR 인식, 또는 키오스크에서 원하는 상품 찾기"),
            ("②", "  시착 요청 및 선택한 시착 좌석으로 이동 후 로봇 배달 대기"),
            ("③", "  상품 수령 및 '수령 완료' 누른 후 시착"),
            ("④", "  좌석 옆의 키오스크로 구매 결제, 또는 회수함에 반납"),
        ]:
            row = StepRow(num, txt)
            add(row)
            self._steps.append(row)
            gap(24)

        gap(30)
        add(Divider())
        gap(30)

        # ── 영업시간 ──────────────────────────────────────────
        add(SectionTitle("영업시간"))
        gap(24)
        self._hours: list[TextRow] = []
        for txt in ["평일  10:00 – 21:00", "주말  10:00 – 22:00"]:
            r = TextRow(txt); r._bold = False
            add(r); self._hours.append(r)
            gap(10)

        gap(30)

        # ── 휴무일 ───────────────────────────────────────────
        add(SectionTitle("휴무일"))
        gap(24)
        self._closed: list[TextRow] = []
        for txt in ["매월 첫째 월요일 정기 휴무", "공휴일 정상 영업"]:
            r = TextRow(txt); r._bold = False
            add(r); self._closed.append(r)
            gap(10)

        gap(30)

        # ── 고객 문의 ─────────────────────────────────────────
        add(SectionTitle("고객 문의"))
        gap(20)
        self._contacts: list[InfoRow] = []
        for icon_key, txt in [
            ("phone", "02-1234-5678"),
            ("mail",  "moosinsa@store.com"),
            ("insta", "@MoosinsaStore"),
        ]:
            row = InfoRow(icon_key, txt)
            add(row); self._contacts.append(row)

        gap(60)
        self._content_lo.addStretch()

    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()

    def _close(self):                                     # ★ CHANGED ★
        self._on_close()

    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_scale(s)

    def _apply_scale(self, s):
        self._content_lo.setContentsMargins(0, 0, 0, 0)
        self._content_lo.setSpacing(0)

        for w in self._scalables:
            if isinstance(w, (SectionTitle, TextRow, StepRow, InfoRow)):
                w.apply_scale(s)
            elif isinstance(w, Divider):
                w.apply_scale(s)
            elif hasattr(w, '_ref'):
                w.setFixedHeight(max(round(w._ref * s), 2))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = InformationPage(
        on_home=lambda: print("→ Home"),
        on_close=lambda: (print("→ 이전 페이지"), win.close()),
    )
    win.show()
    sys.exit(app.exec())
