"""
moosinsa_tryon_delivery.py

- SShoopy 로봇 아이콘이 프로그레스바 위를 이동
- FMS에서 받은 진행거리/총거리로 퍼센트 갱신
- 요청 정보(좌석·상품·사이즈) 표시
- 도착 응답 수신 시 on_arrived 콜백 자동 호출
"""
import sys
import math
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QSizePolicy
)
from PySide6.QtCore import (
    Qt, QByteArray, QTimer, QPropertyAnimation,
    QEasingCurve, Property, QObject, Signal
)
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPainterPath
from PySide6.QtSvgWidgets import QSvgWidget

# ── Reference resolution ─────────────────────────────────────
REF_W, REF_H = 1080, 1920

# ── Palette ──────────────────────────────────────────────────
C_BG       = "#EDE9E3"
C_DARK     = "#1C1C1C"
C_FOREST   = "#2C3D30"
C_FOREST_T = "#C8DDB8"
C_BROWN    = "#5C4A3A"
C_BORDER   = "#D6D1C9"
C_SUB      = "#999999"
C_TRACK    = "#D6D1C9"   # 프로그레스 트랙 색
C_FILL     = "#2C3D30"   # 프로그레스 채움 색

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

# SShoopy 로봇 아이콘 (단순화된 AMR 실루엣)
SVG_ROBOT = """<svg viewBox="0 0 56 40" xmlns="http://www.w3.org/2000/svg">
  <!-- 몸체 -->
  <rect x="8" y="10" width="40" height="22" rx="5" fill="{body}"/>
  <!-- 눈 (LED) -->
  <rect x="16" y="17" width="6" height="5" rx="2" fill="{eye}"/>
  <rect x="34" y="17" width="6" height="5" rx="2" fill="{eye}"/>
  <!-- 안테나 -->
  <line x1="28" y1="10" x2="28" y2="4" stroke="{body}" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="28" cy="3" r="2.5" fill="{eye}"/>
  <!-- 바퀴 -->
  <circle cx="14" cy="33" r="5" fill="{wheel}"/>
  <circle cx="14" cy="33" r="2.2" fill="{eye}"/>
  <circle cx="42" cy="33" r="5" fill="{wheel}"/>
  <circle cx="42" cy="33" r="2.2" fill="{eye}"/>
</svg>"""

# 출발 아이콘 (창고)
SVG_START = """<svg viewBox="0 0 28 28" fill="none"
  stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M3 13L14 4l11 9"/>
  <path d="M5 11v13h6v-6h6v6h6V11"/>
</svg>"""

# 도착 아이콘 (의자/좌석)
SVG_END = """<svg viewBox="0 0 28 28" fill="none"
  stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <rect x="6" y="10" width="16" height="10" rx="2"/>
  <path d="M6 20v4M22 20v4"/>
  <path d="M6 10V7a2 2 0 014 0v3"/>
  <path d="M18 10V7a2 2 0 014 0v3"/>
</svg>"""


def make_svg(tpl: str, color: str, w: int, h: int = 0) -> QSvgWidget:
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background: transparent;")
    return wgt


def make_robot_svg(w: int, h: int, body=C_FOREST, eye=C_FOREST_T, wheel=C_DARK) -> QSvgWidget:
    data = SVG_ROBOT.format(body=body, eye=eye, wheel=wheel).encode()
    wgt = QSvgWidget()
    wgt.load(QByteArray(data))
    wgt.setFixedSize(w, h)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ════════════════════════════════════════════════════════════
#  Progress bar + robot widget
# ════════════════════════════════════════════════════════════
class DeliveryProgressBar(QWidget):
    """
    트랙 위를 SShoopy 로봇이 이동하는 프로그레스 바.
    set_progress(0.0 ~ 1.0) 으로 갱신.
    내부적으로 QPropertyAnimation으로 부드럽게 이동.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._progress   = 0.0   # 0.0 ~ 1.0
        self._anim_val   = 0.0   # 현재 렌더 값 (애니메이션)
        self._s          = 0.5
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def update_order(self, order: dict):                  # ★ NEW ★
        """
        PageManager가 시착 요청 성공 후 order 정보를 주입한다.
        배송 추적 UI를 새 order 기준으로 초기화한다.
        """
        self._order    = order
        self._progress = 0.0
        self._arrived  = False
        self._progress_bar.set_progress(0.0, animated=False)
        self._stage_row.update_stage(0.0)
        self._status_lbl.setText("SShoopy 가 상품을 가져오는 중이에요..")
        # OrderInfoWidget 재생성
        self._order_info.deleteLater()
        self._order_info = OrderInfoWidget(order)
        self._body_lo.insertWidget(
            self._body_lo.count() - 1,   # addStretch(2) 바로 앞
            self._order_info
        )
        self._order_info.apply_scale(self._s)

    # ── Public API ───────────────────────────────────────────
    def set_progress(self, value: float, animated: bool = True):
        """0.0 ~ 1.0 사이 값으로 진행률 갱신."""
        value = max(0.0, min(1.0, value))
        self._progress = value
        if animated:
            self._animate_to(value)
        else:
            self._anim_val = value
            self.update()

    def apply_scale(self, s: float):
        self._s = s
        track_h  = max(round(10 * s), 4)
        robot_h  = max(round(60 * s), 22)
        icon_h   = max(round(32 * s), 12)
        total_h  = robot_h + max(round(16 * s), 6) + track_h + icon_h + max(round(8 * s), 3)
        self.setFixedHeight(total_h)
        self.update()

    # ── Animation ────────────────────────────────────────────
    def _animate_to(self, target: float):
        steps   = max(round(abs(target - self._anim_val) * 30), 1)
        start   = self._anim_val
        delta   = (target - start) / steps
        self._steps_left = steps
        self._delta      = delta
        self._timer      = QTimer(self)
        self._timer.setInterval(16)   # ~60fps
        self._timer.timeout.connect(self._step)
        self._timer.start()

    def _step(self):
        self._steps_left -= 1
        self._anim_val   += self._delta
        if self._steps_left <= 0:
            self._anim_val = self._progress
            self._timer.stop()
        self.update()

    # ── Paint ────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        s = self._s

        w = self.width()
        track_h  = max(round(10 * s), 4)
        robot_w  = max(round(80 * s), 28)
        robot_h  = max(round(52 * s), 18)
        icon_sz  = max(round(28 * s), 10)
        v_gap    = max(round(12 * s), 4)
        h_pad    = max(round(40 * s), 14)   # 좌우 여백 (아이콘 중앙 기준)

        # 트랙 Y 위치
        track_y  = robot_h + v_gap + track_h // 2

        # 트랙 좌우 경계 (아이콘 중앙 기준)
        track_x0 = h_pad
        track_x1 = w - h_pad
        track_len = track_x1 - track_x0

        # ── 트랙 배경 ──
        pen = QPen(QColor(C_TRACK))
        pen.setWidth(track_h)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(int(track_x0), int(track_y), int(track_x1), int(track_y))

        # ── 채워진 트랙 ──
        fill_x1 = track_x0 + track_len * self._anim_val
        if fill_x1 > track_x0:
            pen2 = QPen(QColor(C_FILL))
            pen2.setWidth(track_h)
            pen2.setCapStyle(Qt.RoundCap)
            painter.setPen(pen2)
            painter.drawLine(int(track_x0), int(track_y), int(fill_x1), int(track_y))

        # ── 출발·도착 아이콘 (트랙 아래) ──
        icon_y = track_y + track_h // 2 + max(round(8 * s), 3)

        # 출발 (창고)
        self._draw_svg_icon(painter, SVG_START,
                            C_SUB if self._anim_val < 0.99 else C_FILL,
                            int(track_x0 - icon_sz // 2), int(icon_y), icon_sz)
        # 도착 (좌석)
        self._draw_svg_icon(painter, SVG_END,
                            C_FILL if self._anim_val >= 0.99 else C_SUB,
                            int(track_x1 - icon_sz // 2), int(icon_y), icon_sz)

        # ── 로봇 아이콘 ──
        robot_x = int(track_x0 + track_len * self._anim_val - robot_w // 2)
        robot_x = max(int(track_x0) - robot_w // 2,
                      min(robot_x, int(track_x1) - robot_w // 2))
        robot_y = 0

        svg_data = SVG_ROBOT.format(
            body=C_FOREST, eye=C_FOREST_T, wheel=C_DARK).encode()
        from PySide6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(QByteArray(svg_data))
        from PySide6.QtCore import QRectF
        renderer.render(painter, QRectF(robot_x, robot_y, robot_w, robot_h))

        painter.end()

    def _draw_svg_icon(self, painter, tpl, color, x, y, size):
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtCore import QRectF
        data = tpl.format(color=color).encode()
        renderer = QSvgRenderer(QByteArray(data))
        renderer.render(painter, QRectF(x, y, size, size))


# ════════════════════════════════════════════════════════════
#  Stage label row (출발 / 이동중 / 도착)
# ════════════════════════════════════════════════════════════
class StageRow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._s = 0.5  # [시착요청연동] update_stage 호출 전 apply_scale 미호출 시 AttributeError 방지
        self.setStyleSheet("background: transparent;")
        lo = QHBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        self._start_lbl  = QLabel("출발")
        self._mid_lbl    = QLabel("이동중")
        self._end_lbl    = QLabel("도착")
        self._start_lbl.setAlignment(Qt.AlignLeft)
        self._mid_lbl.setAlignment(Qt.AlignCenter)
        self._end_lbl.setAlignment(Qt.AlignRight)
        lo.addWidget(self._start_lbl)
        lo.addStretch()
        lo.addWidget(self._mid_lbl)
        lo.addStretch()
        lo.addWidget(self._end_lbl)
        self._labels = [self._start_lbl, self._mid_lbl, self._end_lbl]

    def update_stage(self, progress: float):
        """진행률에 따라 활성 단계 강조."""
        if progress < 0.05:
            active = 0
        elif progress < 0.98:
            active = 1
        else:
            active = 2
        for i, lbl in enumerate(self._labels):
            lbl.setProperty("active", i == active)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        self._apply_style(self._s, active)

    def apply_scale(self, s: float):
        self._s = s
        self._apply_style(s, 1)

    def _apply_style(self, s: float, active_idx: int):
        for i, lbl in enumerate(self._labels):
            is_active = (i == active_idx)
            lbl.setStyleSheet(
                f"color:{C_DARK if is_active else C_SUB};"
                f"font-size:{max(round(28*s if is_active else 24*s),9)}px;"
                f"font-family:'Helvetica Neue',Arial,sans-serif;"
                f"font-weight:{'600' if is_active else '300'};"
                f"background:transparent;")


# ════════════════════════════════════════════════════════════
#  Order info table
# ════════════════════════════════════════════════════════════
class OrderInfoWidget(QWidget):
    def __init__(self, order: dict, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_BG};")
        self._order = order
        self._lo    = QVBoxLayout(self)
        self._lo.setSpacing(0)

        rows = [
            ("요청 좌석", str(order.get("seat", "—"))),
            ("상품",     order.get("product", "—")),
            ("색상",     order.get("color", "—")),
            ("사이즈",   order.get("size", "—")),
        ]
        self._rows: list[tuple[QLabel, QLabel]] = []
        for i, (key, val) in enumerate(rows):
            row_w = QWidget()
            bg = "#E8E3DC" if i % 2 == 0 else C_BG
            row_w.setStyleSheet(f"background:{bg};border:none;")
            row_lo = QHBoxLayout(row_w)
            k_lbl = QLabel(key)
            v_lbl = QLabel(val)
            k_lbl.setStyleSheet("background:transparent;")
            v_lbl.setStyleSheet("background:transparent;")
            v_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_lo.addWidget(k_lbl)
            row_lo.addStretch()
            row_lo.addWidget(v_lbl)
            self._lo.addWidget(row_w)
            self._rows.append((row_w, k_lbl, v_lbl))

    def apply_scale(self, s: float):
        self._lo.setSpacing(0)
        hm = max(round(50 * s), 16)
        vm = max(round(18 * s), 6)
        fs_k = max(round(26 * s), 9)
        fs_v = max(round(28 * s), 10)
        r_h  = max(round(88 * s), 30)
        for row_w, k_lbl, v_lbl in self._rows:
            row_w.setFixedHeight(r_h)
            row_w.layout().setContentsMargins(hm, vm, hm, vm)
            k_lbl.setStyleSheet(
                f"color:{C_SUB};font-size:{fs_k}px;"
                f"font-family:'Helvetica Neue',Arial;font-weight:300;"
                f"background:transparent;")
            v_lbl.setStyleSheet(
                f"color:{C_DARK};font-size:{fs_v}px;"
                f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
                f"font-weight:500;background:transparent;")


# ════════════════════════════════════════════════════════════
#  Top bar
# ════════════════════════════════════════════════════════════
class TopBar(QFrame):
    """delivery 화면 전용 — 로봇 배송 중에는 홈/뒤로가기 없이 브랜드명만 표시."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color:{C_DARK};border:none;")
        self._lo = QHBoxLayout(self)
        self._brand = QLabel("MOOSINSA")
        self._brand.setAlignment(Qt.AlignCenter)
        self._lo.addStretch()
        self._lo.addWidget(self._brand)
        self._lo.addStretch()

    def apply_scale(self, s):
        self.setFixedHeight(max(round(130 * s), 44))
        hm = max(round(40 * s), 12)
        self._lo.setContentsMargins(hm, 0, hm, 0)
        self._brand.setStyleSheet(
            f"color:{C_BG};font-size:{max(round(44*s),14)}px;"
            f"font-family:'Georgia',serif;font-weight:500;"
            f"letter-spacing:{max(round(12*s),3)}px;background:transparent;")


# ════════════════════════════════════════════════════════════
#  Main delivery tracking page
# ════════════════════════════════════════════════════════════
class TryonDeliveryPage(QWidget):             # ★ CHANGED: QMainWindow → QWidget ★
    """
    Parameters
    ----------
    order      : dict  – 시착 요청 데이터 {"seat","product","color","size"}
    on_home    : callable
    on_back    : callable  # ★ CHANGED ★
    on_arrived : callable – 도착 시 자동 호출 (수령완료 화면 전환용)
    """

    def __init__(
        self,
        order: dict = None,
        on_home=None,
        on_arrived=None,
        api_client=None,          # [시착요청연동] KioskApiClient 인스턴스
    ):
        super().__init__()

        self._order      = order or {
            "seat": "3", "product": "에어포스 1 '07",
            "color": "White", "size": "260"
        }
        self._on_home    = on_home    or (lambda: None)
        self._on_arrived = on_arrived or (lambda: print("→ 수령완료 화면"))
        self._api        = api_client  # [시착요청연동]
        self._s          = 0.5
        self._progress   = 0.0
        self._arrived    = False
        self._robot_id   = "sshopy2"   # [시착요청연동] update_order 시 갱신
        self._poll_pending = False      # [시착요청연동] 폴링 중복 방지 플래그

        self.setStyleSheet(f"background:{C_BG};")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ── Top bar ── (배송 중: 홈/뒤로가기 없음)
        self._topbar = TopBar()
        self._root.addWidget(self._topbar)

        # ── Content ──
        self._body = QWidget()
        self._body.setStyleSheet(f"background:{C_BG};")
        self._body_lo = QVBoxLayout(self._body)
        self._root.addWidget(self._body, stretch=1)

        # 안내 문구
        self._status_lbl = QLabel("SShoopy 가 상품을 가져오는 중이에요..")
        self._status_lbl.setAlignment(Qt.AlignLeft)
        self._status_lbl.setWordWrap(True)
        self._body_lo.addWidget(self._status_lbl)

        self._body_lo.addStretch(1)

        # 프로그레스 바
        self._progress_bar = DeliveryProgressBar()
        self._body_lo.addWidget(self._progress_bar)

        self._body_lo.addSpacing(8)

        # 단계 레이블 (출발 / 이동중 / 도착)
        self._stage_row = StageRow()
        self._body_lo.addWidget(self._stage_row)

        self._body_lo.addStretch(1)

        # 주문 정보
        self._order_info = OrderInfoWidget(self._order)
        self._body_lo.addWidget(self._order_info)

        self._body_lo.addStretch(2)

    def update_order(self, order: dict):                  # ★ NEW ★
        """
        PageManager가 시착 요청 성공 후 order 정보를 주입한다.
        배송 추적 UI를 새 order 기준으로 초기화한다.
        """
        self._order    = order
        self._progress = 0.0
        self._arrived  = False
        self._robot_id = order.get("robot_id", "sshopy2")  # [시착요청연동]
        self._progress_bar.set_progress(0.0, animated=False)
        self._stage_row.update_stage(0.0)
        self._status_lbl.setText("SShoopy 가 상품을 가져오는 중이에요..")
        # OrderInfoWidget 재생성
        self._order_info.deleteLater()
        self._order_info = OrderInfoWidget(order)
        self._body_lo.insertWidget(
            self._body_lo.count() - 1,   # addStretch(2) 바로 앞
            self._order_info
        )
        self._order_info.apply_scale(self._s)
        # [시착요청연동] 진행률 폴링 시작
        if self._api:
            self._start_polling()

    # ── Public API ───────────────────────────────────────────
    def update_progress(self, traveled: float, total: float):
        """
        FMS 데이터로 진행률 갱신.
        traveled : 이동한 거리
        total    : 전체 거리
        """
        if total <= 0:
            return
        pct = max(0.0, min(1.0, traveled / total))
        self._progress = pct
        self._progress_bar.set_progress(pct)
        self._stage_row.update_stage(pct)

        if pct < 0.98:
            self._status_lbl.setText("SShoopy 가 상품을 가져오는 중이에요..")
        else:
            self._status_lbl.setText("SShoopy 가 도착했어요! 상품을 수령해 주세요.")
            if not self._arrived:
                self._arrived = True
                # 도착 콜백 — 수령완료 화면으로 자동 전환
                QTimer.singleShot(1200, self._on_arrived)

    def notify_arrived(self):
        """
        백엔드에서 도착 응답을 수신했을 때 직접 호출.
        (폴링 or WebSocket 콜백에서 연결)
        """
        self.update_progress(1.0, 1.0)

    # ── 진행률 폴링 ──────────────────────────────────────────

    def _start_polling(self):
        """[시착요청연동] 1초 간격으로 /kiosk/tryon/progress 폴링 시작."""
        if not hasattr(self, "_poll_timer"):
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(1000)
            self._poll_timer.timeout.connect(self._poll_progress)
        else:
            self._poll_timer.stop()
        self._poll_pending = False
        self._poll_timer.start()

    def _poll_progress(self):
        """[시착요청연동] 진행률 단발 폴링 — 이전 요청 처리 중이면 스킵."""
        if self._arrived:
            self._poll_timer.stop()
            return
        if self._poll_pending:
            return
        self._poll_pending = True
        robot_id = self._robot_id

        def _on_progress(data):
            self._poll_pending = False
            if data is None:
                return
            progress_pct = data.get("progress_pct", 0.0)
            arrived      = data.get("arrived", False)
            self.update_progress(progress_pct, 1.0)
            if arrived and hasattr(self, "_poll_timer"):
                self._poll_timer.stop()

        self._api.poll_tryon_progress(robot_id, callback=_on_progress)

    # ── Navigation ───────────────────────────────────────────
    def _go_home(self):
        self._on_home()

    # ── Resize / scale ───────────────────────────────────────
    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_body_scale(s)

    def _apply_body_scale(self, s: float):
        hm = max(round(60 * s), 20)
        vm = max(round(50 * s), 16)
        self._body_lo.setContentsMargins(hm, vm, hm, vm)
        self._body_lo.setSpacing(max(round(16 * s), 5))

        # 안내 문구
        self._status_lbl.setStyleSheet(
            f"color:{C_DARK};font-size:{max(round(36*s),13)}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;background:transparent;")

        # 프로그레스 바
        self._progress_bar.apply_scale(s)

        # 단계 레이블
        hm2 = max(round(40 * s), 14)
        self._stage_row.setContentsMargins(hm2, 0, hm2, 0)
        self._stage_row.apply_scale(s)

        # 주문 정보
        self._order_info.apply_scale(s)


# ════════════════════════════════════════════════════════════
#  Entry point + demo timer
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    sample_order = {
        "seat":    "3",
        "product": "에어포스 1 '07",
        "color":   "White",
        "size":    "260",
    }

    def on_arrived():
        print("→ [도착 완료] 수령완료 화면으로 전환")
        # 실제 구현: win.close(); receipt_win.show()

    win = TryonDeliveryPage(
        order=sample_order,
        on_home=lambda: print("→ Home"),
        on_arrived=on_arrived,
    )
    win.show()

    # ── Demo: 3초마다 진행률 +15% 자동 증가 ──────────────────
    _demo_progress = [0.0]

    def demo_tick():
        _demo_progress[0] = min(_demo_progress[0] + 0.12, 1.0)
        total = 100.0
        traveled = _demo_progress[0] * total
        win.update_progress(traveled, total)

    demo_timer = QTimer()
    demo_timer.setInterval(2000)
    demo_timer.timeout.connect(demo_tick)
    demo_timer.start()

    sys.exit(app.exec())
