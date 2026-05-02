"""
moosinsa_tryon.py
- 상품 이미지/이름/가격 표시
- 색상·사이즈 재고 연동 버튼 (활성/비활성)
- 시착 좌석 선택 맵 (배경 이미지 교체 가능, 실시간 점유 상태)
- 미선택/점유 오류 팝업
- 시착 요청 버튼
"""
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QScrollArea, QSizePolicy,
    QDialog, QGridLayout, QButtonGroup
)
from PySide6.QtCore import Qt, QByteArray, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
from PySide6.QtSvgWidgets import QSvgWidget
from kiosk_category_brand import _load_image_async  # [트라이온이미지]

# ── Reference resolution ─────────────────────────────────────
REF_W, REF_H = 1080, 1920

# ── Palette ──────────────────────────────────────────────────
C_BG       = "#EDE9E3"
C_DARK     = "#1C1C1C"
C_FOREST   = "#2C3D30"
C_FOREST_T = "#C8DDB8"
C_BROWN    = "#5C4A3A"
C_BROWN_H  = "#6E5A48"
C_BORDER   = "#D6D1C9"
C_SUB      = "#999999"
C_DISABLED = "#C8C4BE"
C_OCCUPIED = "#C0392B"   # 점유 좌석
C_AVAIL    = C_FOREST    # 빈 좌석
C_SELECTED = C_DARK      # 선택된 좌석

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

SVG_WARN = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M16 3L2 28h28L16 3z"/>
  <line x1="16" y1="13" x2="16" y2="20"/>
  <circle cx="16" cy="24" r="1.2" fill="{color}"/>
</svg>"""


def make_svg(tpl: str, color: str, w: int, h: int = 0) -> QSvgWidget:
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ════════════════════════════════════════════════════════════
#  Mock DB
# ════════════════════════════════════════════════════════════
MOCK_PRODUCT = {
    "name":   "에어포스 1 '07",
    "brand":  "나이키",
    "price":  129000,
    "colors": [
        {"label": "Black",  "hex": "#1C1C1C", "stock": True},
        {"label": "White",  "hex": "#F5F1EC", "stock": True},
        {"label": "Red",    "hex": "#C0392B", "stock": False},  # 품절
        {"label": "Brown",  "hex": "#5C4A3A", "stock": True},
    ],
    "sizes": [
        {"label": "255", "stock": True},
        {"label": "260", "stock": True},
        {"label": "265", "stock": False},  # 품절
        {"label": "270", "stock": True},
        {"label": "275", "stock": True},
        {"label": "280", "stock": False},  # 품절
    ],
}

# [상품정보연동] category_brand product → tryon _build_content() 형식 변환 헬퍼
def _safe_product_for_tryon(product: dict) -> dict:
    """
    category_brand에서 넘어온 product dict를 _build_content()가 요구하는 형식으로 변환.
    - name: category_brand는 'name' 키 사용 (tryon은 'name' 직접 사용)
    - price: "₩129,000" 문자열 → int
    - colors: 문자열/JSON → [{"label", "hex", "stock"}]
    - sizes:  문자열/JSON → [{"label", "stock"}]
    """
    import json as _json

    COLOR_HEX_MAP = {
        "black": "#1C1C1C", "블랙": "#1C1C1C",
        "white": "#F5F1EC", "화이트": "#F5F1EC",
        "red": "#C0392B", "레드": "#C0392B",
        "brown": "#5C4A3A", "브라운": "#5C4A3A",
        "navy": "#1A2A4A", "네이비": "#1A2A4A",
        "grey": "#888888", "그레이": "#888888", "gray": "#888888",
        "beige": "#D4C5A9", "베이지": "#D4C5A9",
        "green": "#2C5F2E", "그린": "#2C5F2E",
        "blue": "#1E4D8C", "블루": "#1E4D8C",
    }

    # price: "₩129,000" → 129000
    price_raw = product.get("price", 0)
    if isinstance(price_raw, str):
        try:
            price = int(price_raw.replace("₩", "").replace(",", "").strip())
        except ValueError:
            price = 0
    else:
        price = int(price_raw)

    # colors 파싱
    raw_colors = product.get("colors", [])
    if isinstance(raw_colors, str):
        try:
            raw_colors = _json.loads(raw_colors)
        except Exception:
            raw_colors = []
    colors = []
    for c in raw_colors:
        if isinstance(c, dict) and "label" in c:
            colors.append(c)
        else:
            label = str(c)
            colors.append({"label": label, "hex": COLOR_HEX_MAP.get(label.lower(), "#888888"), "stock": True})

    # sizes 파싱
    raw_sizes = product.get("sizes", [])
    if isinstance(raw_sizes, str):
        try:
            raw_sizes = _json.loads(raw_sizes)
        except Exception:
            raw_sizes = []
    sizes = []
    for s in raw_sizes:
        if isinstance(s, dict) and "label" in s:
            sizes.append(s)
        else:
            sizes.append({"label": str(s), "stock": True})

    return {
        "name":      product.get("name") or product.get("model", ""),
        "brand":     product.get("brand", ""),
        "price":     price,
        "shoe_id":   product.get("shoe_id", ""),
        "image_url": product.get("image_url", None),
        "colors":    colors if colors else MOCK_PRODUCT["colors"],
        "sizes":     sizes  if sizes  else MOCK_PRODUCT["sizes"],
    }


# 좌석 맵: True = 점유, False = 빈 자리
# 실제 연동 시 이 dict를 서버에서 폴링하여 갱신
SEAT_STATUS = {
    "1": False, "2": True,
    "3": False, "4": False,
}
# 2x2 grid
SEAT_LAYOUT = [["1","2"],["3","4"]]

# 전면대 레이블 (좌석 맵 상단/하단 표시)
DISPLAY_LABEL = "진 열 대"


# ════════════════════════════════════════════════════════════
#  Top bar
# ════════════════════════════════════════════════════════════
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

    def apply_scale(self, s):
        self.setFixedHeight(max(round(130 * s), 44))
        hm = max(round(40 * s), 12)
        self._lo.setContentsMargins(hm, 0, hm, 0)
        icon_sz = max(round(54 * s), 20); self._home_icon.setFixedSize(icon_sz, icon_sz)
        btn_sz  = max(round(100 * s), 36); self._home_btn.setFixedSize(btn_sz, btn_sz)
        self._brand.setStyleSheet(
            f"color:{C_BG};font-size:{max(round(44*s),14)}px;"
            f"font-family:'Georgia',serif;font-weight:500;"
            f"letter-spacing:{max(round(12*s),3)}px;background:transparent;")
        bsz = max(round(100*s), 36)                        # ★ CHANGED ★
        self._back_btn.setFixedSize(bsz, bsz)
        bisz = max(round(44*s), 18)
        self._back_icon.setFixedSize(bisz, bisz)


# ════════════════════════════════════════════════════════════
#  Section label
# ════════════════════════════════════════════════════════════
class SectionLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)

    def apply_scale(self, s):
        self.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(32*s),11)}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:400;"
            f"letter-spacing:{max(round(4*s),1)}px;background:transparent;")


# ════════════════════════════════════════════════════════════
#  Option chip button (색상 / 사이즈)
# ════════════════════════════════════════════════════════════
class OptionChip(QPushButton):
    def __init__(self, label: str, enabled: bool, color_hex: str = None, parent=None):
        super().__init__(parent)
        self._label          = label
        self.setText(label)  # [색상사이즈버튼수정] 사이즈 버튼 텍스트 초기화
        self._available      = enabled
        self._color_hex      = color_hex
        self._filter_enabled = True  # [크로스필터] 다른 칩 선택 시 유효 조합 필터
        self.setCheckable(enabled)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.setEnabled(enabled)
        self.toggled.connect(lambda _: self._restyle(self._s))
        self._s = 0.5

    def set_filter(self, enabled: bool):
        """[크로스필터] 다른 축 칩이 선택될 때 유효하지 않은 조합 비활성화."""
        self._filter_enabled = enabled
        if not enabled and self.isChecked():
            self.setChecked(False)
        can = self._available and enabled
        self.setCheckable(can)
        self.setCursor(Qt.PointingHandCursor if can else Qt.ArrowCursor)
        self.setEnabled(can)
        self._restyle(self._s)

    def apply_scale(self, s):
        self._s = s
        self._restyle(s)

    def _restyle(self, s):
        r   = max(round(36 * s), 12)
        fs  = max(round(30 * s), 11)
        px  = max(round(34 * s), 11)
        py  = max(round(16 * s), 5)
        h   = max(round(88 * s), 30)
        self.setFixedHeight(h)

        if not self._available:
            # 품절: 취소선
            self.setStyleSheet(
                f"QPushButton{{background:transparent;color:{C_DISABLED};"
                f"border:1.5px solid {C_DISABLED};border-radius:{r}px;"
                f"font-size:{fs}px;font-family:'Helvetica Neue',Arial;"
                f"font-weight:300;padding:{py}px {px}px;"
                f"text-decoration:line-through;}}")
            return

        if not self._filter_enabled:
            # [크로스필터] 선택한 다른 축과 조합 없음: 흐리게, 취소선 없음
            self.setStyleSheet(
                f"QPushButton{{background:transparent;color:{C_DISABLED};"
                f"border:1.5px dashed {C_DISABLED};border-radius:{r}px;"
                f"font-size:{fs}px;font-family:'Helvetica Neue',Arial;"
                f"font-weight:300;padding:{py}px {px}px;}}")
            return

        active = self.isChecked()
        if self._color_hex:
            # [색상사이즈버튼수정] 배경=실제 색상, 명도 기반 폰트색 자동 결정
            h = self._color_hex.lstrip("#")
            if len(h) == 6:
                _r, _g, _b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                fg = "#F5F1EC" if (0.299*_r + 0.587*_g + 0.114*_b) < 128 else "#1C1C1C"
            else:
                fg = "#1C1C1C"
            border = f"3px solid {fg}" if active else f"1.5px solid {C_BORDER}"
            self.setText(self._label)
            self.setStyleSheet(
                f"QPushButton{{background:{self._color_hex};color:{fg};"
                f"border:{border};border-radius:{r}px;"
                f"font-size:{fs}px;font-family:'Helvetica Neue',Arial;"
                f"font-weight:{'600' if active else '500'};padding:{py}px {px}px;}}"
                f"QPushButton:hover{{border:2px solid {fg};}}")
        else:
            # 사이즈 칩
            if active:
                self.setStyleSheet(
                    f"QPushButton{{background:{C_DARK};color:{C_BG};"
                    f"border:2px solid {C_DARK};border-radius:{r}px;"
                    f"font-size:{fs}px;font-family:'Helvetica Neue',Arial;"
                    f"font-weight:500;padding:{py}px {px}px;}}")
            else:
                self.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{C_DARK};"
                    f"border:1.5px solid {C_BORDER};border-radius:{r}px;"
                    f"font-size:{fs}px;font-family:'Helvetica Neue',Arial;"
                    f"font-weight:400;padding:{py}px {px}px;}}"
                    f"QPushButton:hover{{border:1.5px solid {C_DARK};}}")


# ════════════════════════════════════════════════════════════
#  Seat map widget
# ════════════════════════════════════════════════════════════
class SeatMap(QFrame):
    """
    배경 이미지 위에 좌석 버튼을 오버레이.
    set_map_image(path) 로 배경 교체 가능.
    set_seat_status(dict) 로 실시간 점유 상태 갱신.
    """
    def __init__(self, seat_status: dict, on_select, parent=None):
        super().__init__(parent)
        self._status    = dict(seat_status)
        self._on_select = on_select
        self._selected  = None
        self._seat_btns: dict[str, QPushButton] = {}
        self._map_pixmap: QPixmap | None = None
        self._s         = 0.5

        self.setStyleSheet(
            f"QFrame{{background:{C_BG};"
            f"border:1px solid {C_BORDER};border-radius:12px;}}")
        self._build()

    def set_map_image(self, path: str):
        """배경 맵 이미지 교체. PNG/JPG 경로."""
        self._map_pixmap = QPixmap(path)
        self.update()

    def set_seat_status(self, status: dict):
        """실시간 좌석 점유 상태 갱신."""
        self._status = dict(status)
        if self._selected and self._status.get(self._selected, False):
            self._selected = None
            self._on_select(None)
        self._restyle_seats(self._s)

    def selected_seat(self) -> str | None:
        return self._selected

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 상단 진열대 레이블
        self._top_lbl = QLabel(DISPLAY_LABEL)
        self._top_lbl.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._top_lbl)

        # 좌석 그리드 (입구/통로 표시 포함)
        self._grid_frame = QFrame()
        self._grid_frame.setStyleSheet("background:transparent;border:none;")
        self._grid_lo = QGridLayout(self._grid_frame)
        self._grid_lo.setAlignment(Qt.AlignCenter)

        # 좌석 버튼 배치 (2x2)
        for ri, row in enumerate(SEAT_LAYOUT):
            for ci, seat_id in enumerate(row):
                btn = QPushButton(seat_id)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setCheckable(True)
                btn.clicked.connect(
                    lambda checked=False, sid=seat_id: self._seat_clicked(sid))
                self._seat_btns[seat_id] = btn
                self._grid_lo.addWidget(btn, ri, ci + 1)

        outer.addWidget(self._grid_frame, stretch=1)

        # 하단 진열대 레이블
        self._bot_lbl = QLabel(DISPLAY_LABEL)
        self._bot_lbl.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._bot_lbl)

    def _seat_clicked(self, seat_id: str):
        if self._status.get(seat_id, False):
            # 점유 중 → 선택 불가
            self._seat_btns[seat_id].setChecked(False)
            return
        # 이전 선택 해제
        if self._selected and self._selected != seat_id:
            self._seat_btns[self._selected].setChecked(False)
        self._selected = seat_id if self._seat_btns[seat_id].isChecked() else None
        self._on_select(self._selected)
        self._restyle_seats(self._s)

    def _restyle_seats(self, s):
        r   = max(round(14 * s), 5)
        sz  = max(round(110 * s), 38)
        fs  = max(round(22 * s), 8)
        for sid, btn in self._seat_btns.items():
            occupied = self._status.get(sid, False)
            selected = (sid == self._selected)
            btn.setEnabled(not occupied)
            btn.setFixedSize(sz, sz)
            if occupied:
                btn.setStyleSheet(
                    f"QPushButton{{background:{C_OCCUPIED};color:#fff;"
                    f"border:none;border-radius:{r}px;"
                    f"font-size:{max(round(30*s),11)}px;font-weight:500;}}")
            elif selected:
                btn.setStyleSheet(
                    f"QPushButton{{background:{C_DARK};color:{C_BG};"
                    f"border:2px solid {C_DARK};border-radius:{r}px;"
                    f"font-size:{fs}px;font-weight:600;}}")
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:{C_FOREST};color:{C_FOREST_T};"
                    f"border:none;border-radius:{r}px;"
                    f"font-size:{fs}px;font-weight:400;}}"
                    f"QPushButton:hover{{background:#364D3A;}}")

    def apply_scale(self, s):
        self._s = s
        hm = max(round(20 * s), 7)
        vm = max(round(16 * s), 5)
        self.layout().setContentsMargins(hm, vm, hm, vm)
        self.layout().setSpacing(max(round(10 * s), 3))
        self._grid_lo.setSpacing(max(round(10 * s), 3))
        self.setStyleSheet(
            f"QFrame{{background:{C_BG};"
            f"border:1px solid {C_BORDER};"
            f"border-radius:{max(round(16*s),6)}px;}}")

        fs_lbl = max(round(22 * s), 8)
        for lbl in (self._top_lbl, self._bot_lbl):
            lbl.setFixedHeight(max(round(44 * s), 16))
            lbl.setStyleSheet(
                f"color:{C_SUB};font-size:{fs_lbl}px;"
                f"font-family:'Helvetica Neue',Arial;font-weight:300;"
                f"letter-spacing:{max(round(4*s),1)}px;background:transparent;border:none;")

        self._restyle_seats(s)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._map_pixmap and not self._map_pixmap.isNull():
            painter = QPainter(self)
            painter.setOpacity(0.18)
            scaled = self._map_pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (self.width()  - scaled.width())  // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)


# ════════════════════════════════════════════════════════════
#  Error popup dialog
# ════════════════════════════════════════════════════════════
class ErrorDialog(QDialog):
    def __init__(self, message: str, s: float, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog{{background:{C_BG};"
            f"border:1.5px solid {C_BORDER};"
            f"border-radius:{max(round(24*s),8)}px;}}")

        lo = QVBoxLayout(self)
        pad = max(round(60 * s), 20)
        lo.setContentsMargins(pad, pad, pad, pad)
        lo.setSpacing(max(round(28 * s), 10))
        lo.setAlignment(Qt.AlignCenter)

        # 경고 아이콘
        icon = make_svg(SVG_WARN, C_BROWN, max(round(52 * s), 18))
        lo.addWidget(icon, alignment=Qt.AlignCenter)

        # 메시지
        msg_lbl = QLabel(message)
        msg_lbl.setAlignment(Qt.AlignCenter)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(28*s),10)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:400;background:transparent;border:none;")
        lo.addWidget(msg_lbl)

        # 확인 버튼
        ok_btn = QPushButton("확인")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setFixedHeight(max(round(72 * s), 26))
        ok_btn.setStyleSheet(
            f"QPushButton{{background:{C_DARK};color:{C_BG};border:none;"
            f"border-radius:{max(round(12*s),4)}px;"
            f"font-size:{max(round(26*s),9)}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:500;}}"
            f"QPushButton:hover{{background:#2E2E2E;}}")
        ok_btn.clicked.connect(self.accept)
        lo.addWidget(ok_btn)

        self.setMinimumWidth(max(round(520 * s), 180))


# ════════════════════════════════════════════════════════════
#  Main page
# ════════════════════════════════════════════════════════════
class TryonPage(QWidget):                     # ★ CHANGED: QMainWindow → QWidget ★
    def __init__(
        self,
        product: dict = None,
        seat_status: dict = None,
        on_home=None,
        on_back=None,
        on_tryon_request=None,
        api_client=None,
    ):
        super().__init__()

        self._product    = product      or MOCK_PRODUCT
        self._seats      = seat_status  or SEAT_STATUS
        self._on_home    = on_home      or (lambda: None)
        self._on_back    = on_back      or (lambda: None)
        self._on_request = on_tryon_request or (
            lambda sel: print(f"시착 요청: {sel}"))
        self._api        = api_client   # KioskApiClient 인스턴스 (None이면 MOCK 사용)
        self._s          = 0.5

        self._sel_color  = None
        self._sel_size   = None
        self._sel_seat   = None

        self.setStyleSheet(f"background:{C_BG};")  # ★ CHANGED ★
        self._root = QVBoxLayout(self)             # ★ CHANGED ★
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # Top bar
        self._topbar = TopBar(on_home=self._go_home, on_back=self._on_back)  # ★ CHANGED ★
        self._root.addWidget(self._topbar)

        # Scroll area (전체 콘텐츠)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{C_BG};}}"
            f"QScrollBar:vertical{{width:0px;}}")
        self._root.addWidget(self._scroll, stretch=1)

        # Content widget
        self._content = QWidget()
        self._content.setStyleSheet(f"background:{C_BG};")
        self._content_lo = QVBoxLayout(self._content)
        self._scroll.setWidget(self._content)

        self._build_content()

        # 시착 요청 버튼 (항상 하단 고정)
        self._request_btn = QPushButton("시 착 요 청")
        self._request_btn.setCursor(Qt.PointingHandCursor)
        self._request_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._request_btn.clicked.connect(self._on_request_clicked)
        self._root.addWidget(self._request_btn)

    # ── Build content ────────────────────────────────────────
    def _build_content(self):
        lo = self._content_lo
        lo.setSpacing(0)
        lo.setContentsMargins(0, 0, 0, 0)

        p = self._product

        # ── 상품 정보 ──────────────────────────────────────
        self._product_frame = QFrame()
        self._product_frame.setStyleSheet(
            f"QFrame{{background:{C_BG};border:none;}}")
        pf_lo = QHBoxLayout(self._product_frame)

        # 이미지 placeholder
        self._img_frame = QFrame()
        self._img_frame.setStyleSheet(
            f"QFrame{{background:{C_BG};border:1px solid {C_BORDER};"
            f"border-radius:12px;}}")
        img_inner = QLabel("IMG")
        img_inner.setAlignment(Qt.AlignCenter)
        img_inner.setStyleSheet(f"color:{C_BORDER};background:transparent;border:none;")
        img_lo = QVBoxLayout(self._img_frame)
        img_lo.setContentsMargins(0, 0, 0, 0)
        img_lo.addWidget(img_inner)
        self._img_label = img_inner
        pf_lo.addWidget(self._img_frame)

        # 이름 / 브랜드 / 가격
        info_w = QWidget()
        info_w.setStyleSheet("background:transparent;")
        info_lo = QVBoxLayout(info_w)
        info_lo.setAlignment(Qt.AlignVCenter)
        self._name_lbl  = QLabel(p["name"])
        self._name_lbl.setWordWrap(True)
        self._brand_lbl = QLabel(p["brand"])
        self._price_lbl = QLabel(f"₩{p['price']:,}")
        for lbl in (self._name_lbl, self._brand_lbl, self._price_lbl):
            lbl.setStyleSheet("background:transparent;")
        info_lo.addWidget(self._name_lbl)
        info_lo.addWidget(self._brand_lbl)
        info_lo.addSpacing(8)
        info_lo.addWidget(self._price_lbl)
        pf_lo.addWidget(info_w, stretch=1)
        lo.addWidget(self._product_frame)

        # ── 색상 선택 ───────────────────────────────────────
        self._color_section = self._make_section("색 상 선 택")
        self._color_chips: list[OptionChip] = []
        self._color_chip_lo = QHBoxLayout()
        self._color_chip_lo.setAlignment(Qt.AlignLeft)
        for c in p["colors"]:
            chip = OptionChip(c["label"], c["stock"], c["hex"])
            chip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            chip.clicked.connect(lambda chk=False, lbl=c["label"]: self._color_selected(lbl))
            self._color_chips.append(chip)
            self._color_chip_lo.addWidget(chip)
        self._color_group = QButtonGroup(self)
        self._color_group.setExclusive(True)
        for chip in self._color_chips:
            self._color_group.addButton(chip)
        self._color_section.layout().addLayout(self._color_chip_lo)
        lo.addWidget(self._color_section)

        # ── 사이즈 선택 ─────────────────────────────────────
        self._size_section = self._make_section("사 이 즈 선 택")
        self._size_chips: list[OptionChip] = []
        self._size_chip_lo = QHBoxLayout()
        self._size_chip_lo.setAlignment(Qt.AlignLeft)
        for sz in p["sizes"]:
            chip = OptionChip(sz["label"], sz["stock"])
            chip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            chip.clicked.connect(lambda chk=False, lbl=sz["label"]: self._size_selected(lbl))
            self._size_chips.append(chip)
            self._size_chip_lo.addWidget(chip)
        self._size_group = QButtonGroup(self)
        self._size_group.setExclusive(True)
        for chip in self._size_chips:
            self._size_group.addButton(chip)
        self._size_section.layout().addLayout(self._size_chip_lo)
        lo.addWidget(self._size_section)

        # ── 시착 좌석 선택 ─────────────────────────────────
        self._seat_section = self._make_section("시 착 좌 석 선 택")
        self._seat_map = SeatMap(self._seats, self._seat_selected)
        self._seat_section.layout().addWidget(self._seat_map)
        lo.addWidget(self._seat_section)

        lo.addStretch()

    def _make_section(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame{{background:{C_BG};border:none;}}")
        vlo = QVBoxLayout(frame)
        lbl = SectionLabel(title)
        vlo.addWidget(lbl)
        frame._title_lbl = lbl
        return frame

    def update_product(self, product: dict, seat_status: dict = None):
        """
        PageManager가 상품 클릭 시 호출.
        product에 shoe_id가 있으면 API에서 색상/사이즈/재고를 조회한 뒤 빌드.
        shoe_id가 없거나 API 미설정이면 전달받은 product 그대로 사용.
        """
        # [상품정보연동] category_brand에서 넘어온 product는 포맷이 달라 _build_content()
        # 에서 c["label"] 등 접근 시 TypeError가 발생할 수 있음 → 변환 후 렌더
        self._product = _safe_product_for_tryon(product)
        if seat_status:
            self._seats = seat_status
        self._sel_color = None
        self._sel_size  = None
        self._sel_seat  = None
        self._rebuild_content()

        shoe_id = product.get("shoe_id", "")
        if self._api and shoe_id:
            # [상품상세재고연동] API에서 이름/가격/재고 모두 조회 후 갱신
            from kiosk_api_client import normalize_shoe_for_tryon, normalize_seat_status
            def _on_shoe(data):
                if data:
                    self._product = normalize_shoe_for_tryon(data)
                    self._sel_color = None
                    self._sel_size  = None
                    self._rebuild_content()
            def _on_seat(data):
                if data:
                    self._seats = normalize_seat_status(data)
                    self._sel_seat = None
                    self._rebuild_content()
            self._api.fetch_shoe_full(shoe_id, callback=_on_shoe)  # [상품상세재고연동]
            self._api.fetch_seat_status(callback=_on_seat)

    def _rebuild_content(self):
        """content 위젯 초기화 후 재빌드."""
        self._sel_color = None
        self._sel_size  = None
        self._sel_seat  = None
        while self._content_lo.count():
            item = self._content_lo.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._build_content()
        self._apply_content_scale(self._s)
        self._scroll.verticalScrollBar().setValue(0)  # [스크롤초기화] 상품 변경 시 맨 위로

    # ── selection callbacks ──────────────────────────────────
    def _color_selected(self, label: str):
        self._sel_color = label
        valid_sizes = set(self._product.get("color_sizes", {}).get(label, []))
        for chip in self._size_chips:
            chip.set_filter(not valid_sizes or chip._label in valid_sizes)
        if valid_sizes and self._sel_size and self._sel_size not in valid_sizes:
            self._sel_size = None
        self._update_request_btn()

    def _size_selected(self, label: str):
        self._sel_size = label
        valid_colors = set(self._product.get("size_colors", {}).get(label, []))
        for chip in self._color_chips:
            chip.set_filter(not valid_colors or chip._label in valid_colors)
        if valid_colors and self._sel_color and self._sel_color not in valid_colors:
            self._sel_color = None
        self._update_request_btn()

    def _seat_selected(self, seat_id):
        self._sel_seat = seat_id
        self._update_request_btn()

    def _update_request_btn(self):
        self._apply_request_btn_style(self._s)

    def _apply_request_btn_style(self, s, ready: bool = True):
        fs = max(round(36 * s), 13)
        h  = max(round(150 * s), 52)
        self._request_btn.setFixedHeight(h)
        self._request_btn.setStyleSheet(
            f"QPushButton{{background:{C_DARK};color:{C_BG};border:none;"
            f"font-size:{fs}px;font-family:'Georgia',serif;"
            f"font-weight:500;letter-spacing:{max(round(8*s),2)}px;}}"
            f"QPushButton:hover{{background:#2E2E2E;}}") 

    def _on_request_clicked(self):
        missing = []
        if not self._sel_color:
            missing.append("색상")
        if not self._sel_size:
            missing.append("사이즈")
        if not self._sel_seat:
            missing.append("시착 좌석")
        elif self._seats.get(self._sel_seat, False):
            dlg = ErrorDialog(
                f"선택하신 좌석({self._sel_seat})은\n현재 사용 중입니다.\n다른 좌석을 선택해 주세요.",
                self._s, self)
            dlg.exec()
            return

        if missing:
            items = ", ".join(missing)
            dlg = ErrorDialog(
                f"{items} 선택 후\n시착 요청할 수 있습니다.", self._s, self)
            dlg.exec()
            return

        selection = {
            "product":  self._product["name"],
            "shoe_id":  self._product.get("shoe_id", ""),
            "color":    self._sel_color,
            "size":     self._sel_size,
            "seat":     self._sel_seat,
        }

        shoe_id = selection["shoe_id"]
        if self._api and shoe_id:
            # [시착요청연동] 버튼 비활성화 (중복 클릭 방지)
            self._request_btn.setEnabled(False)

            # [시착요청연동] STEP 1: DB 재고 확인
            def _on_stock(data):
                if data is None:
                    self._request_btn.setEnabled(True)
                    dlg = ErrorDialog(
                        "서버와 통신에 실패했습니다.\n잠시 후 다시 시도해 주세요.",
                        self._s, self)
                    dlg.exec()
                    return
                if not data.get("in_stock", False):
                    self._request_btn.setEnabled(True)
                    dlg = ErrorDialog(
                        "죄송합니다.\n선택하신 상품의 재고가 소진되었습니다.\n"
                        "다른 색상 또는 사이즈를 선택해 주세요.",
                        self._s, self)
                    dlg.exec()
                    return

                # [시착요청연동] STEP 2: 로봇 시착 요청
                def _on_tryon(resp):
                    self._request_btn.setEnabled(True)
                    if resp is None:
                        dlg = ErrorDialog(
                            "서버와 통신에 실패했습니다.\n잠시 후 다시 시도해 주세요.",
                            self._s, self)
                        dlg.exec()
                        return
                    if not resp.get("success", True):
                        dlg = ErrorDialog(
                            resp.get("detail", "시착 요청에 실패했습니다."),
                            self._s, self)
                        dlg.exec()
                        return
                    selection["robot_id"] = resp.get("robot_id", "sshopy2")
                    self._on_request(selection)

                self._api.request_tryon(
                    shoe_id=shoe_id,
                    color=self._sel_color,
                    size=self._sel_size,
                    seat_id=int(self._sel_seat),
                    robot_id="sshopy2",
                    callback=_on_tryon,
                )

            self._api.check_stock(
                shoe_id=shoe_id,
                color=self._sel_color,
                size=self._sel_size,
                callback=_on_stock,
            )
        else:
            # API 없음 (mock 모드) — 재고 확인 없이 바로 진행
            selection["robot_id"] = "sshopy2"
            self._on_request(selection)

    # ── navigation ───────────────────────────────────────────
    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()

    # ── resize / scale ───────────────────────────────────────
    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_content_scale(s)

    def _apply_content_scale(self, s):
        hm = max(round(50 * s), 16)
        vm = max(round(40 * s), 14)

        # 상품 영역
        img_sz = max(round(220 * s), 74)
        self._img_frame.setFixedSize(img_sz, img_sz)
        self._img_frame.setStyleSheet(
            f"QFrame{{background:{C_BG};border:1px solid {C_BORDER};"
            f"border-radius:{max(round(12*s),4)}px;}}")
        self._img_label.setStyleSheet(
            f"color:{C_BORDER};font-size:{max(round(18*s),7)}px;"
            f"font-family:'Helvetica Neue',Arial;background:transparent;border:none;")
        _load_image_async(self._product.get("image_url", ""), self._img_label, img_sz)  # [트라이온이미지]

        self._product_frame.layout().setContentsMargins(hm, vm, hm, vm)
        self._product_frame.layout().setSpacing(max(round(28 * s), 10))

        self._name_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(34*s),12)}px;"
            f"font-family:'Georgia',serif;font-weight:600;background:transparent;")
        self._brand_lbl.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(30*s),11)}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:300;background:transparent;")
        self._price_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(36*s),13)}px;"
            f"font-family:'Georgia',serif;font-weight:500;background:transparent;")

        # 섹션들 공통 여백
        for section in (self._color_section, self._size_section, self._seat_section):
            section.layout().setContentsMargins(hm, vm, hm, vm)
            section.layout().setSpacing(max(round(28 * s), 9))
            section._title_lbl.apply_scale(s)

        # 칩 간격
        for chip_lo in (self._color_chip_lo, self._size_chip_lo):
            chip_lo.setSpacing(max(round(10 * s), 3))

        for chip in self._color_chips + self._size_chips:
            chip.apply_scale(s)

        # 좌석 맵
        map_h = max(round(340 * s), 120)
        self._seat_map.setFixedHeight(map_h)
        self._seat_map.apply_scale(s)

        # 시착 버튼
        self._apply_request_btn_style(s)


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    def on_request(sel):
        print(f"\n[시착 요청 완료]")
        print(f"  상품: {sel['product']}")
        print(f"  색상: {sel['color']}")
        print(f"  사이즈: {sel['size']}")
        print(f"  좌석: {sel['seat']}")

    win = TryonPage(
        product=MOCK_PRODUCT,
        seat_status=SEAT_STATUS,
        on_home=lambda: print("→ Home"),
        on_back=lambda: print("→ Back"),     # ★ CHANGED ★
        on_tryon_request=on_request,
    )
    win.show()
    sys.exit(app.exec())
