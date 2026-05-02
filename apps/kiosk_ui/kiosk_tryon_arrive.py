import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QSizePolicy
)
from PySide6.QtCore import Qt, QByteArray, QTimer
from PySide6.QtSvgWidgets import QSvgWidget

REF_W, REF_H = 1080, 1920
ARRIVE_TIMEOUT_MS = 30_000

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

SVG_BACK = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M20 6L8 16l12 10"/>
</svg>"""  # ★ NEW ★

SVG_CHECK = """<svg viewBox="0 0 64 64" fill="none"
  stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <circle cx="32" cy="32" r="28"/>
  <polyline points="18,33 27,43 46,22"/>
</svg>"""

SVG_BOX = """<svg viewBox="0 0 64 64" fill="none"
  stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M32 6L58 20v24L32 58 6 44V20L32 6z"/>
  <path d="M32 6v52"/>
  <path d="M6 20l26 14 26-14"/>
  <path d="M19 13l26 14"/>
</svg>"""


def make_svg(tpl, color, w, h=0):
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background:transparent;")
    return wgt


class TopBar(QFrame):
    """arrive 화면 전용 — 수령 완료 전까지 홈/뒤로가기 없이 브랜드명만 표시."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_DARK};border:none;")
        self._lo = QHBoxLayout(self)
        self._brand = QLabel("MOOSINSA")
        self._brand.setAlignment(Qt.AlignCenter)
        self._lo.addStretch()
        self._lo.addWidget(self._brand)
        self._lo.addStretch()

    def apply_scale(self, s):
        self.setFixedHeight(max(round(130*s), 44))
        hm = max(round(40*s), 12)
        self._lo.setContentsMargins(hm, 0, hm, 0)
        self._brand.setStyleSheet(
            f"color:{C_BG};font-size:{max(round(44*s),14)}px;"
            f"font-family:'Georgia',serif;font-weight:500;"
            f"letter-spacing:{max(round(12*s),3)}px;background:transparent;")


class TryonArrivePage(QWidget):
    def __init__(self, on_home=None, on_confirmed=None):
        super().__init__()

        self._on_home      = on_home      or (lambda: None)
        self._on_confirmed = on_confirmed or (lambda: None)
        self._s            = 0.5
        self._confirmed    = False

        self.setStyleSheet(f"background:{C_BG};")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ── Top bar ── (수령 전: 홈/뒤로가기 없음)
        self._topbar = TopBar()
        self._root.addWidget(self._topbar)

        # ── 중앙 정렬 body ──
        self._body = QWidget()
        self._body.setStyleSheet(f"background:{C_BG};")
        self._body_lo = QVBoxLayout(self._body)
        self._body_lo.setAlignment(Qt.AlignCenter)
        self._root.addWidget(self._body, stretch=1)

        self._body_lo.addStretch(1)

        # 체크 아이콘
        self._check_icon = make_svg(SVG_CHECK, C_FOREST, 80)
        self._body_lo.addWidget(self._check_icon, alignment=Qt.AlignHCenter)

        # 도착 타이틀
        self._title = QLabel("도착했습니다!")
        self._title.setAlignment(Qt.AlignCenter)
        self._body_lo.addWidget(self._title)

        # 안내 문구
        self._desc = QLabel(
            "상품 박스를 가져가신 후\n'수령 완료' 버튼을 눌러주세요.")
        self._desc.setAlignment(Qt.AlignCenter)
        self._desc.setWordWrap(True)
        self._body_lo.addWidget(self._desc)

        # 박스 아이콘
        self._box_icon = make_svg(SVG_BOX, C_FOREST, 80)
        self._body_lo.addWidget(self._box_icon, alignment=Qt.AlignHCenter)

        self._body_lo.addStretch(1)

        # 카운트다운
        self._timer_lbl = QLabel()
        self._timer_lbl.setAlignment(Qt.AlignCenter)
        self._body_lo.addWidget(self._timer_lbl)

        self._body_lo.addStretch(1)

        # 수령완료 버튼
        self._confirm_btn = QPushButton("수령 완료")
        self._confirm_btn.setCursor(Qt.PointingHandCursor)
        self._confirm_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._confirm_btn.clicked.connect(self._confirm)
        self._root.addWidget(self._confirm_btn)

        self._remaining_ms = ARRIVE_TIMEOUT_MS
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)
        # ※ 타이머는 reset() 호출 시에만 시작 — __init__ 에서 즉시 시작하면
        #   PageManager가 앱 시작 시 모든 페이지를 생성하는 시점에 카운트다운이
        #   돌기 시작해 홈 화면에서 30초 후 tryon_another로 튀는 버그 발생.
        self._update_timer_lbl()

    def reset(self):                                       # ★ NEW ★
        """
        PageManager가 delivery → arrive 전환 시 호출.
        카운트다운과 confirmed 상태를 초기화하고 타이머를 다시 시작한다.
        """
        self._confirmed   = False
        self._remaining_ms = ARRIVE_TIMEOUT_MS
        self._update_timer_lbl()
        self._countdown.stop()
        self._countdown.start()

    def _tick(self):
        self._remaining_ms -= 1000
        self._update_timer_lbl()
        if self._remaining_ms <= 0:
            self._countdown.stop()
            self._confirm()

    def _update_timer_lbl(self):
        secs = max(self._remaining_ms // 1000, 0)
        self._timer_lbl.setText(f"{secs}초 후 자동으로 넘어갑니다")

    def _confirm(self):
        if self._confirmed:
            return
        self._confirmed = True
        self._countdown.stop()
        self._on_confirmed()

    def _go_home(self):                                   # ★ CHANGED ★
        self._countdown.stop()
        self._on_home()

    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_scale(s)

    def _apply_scale(self, s):
        hm = max(round(80*s), 26)
        self._body_lo.setContentsMargins(hm, 0, hm, 0)
        self._body_lo.setSpacing(max(round(44*s), 15))

        isz = max(round(110*s), 36); self._check_icon.setFixedSize(isz, isz)
        bsz = max(round(90*s),  30); self._box_icon.setFixedSize(bsz, bsz)

        self._title.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(64*s),21)}px;"
            f"font-family:'Georgia',serif;font-weight:600;background:transparent;")
        self._desc.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(38*s),13)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;line-height:1.7;background:transparent;")
        self._timer_lbl.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(24*s),8)}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:300;background:transparent;")

        h_btn  = max(round(160*s), 54)
        fs_btn = max(round(40*s),  14)
        self._confirm_btn.setFixedHeight(h_btn)
        self._confirm_btn.setStyleSheet(
            f"QPushButton{{background:{C_DARK};color:{C_BG};border:none;"
            f"font-size:{fs_btn}px;font-family:'Georgia',serif;"
            f"font-weight:500;letter-spacing:{max(round(8*s),2)}px;}}"
            f"QPushButton:hover{{background:#2E2E2E;}}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TryonArrivePage(
        on_home=lambda: print("→ Home"),
        on_confirmed=lambda: print("→ TryonAnotherPage"),
    )
    win.show()
    sys.exit(app.exec())