import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QSizePolicy
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

SVG_BACK = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M20 6L8 16l12 10"/>
</svg>"""  # ★ NEW ★


def make_svg(tpl, color, w, h=0):
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background:transparent;")
    return wgt


class TopBar(QFrame):
    """another 화면 전용 — 홈 버튼만 있고 뒤로가기 없음."""
    def __init__(self, on_home, parent=None):
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
        # 우측 균형용 빈 위젯 (홈 버튼과 동일 크기)
        self._spacer_btn = QWidget()
        self._lo.addWidget(self._home_btn)
        self._lo.addStretch()
        self._lo.addWidget(self._brand)
        self._lo.addStretch()
        self._lo.addWidget(self._spacer_btn)

    def apply_scale(self, s):
        self.setFixedHeight(max(round(130*s), 44))
        hm = max(round(40*s), 12)
        self._lo.setContentsMargins(hm, 0, hm, 0)
        isz = max(round(54*s), 20); self._home_icon.setFixedSize(isz, isz)
        bsz = max(round(100*s), 36)
        self._home_btn.setFixedSize(bsz, bsz)
        self._spacer_btn.setFixedSize(bsz, bsz)
        self._brand.setStyleSheet(
            f"color:{C_BG};font-size:{max(round(44*s),14)}px;"
            f"font-family:'Georgia',serif;font-weight:500;"
            f"letter-spacing:{max(round(12*s),3)}px;background:transparent;")


# ── 상품 정보 위젯 (외곽선 없음, 이미지+정보 세로 구성) ────
class ProductCard(QWidget):
    def __init__(self, order: dict, parent=None):
        super().__init__(parent)
        self._order = order
        self.setStyleSheet("background:transparent;")

        self._lo = QVBoxLayout(self)
        self._lo.setAlignment(Qt.AlignCenter)

        # 상단: 이미지 + 기본 정보 가로 배치
        top_w = QWidget()
        top_w.setStyleSheet("background:transparent;")
        top_lo = QHBoxLayout(top_w)
        top_lo.setAlignment(Qt.AlignCenter)

        # 이미지 플레이스홀더
        self._img_frame = QFrame()
        self._img_frame.setStyleSheet(
            f"QFrame{{background:#E8E3DC;"
            f"border:1px solid {C_BORDER};border-radius:12px;}}")
        self._img_lbl = QLabel("IMG")
        self._img_lbl.setAlignment(Qt.AlignCenter)
        self._img_lbl.setStyleSheet(
            f"color:{C_BORDER};background:transparent;border:none;")
        img_inner = QVBoxLayout(self._img_frame)
        img_inner.setContentsMargins(0, 0, 0, 0)
        img_inner.addWidget(self._img_lbl)
        top_lo.addWidget(self._img_frame)

        # 이름/브랜드/가격
        info_w = QWidget()
        info_w.setStyleSheet("background:transparent;")
        info_lo = QVBoxLayout(info_w)
        info_lo.setAlignment(Qt.AlignVCenter)
        self._name_lbl  = QLabel(order.get("product", "—"))
        self._name_lbl.setWordWrap(True)
        self._brand_lbl = QLabel(order.get("brand", "—"))
        self._price_lbl = QLabel(f"₩{order.get('price', 0):,}" if order.get("price") else "—")
        for lbl in (self._name_lbl, self._brand_lbl, self._price_lbl):
            lbl.setStyleSheet("background:transparent;")
        info_lo.addWidget(self._name_lbl)
        info_lo.addWidget(self._brand_lbl)
        info_lo.addWidget(self._price_lbl)
        top_lo.addWidget(info_w, stretch=1)
        self._lo.addWidget(top_w)

        # 하단: 색상·사이즈·좌석 각각 행으로 표시
        detail_w = QWidget()
        detail_w.setStyleSheet("background:transparent;")
        self._detail_lo = QVBoxLayout(detail_w)
        self._detail_lo.setSpacing(0)

        def make_row(key, val, highlight=False):
            row = QWidget()
            row.setStyleSheet("background:transparent;")
            rlo = QHBoxLayout(row)
            k_lbl = QLabel(key)
            v_lbl = QLabel(val)
            k_lbl.setStyleSheet("background:transparent;")
            v_lbl.setStyleSheet("background:transparent;")
            v_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rlo.addWidget(k_lbl)
            rlo.addStretch()
            rlo.addWidget(v_lbl)
            return row, k_lbl, v_lbl

        self._color_row,  self._color_k,  self._color_v  = make_row("색상",  order.get("color","—"))
        self._size_row,   self._size_k,   self._size_v   = make_row("사이즈", order.get("size","—"))
        self._seat_row,   self._seat_k,   self._seat_v   = make_row("시착 좌석", order.get("seat","—"), highlight=True)

        for row in (self._color_row, self._size_row, self._seat_row):
            self._detail_lo.addWidget(row)

        self._lo.addWidget(detail_w)

    def apply_scale(self, s):
        self._lo.setContentsMargins(0, 0, 0, 0)
        self._lo.setSpacing(max(round(32*s), 11))

        # 상단 이미지+정보 레이아웃
        top_lo = self._lo.itemAt(0).widget().layout()
        top_lo.setContentsMargins(0, 0, 0, 0)
        top_lo.setSpacing(max(round(28*s), 10))

        img_sz = max(round(200*s), 66)
        self._img_frame.setFixedSize(img_sz, img_sz)
        self._img_lbl.setStyleSheet(
            f"color:{C_BORDER};font-size:{max(round(18*s),7)}px;"
            f"font-family:'Helvetica Neue',Arial;background:transparent;border:none;")
        self._img_frame.setStyleSheet(
            f"QFrame{{background:#E8E3DC;"
            f"border:1px solid {C_BORDER};"
            f"border-radius:{max(round(12*s),4)}px;}}")

        info_lo = top_lo.itemAt(1).widget().layout()
        info_lo.setSpacing(max(round(10*s), 3))

        self._name_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(32*s),11)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:600;background:transparent;")
        self._brand_lbl.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(28*s),10)}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:400;background:transparent;")
        self._price_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(30*s),11)}px;"
            f"font-family:'Georgia',serif;font-weight:500;background:transparent;")

        # 하단 디테일 행들
        row_h   = max(round(80*s), 28)
        fs_key  = max(round(26*s), 9)
        fs_val  = max(round(28*s), 10)
        fs_seat_key = max(round(30*s), 11)
        fs_seat_val = max(round(52*s), 18)  # 좌석번호 크게
        hm = max(round(10*s), 3)

        self._detail_lo.setSpacing(0)

        for row, k_lbl, v_lbl, fk, fv in [
            (self._color_row, self._color_k, self._color_v, fs_key, fs_val),
            (self._size_row,  self._size_k,  self._size_v,  fs_key, fs_val),
        ]:
            row.setFixedHeight(row_h)
            row.layout().setContentsMargins(hm, 0, hm, 0)
            k_lbl.setStyleSheet(
                f"color:{C_SUB};font-size:{fk}px;"
                f"font-family:'Helvetica Neue',Arial;font-weight:300;background:transparent;")
            v_lbl.setStyleSheet(
                f"color:{C_DARK};font-size:{fv}px;"
                f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
                f"font-weight:500;background:transparent;")

        # 좌석 행 — 크게 강조
        seat_h = max(round(120*s), 42)
        self._seat_row.setFixedHeight(seat_h)
        self._seat_row.layout().setContentsMargins(hm, 0, hm, 0)
        self._seat_k.setStyleSheet(
            f"color:{C_DARK};font-size:{fs_seat_key}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:400;background:transparent;")
        self._seat_v.setStyleSheet(
            f"color:{C_DARK};font-size:{fs_seat_val}px;"
            f"font-family:'Georgia',serif;font-weight:600;background:transparent;")


class TryonAnotherPage(QWidget):
    def __init__(self, order=None, on_home=None, on_retry=None):
        super().__init__()

        self._order    = order or {}
        self._on_home  = on_home  or (lambda: None)
        self._on_retry = on_retry or (lambda: None)
        self._s        = 0.5

        self.setStyleSheet(f"background:{C_BG};")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ── Top bar ── (홈만 있고 뒤로가기 없음)
        self._topbar = TopBar(on_home=self._go_home)
        self._root.addWidget(self._topbar)

        # ── 중앙 정렬 body ──
        self._body = QWidget()
        self._body.setStyleSheet(f"background:{C_BG};")
        self._body_lo = QVBoxLayout(self._body)
        self._root.addWidget(self._body, stretch=1)

        self._body_lo.addStretch(2)

        # 메인 문구
        self._title = QLabel("같은 상품, 다른 사이즈\n신어보시겠어요?")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)
        self._body_lo.addWidget(self._title)

        # 서브 문구
        self._sub = QLabel("앉아있던 좌석에서 바로\n추가 시착 요청이 가능합니다.")
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setWordWrap(True)
        self._body_lo.addWidget(self._sub)

        self._body_lo.addStretch(1)

        # 상품 카드
        self._card = ProductCard(self._order)
        self._body_lo.addWidget(self._card)

        self._body_lo.addStretch(2)

        # 하단 버튼
        self._retry_btn = QPushButton("다른 사이즈 시착하기")
        self._retry_btn.setCursor(Qt.PointingHandCursor)
        self._retry_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._retry_btn.clicked.connect(self._do_retry)
        self._root.addWidget(self._retry_btn)

    def _do_retry(self):                                  # ★ CHANGED ★
        self._on_retry()

    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()

    def update_order(self, order: dict):                  # ★ NEW ★
        """PageManager가 arrive → another 전환 시 order 정보를 갱신한다."""
        self._order = order
        self._card._order = order
        # 카드 내 레이블 직접 갱신
        self._card._name_lbl.setText(order.get("product", "—"))
        self._card._brand_lbl.setText(order.get("brand", "—"))
        price = order.get("price")
        self._card._price_lbl.setText(f"₩{price:,}" if price else "—")
        self._card._color_v.setText(order.get("color", "—"))
        self._card._size_v.setText(order.get("size", "—"))
        self._card._seat_v.setText(str(order.get("seat", "—")))

    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_scale(s)

    def _apply_scale(self, s):
        hm = max(round(70*s), 22)
        self._body_lo.setContentsMargins(hm, 0, hm, 0)
        self._body_lo.setSpacing(max(round(36*s), 12))

        self._title.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(52*s),18)}px;"
            f"font-family:'Georgia',serif;font-weight:600;"
            f"line-height:1.3;background:transparent;")
        self._sub.setStyleSheet(
            f"color:{C_SUB};font-size:{max(round(28*s),10)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;line-height:1.6;background:transparent;")

        self._card.apply_scale(s)

        h_btn  = max(round(160*s), 54)
        fs_btn = max(round(36*s),  13)
        self._retry_btn.setFixedHeight(h_btn)
        self._retry_btn.setStyleSheet(
            f"QPushButton{{background:{C_FOREST};color:{C_FOREST_T};border:none;"
            f"font-size:{fs_btn}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:500;letter-spacing:{max(round(4*s),1)}px;}}"
            f"QPushButton:hover{{background:#364D3A;}}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    sample_order = {
        "seat": "3", "product": "에어포스 1 '07", "brand": "나이키",
        "price": 129000, "color": "White", "size": "260",
    }
    win = TryonAnotherPage(
        order=sample_order,
        on_home=lambda: print("→ Home"),
        on_retry=lambda: print("→ TryonPage"),
    )
    win.show()
    sys.exit(app.exec())
