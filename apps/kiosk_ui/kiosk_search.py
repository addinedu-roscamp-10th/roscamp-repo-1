"""
moosinsa_search.py
- 한글 두벌식 조합 엔진 내장 (자모 → 완성형 유니코드)
- 키보드 행간 gap = 0
- 안내문구 + 입력창 세로 중앙 배치
"""
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QLineEdit, QSizePolicy, QSpacerItem
)
from PySide6.QtCore import Qt, QByteArray, QTimer
from PySide6.QtSvgWidgets import QSvgWidget

# ── Reference resolution ─────────────────────────────────────
REF_W, REF_H = 1080, 1920

# ── Palette ──────────────────────────────────────────────────
C_BG      = "#EDE9E3"
C_DARK    = "#1C1C1C"
C_BROWN   = "#5C4A3A"
C_BROWN_H = "#6E5A48"
C_BORDER  = "#D6D1C9"
C_SUB     = "#999999"
C_KEY_BG  = "#F5F1EC"
C_KEY_SP  = "#D9D4CD"
C_KEY_HOV = "#E2DDD6"

# ── SVG ──────────────────────────────────────────────────────
SVG_HOME = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M4 14L16 4l12 10"/>
  <path d="M6 12v14h7v-7h6v7h7V12"/>
</svg>"""

SVG_ENTER = """<svg viewBox="0 0 28 20" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M24 4v6a2 2 0 01-2 2H5"/>
  <polyline points="9,7 4,12 9,17"/>
</svg>"""

SVG_BACK = """<svg viewBox="0 0 32 32" fill="none"
  stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
  xmlns="http://www.w3.org/2000/svg">
  <path d="M20 6L8 16l12 10"/>
</svg>"""


def make_svg(tpl: str, color: str, w: int, h: int = 0) -> QSvgWidget:
    wgt = QSvgWidget()
    wgt.load(QByteArray(tpl.format(color=color).encode()))
    wgt.setFixedSize(w, h if h else w)
    wgt.setStyleSheet("background: transparent;")
    return wgt


# ════════════════════════════════════════════════════════════
#  한글 두벌식 조합 엔진
# ════════════════════════════════════════════════════════════
# 초성 리스트 (19개)
CHOSEONG = [
    'ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ',
    'ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'
]
# 중성 리스트 (21개)
JUNGSEONG = [
    'ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ','ㅙ','ㅚ',
    'ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ'
]
# 종성 리스트 (28개, 0=없음)
JONGSEONG = [
    '','ㄱ','ㄲ','ㄳ','ㄴ','ㄵ','ㄶ','ㄷ','ㄹ','ㄺ','ㄻ','ㄼ','ㄽ','ㄾ','ㄿ','ㅀ',
    'ㅁ','ㅂ','ㅄ','ㅅ','ㅆ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'
]

# 자음 집합
CONSONANTS = set('ㄱㄲㄳㄴㄵㄶㄷㄸㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅃㅄㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ')
VOWELS     = set('ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ')

# 복합 모음 조합 (ㅗ+ㅏ→ㅘ 등)
VOWEL_COMBINE = {
    ('ㅗ','ㅏ'):'ㅘ', ('ㅗ','ㅐ'):'ㅙ', ('ㅗ','ㅣ'):'ㅚ',
    ('ㅜ','ㅓ'):'ㅝ', ('ㅜ','ㅔ'):'ㅞ', ('ㅜ','ㅣ'):'ㅟ',
    ('ㅡ','ㅣ'):'ㅢ',
}
# 복합 자음 (종성 ㄱ+ㅅ→ㄳ 등)
CONS_COMBINE = {
    ('ㄱ','ㅅ'):'ㄳ', ('ㄴ','ㅈ'):'ㄵ', ('ㄴ','ㅎ'):'ㄶ',
    ('ㄹ','ㄱ'):'ㄺ', ('ㄹ','ㅁ'):'ㄻ', ('ㄹ','ㅂ'):'ㄼ',
    ('ㄹ','ㅅ'):'ㄽ', ('ㄹ','ㅌ'):'ㄾ', ('ㄹ','ㅍ'):'ㄿ',
    ('ㄹ','ㅎ'):'ㅀ', ('ㅂ','ㅅ'):'ㅄ',
}
# 복합 종성 분리 (모음이 들어올 때)
CONS_SPLIT = {v: k for k, v in CONS_COMBINE.items()}


def cho_idx(c):  return CHOSEONG.index(c)  if c in CHOSEONG  else -1
def jung_idx(v): return JUNGSEONG.index(v) if v in JUNGSEONG else -1
def jong_idx(c): return JONGSEONG.index(c) if c in JONGSEONG else -1


def compose(cho, jung, jong=''):
    """자모 → 완성형 유니코드 한 글자"""
    ci = cho_idx(cho); vi = jung_idx(jung); ji = jong_idx(jong)
    if ci < 0 or vi < 0 or ji < 0:
        return cho + jung + jong
    return chr(0xAC00 + ci * 21 * 28 + vi * 28 + ji)


# ── 검색어 검증 ───────────────────────────────────────────   # ★ NEW ★
MIN_QUERY_LEN = 2   # 최소 글자 수

# 완성형 한글 범위: AC00–D7A3
_HAN_START, _HAN_END = 0xAC00, 0xD7A3
# 자모 단독 범위: 초성(1100–11FF), 호환자모(3130–318F)
_JAMO_RANGES = [(0x1100, 0x11FF), (0x3130, 0x318F)]


def _is_complete_hangul(ch: str) -> bool:
    """완성형 한글 한 글자인지 확인."""
    return _HAN_START <= ord(ch) <= _HAN_END


def _is_jamo_only(ch: str) -> bool:
    """자모 단독 문자(초성/중성/종성)인지 확인."""
    cp = ord(ch)
    return any(s <= cp <= e for s, e in _JAMO_RANGES)


def _is_alphanumeric(ch: str) -> bool:
    """영문자 또는 숫자인지 확인."""
    return ch.isascii() and (ch.isalpha() or ch.isdigit())


def validate_query(query: str) -> str | None:
    """                                                         # ★ NEW ★
    검색어 유효성 검사.
    유효하면 None 반환, 유효하지 않으면 사용자에게 보여줄 에러 문자열 반환.

    검사 순서:
      1. 빈 입력 / 공백만
      2. 자모만 (미완성 한글)
      3. 특수문자만 (한글·영문·숫자 없음)
      4. 두 글자 미만
    """
    q = query.strip()

    # 1) 빈 입력
    if not q:
        return "검색어를 입력해 주세요."

    # 2) 자모만 — 모든 글자가 단독 자모인 경우
    if all(_is_jamo_only(ch) for ch in q if not ch.isspace()):
        return "완성된 글자로 입력해 주세요.\n(예: 나이키, 런닝화)"

    # 3) 특수문자만 — 한글(완성형+자모)·영문·숫자가 하나도 없는 경우
    has_meaningful = any(
        _is_complete_hangul(ch) or _is_jamo_only(ch) or _is_alphanumeric(ch)
        for ch in q
    )
    if not has_meaningful:
        return "검색어에 한글 또는 영문·숫자를 포함해 주세요."

    # 4) 두 글자 미만 (공백 제외 기준)
    meaningful_chars = [ch for ch in q if not ch.isspace()]
    if len(meaningful_chars) < MIN_QUERY_LEN:
        return f"두 글자 이상 입력해 주세요."

    return None   # 유효


class HangulComposer:
    """
    두벌식 입력기 상태 머신.
    push(key) → 현재까지 완성된 문자열 반환
    backspace() → 한 단계 되돌리기
    flush() → 조합 중인 글자 확정 후 반환
    """
    def __init__(self):
        self._committed = ""   # 확정된 문자열
        self._cho   = ""       # 현재 초성
        self._jung  = ""       # 현재 중성
        self._jong  = ""       # 현재 종성 (단일 or 복합)
        self._state = 0
        # state: 0=비어있음 1=초성 2=초+중 3=초+중+종

    def reset(self):                                       # ★ NEW ★
        """조합기 상태와 확정 문자열을 모두 초기화한다."""
        self._committed = ""
        self._cho = ""
        self._jung = ""
        self._jong = ""
        self._state = 0

    def push(self, key: str) -> str:
        if key in VOWELS:
            self._push_vowel(key)
        elif key in CONSONANTS:
            self._push_consonant(key)
        else:
            # 숫자·특수문자: 조합 중인 글자 확정 후 추가
            self._committed += self._current_char() + key
            self._reset_state()
        return self.text()

    def backspace(self) -> str:
        if self._state == 3:
            if self._jong in CONS_SPLIT:
                # 복합 종성 → 앞 자음만 남김
                self._jong = CONS_SPLIT[self._jong][0]
            else:
                self._jong = ""
                self._state = 2
        elif self._state == 2:
            if self._jung in {v for k, v in VOWEL_COMBINE.items()}:
                # 복합 모음 → 앞 모음만 남김
                for (a, b), c in VOWEL_COMBINE.items():
                    if c == self._jung:
                        self._jung = a
                        break
            else:
                self._jung = ""
                self._state = 1
        elif self._state == 1:
            self._cho = ""
            self._state = 0
        elif self._committed:
            last = self._committed[-1]
            self._committed = self._committed[:-1]
            code = ord(last)
            if 0xAC00 <= code <= 0xD7A3:
                # 완성형 분해
                code -= 0xAC00
                ji = code % 28; code //= 28
                vi = code % 21; ci = code // 21
                self._cho  = CHOSEONG[ci]
                self._jung = JUNGSEONG[vi]
                self._jong = JONGSEONG[ji]
                self._state = 3 if ji > 0 else 2
            else:
                # 단독 자모 or 영문/숫자
                pass
        return self.text()

    def flush(self) -> str:
        self._committed += self._current_char()
        self._reset_state()
        return self.text()

    def text(self) -> str:
        return self._committed + self._current_char()

    def _current_char(self) -> str:
        if self._state == 0: return ""
        if self._state == 1: return self._cho
        if self._state == 2: return compose(self._cho, self._jung)
        if self._state == 3: return compose(self._cho, self._jung, self._jong)
        return ""

    def _reset_state(self):
        self._cho = self._jung = self._jong = ""
        self._state = 0

    def _push_vowel(self, v: str):
        if self._state == 0:
            # 독립 모음
            self._committed += v
        elif self._state == 1:
            # 초성 + 모음 → 초+중
            self._jung = v; self._state = 2
        elif self._state == 2:
            # 복합 모음 시도
            combined = VOWEL_COMBINE.get((self._jung, v))
            if combined:
                self._jung = combined
            else:
                # 현재 글자 확정 후 새 모음 독립
                self._committed += compose(self._cho, self._jung)
                self._reset_state()
                self._committed += v
        elif self._state == 3:
            # 종성이 있을 때 모음 → 종성을 다음 글자 초성으로 분리
            if self._jong in CONS_SPLIT:
                j1, j2 = CONS_SPLIT[self._jong]
                self._committed += compose(self._cho, self._jung, j1)
                self._cho = j2; self._jung = v; self._jong = ""; self._state = 2
            else:
                next_cho = self._jong
                self._committed += compose(self._cho, self._jung)
                self._cho = next_cho; self._jung = v; self._jong = ""; self._state = 2

    def _push_consonant(self, c: str):
        if self._state == 0:
            self._cho = c; self._state = 1
        elif self._state == 1:
            # 연속 자음: 이전 확정 후 새 초성
            self._committed += self._cho
            self._cho = c
        elif self._state == 2:
            # 초+중 이후 자음 → 종성 후보
            self._jong = c; self._state = 3
        elif self._state == 3:
            # 복합 종성 시도
            combined = CONS_COMBINE.get((self._jong, c))
            if combined:
                self._jong = combined
            else:
                # 현재 글자 확정, 새 초성
                self._committed += compose(self._cho, self._jung, self._jong)
                self._reset_state()
                self._cho = c; self._state = 1


# ── Keyboard layout ───────────────────────────────────────────
KO_ROWS_NORMAL = [
    ["ㅂ","ㅈ","ㄷ","ㄱ","ㅅ","ㅛ","ㅕ","ㅑ","ㅐ","ㅔ"],
    ["ㅁ","ㄴ","ㅇ","ㄹ","ㅎ","ㅗ","ㅓ","ㅏ","ㅣ"],
    ["SHIFT","ㅋ","ㅌ","ㅊ","ㅍ","ㅠ","ㅜ","ㅡ","⌫"],
    ["!#1"," ","한/영"],
]
KO_ROWS_SHIFT = [
    ["ㅃ","ㅉ","ㄸ","ㄲ","ㅆ","ㅛ","ㅕ","ㅑ","ㅒ","ㅖ"],
    ["ㅁ","ㄴ","ㅇ","ㄹ","ㅎ","ㅗ","ㅓ","ㅏ","ㅣ"],
    ["SHIFT","ㅋ","ㅌ","ㅊ","ㅍ","ㅠ","ㅜ","ㅡ","⌫"],
    ["!#1"," ","한/영"],
]
NUM_ROWS = [
    ["1","2","3","4","5","6","7","8","9","0"],
    ["-","/",":",";","(",")", "₩","&","@","\""],
    ["#+=",".",",","?","!","'","~","<",">","⌫"],
    ["한글"," ","한/영"],
]
SPECIAL_KEYS = {"SHIFT","⌫"," ","!#1","#+=","한글","한/영"}


# ── Top bar ───────────────────────────────────────────────────
class TopBar(QFrame):
    def __init__(self, on_home, on_back, parent=None):  # ★ CHANGED ★
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {C_DARK}; border: none;")
        self._lo = QHBoxLayout(self)
        self._home_btn = QPushButton()
        self._home_btn.setCursor(Qt.PointingHandCursor)
        self._home_btn.clicked.connect(on_home)
        self._home_btn.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self._home_icon = make_svg(SVG_HOME, C_BG, 28)
        hl = QHBoxLayout(self._home_btn)
        hl.setContentsMargins(0,0,0,0)
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
        self.setFixedHeight(max(round(130*s),44))
        hm = max(round(40*s),12)
        self._lo.setContentsMargins(hm,0,hm,0)
        icon_sz = max(round(54*s),20); self._home_icon.setFixedSize(icon_sz,icon_sz)
        btn_sz  = max(round(100*s),36); self._home_btn.setFixedSize(btn_sz,btn_sz)
        self._brand.setStyleSheet(f"color:{C_BG};font-size:{max(round(44*s),14)}px;"
            f"font-family:'Georgia',serif;font-weight:500;"
            f"letter-spacing:{max(round(12*s),3)}px;background:transparent;")
        bsz = max(round(100*s), 36)                        # ★ CHANGED ★
        self._back_btn.setFixedSize(bsz, bsz)
        bisz = max(round(44*s), 18)
        self._back_icon.setFixedSize(bisz, bisz)


# ── Virtual keyboard ──────────────────────────────────────────
class VirtualKeyboard(QFrame):
    def __init__(self, on_key, parent=None):
        super().__init__(parent)
        self._on_key  = on_key
        self._shifted = False
        self._nummode = False
        self._s       = 0.5
        self.setStyleSheet(f"background-color:{C_KEY_SP};border:none;")
        self._outer = QVBoxLayout(self)
        self._outer.setSpacing(3)          # ← 행간 완전히 0
        self._outer.setContentsMargins(0,0,0,0)
        self._row_widgets: list[QWidget] = []
        self._build()

    def _rows(self):
        if self._nummode:   return NUM_ROWS
        if self._shifted:   return KO_ROWS_SHIFT
        return KO_ROWS_NORMAL

    def _build(self):
        for w in self._row_widgets:
            w.deleteLater()
        self._row_widgets.clear()

        s        = self._s
        key_h    = max(round(116 * s), 38)
        h_gap    = max(round(6  * s), 2)    # 키 좌우 간격만 살짝
        h_pad    = max(round(10 * s), 3)
        v_pad    = max(round(8  * s), 2)
        radius   = max(round(10 * s), 4)
        fs       = max(round(30 * s), 11)
        fs_sm    = max(round(24 * s), 9)

        self._outer.setContentsMargins(h_pad, v_pad, h_pad, v_pad)
        self._outer.setSpacing(3)           # ← 확실히 0 유지

        for row_keys in self._rows():
            row_w = QWidget()
            row_w.setStyleSheet("background:transparent;")
            row_lo = QHBoxLayout(row_w)
            row_lo.setContentsMargins(0,0,0,0)
            row_lo.setSpacing(h_gap)

            for key in row_keys:
                btn = QPushButton()
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(key_h)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

                if key == " ":
                    btn.setText("space")
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_BG};color:{C_SUB};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs_sm}px;font-family:'Helvetica Neue',Arial;"
                        f"font-weight:300;}}"
                        f"QPushButton:hover{{background:{C_KEY_HOV};}}")
                    btn.clicked.connect(lambda: self._on_key(" "))

                elif key == "⌫":
                    back_lo = QHBoxLayout(btn)
                    back_lo.setContentsMargins(0,0,0,0)
                    bw = max(round(28*s),10); bh = max(round(20*s),8)
                    back_icon = make_svg(SVG_BACK, C_DARK, bw, bh)
                    back_lo.addStretch(); back_lo.addWidget(back_icon); back_lo.addStretch()
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_SP};border:none;"
                        f"border-radius:{radius}px;}}"
                        f"QPushButton:hover{{background:{C_BORDER};}}")
                    btn.clicked.connect(lambda: self._on_key("⌫"))

                elif key == "SHIFT":
                    lbl = "⇩" if self._shifted else "⇧"
                    btn.setText(lbl)
                    bg  = C_DARK if self._shifted else C_KEY_SP
                    fg  = C_BG   if self._shifted else C_DARK
                    btn.setStyleSheet(
                        f"QPushButton{{background:{bg};color:{fg};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{max(round(32*s),11)}px;}}"
                        f"QPushButton:hover{{background:{C_BORDER};}}")
                    btn.clicked.connect(self._toggle_shift)

                elif key in ("!#1","#+=","한글"):
                    btn.setText(key)
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_SP};color:{C_DARK};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs_sm}px;font-family:'Helvetica Neue',Arial;"
                        f"font-weight:400;}}"
                        f"QPushButton:hover{{background:{C_BORDER};}}")
                    btn.clicked.connect(self._toggle_num)

                elif key == "한/영":
                    btn.setText("한/영")
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_BROWN};color:{C_BG};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs_sm}px;font-family:'Helvetica Neue',Arial;"
                        f"font-weight:400;}}"
                        f"QPushButton:hover{{background:{C_BROWN_H};}}")
                    btn.clicked.connect(lambda: self._on_key("한/영"))

                else:
                    btn.setText(key)
                    btn.setStyleSheet(
                        f"QPushButton{{background:{C_KEY_BG};color:{C_DARK};"
                        f"border:none;border-radius:{radius}px;"
                        f"font-size:{fs}px;"
                        f"font-family:'Apple SD Gothic Neo','Noto Sans KR',"
                        f"'Malgun Gothic',sans-serif;font-weight:400;}}"
                        f"QPushButton:hover{{background:{C_KEY_HOV};}}"
                        f"QPushButton:pressed{{background:{C_BORDER};}}")
                    k = key
                    btn.clicked.connect(lambda chk=False, c=k: self._on_key(c))

                row_lo.addWidget(btn)
            self._outer.addWidget(row_w)
            self._row_widgets.append(row_w)

    def _toggle_shift(self):
        self._shifted = not self._shifted
        self._build()

    def _toggle_num(self):
        self._nummode = not self._nummode
        self._shifted = False
        self._build()

    def apply_scale(self, s):
        self._s = s
        self._build()


# ── Search page ────────────────────────────────────────────────
class ShoeSearchPage(QWidget):                # ★ CHANGED: QMainWindow → QWidget ★
    def __init__(self, on_home=None, on_back=None, on_search=None):  # ★ CHANGED ★
        super().__init__()                         # ★ CHANGED ★

        self._on_home   = on_home   or (lambda: None)
        self._on_back   = on_back   or (lambda: None)   # ★ CHANGED ★
        self._on_search = on_search or (lambda q: print(f"검색: {q}"))
        self._s         = 0.5
        self._composer  = HangulComposer()

        self.setStyleSheet(f"background-color:{C_BG};")  # ★ CHANGED ★

        self._root = QVBoxLayout(self)             # ★ CHANGED ★
        self._root.setContentsMargins(0,0,0,0)
        self._root.setSpacing(0)

        # Top bar
        self._topbar = TopBar(on_home=self._go_home, on_back=self._on_back)  # ★ CHANGED ★
        self._root.addWidget(self._topbar)

        # ── 중앙 영역: stretch | prompt+input | stretch ──
        self._mid = QWidget()
        self._mid.setStyleSheet(f"background:{C_BG};")
        self._mid_lo = QVBoxLayout(self._mid)
        self._mid_lo.setContentsMargins(0,0,0,0)
        self._mid_lo.setSpacing(0)

        self._mid_lo.addStretch(2)   # 위 여백 (더 크게)

        # Prompt
        self._prompt = QLabel(
            "찾으시는 신발의 이름이나 브랜드,\n"
            "또는 원하시는 스타일을 입력해 주세요."
        )
        self._prompt.setAlignment(Qt.AlignCenter)
        self._prompt.setWordWrap(True)
        self._mid_lo.addWidget(self._prompt)

        self._mid_lo.addSpacing(24)   # prompt ↔ input 간격

        # Input row
        self._input_row = QFrame()
        self._input_row.setStyleSheet("background:transparent;border:none;")
        self._input_lo = QHBoxLayout(self._input_row)
        self._input_lo.setContentsMargins(0,0,0,0)
        self._input_lo.setSpacing(0)

        self._input = QLineEdit()
        self._input.setReadOnly(True)   # 가상 키보드로만 입력
        self._input.setPlaceholderText("검색어를 입력하세요")

        self._enter_btn = QPushButton()
        self._enter_btn.setCursor(Qt.PointingHandCursor)
        self._enter_btn.clicked.connect(self._do_search)
        self._enter_lo = QHBoxLayout(self._enter_btn)
        self._enter_lo.setContentsMargins(0,0,0,0)
        self._enter_lo.setSpacing(0)
        self._enter_icon = make_svg(SVG_ENTER, C_BG, 24, 18)
        self._enter_lbl  = QLabel("입력")
        self._enter_lo.addStretch()
        self._enter_lo.addWidget(self._enter_icon)
        self._enter_lo.addSpacing(6)
        self._enter_lo.addWidget(self._enter_lbl)
        self._enter_lo.addStretch()

        self._input_lo.addWidget(self._input, stretch=1)
        self._input_lo.addWidget(self._enter_btn)
        self._mid_lo.addWidget(self._input_row)

        # 에러 메시지 레이블 (평소 숨김)                       # ★ NEW ★
        self._error_lbl = QLabel("")
        self._error_lbl.setAlignment(Qt.AlignCenter)
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setVisible(False)
        self._mid_lo.addWidget(self._error_lbl)

        # 에러 자동 해제 타이머                                 # ★ NEW ★
        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._clear_error)

        self._mid_lo.addStretch(1)   # 아래 여백 (작게 → 키보드에 공간 양보)

        self._root.addWidget(self._mid, stretch=1)

        # Virtual keyboard
        self._keyboard = VirtualKeyboard(on_key=self._key_pressed)
        self._keyboard.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._root.addWidget(self._keyboard)

    # ── actions ──────────────────────────────────────────────
    def _go_home(self):                                   # ★ CHANGED ★
        self._on_home()

    def reset(self):                                       # ★ NEW ★
        """
        입력창과 한글 조합기를 초기화한다.
        PageManager가 search 에서 다른 페이지로 전환할 때 호출.
        """
        self._composer.reset()
        self._input.clear()
        self._clear_error()                                    # ★ NEW ★

    def _show_error(self, message: str):                       # ★ NEW ★
        """입력창 테두리를 빨갛게 + 에러 문구 표시. 1.5초 후 자동 복원."""
        s = self._s
        r = max(round(12 * s), 4)
        input_h = max(round(96 * s), 34)
        enter_w = max(round(150 * s), 50)
        fs_input = max(round(30 * s), 11)

        # 입력창 테두리 빨갛게
        self._input.setStyleSheet(
            f"QLineEdit{{background:{C_BG};color:{C_DARK};"            f"border:1.5px solid #C0392B;border-right:none;"            f"border-top-left-radius:{r}px;border-bottom-left-radius:{r}px;"            f"font-size:{fs_input}px;"            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"            f"padding:0px {max(round(20*s),6)}px;}}"            f"QLineEdit:focus{{border:1.5px solid #C0392B;border-right:none;}}")

        # 에러 레이블 표시
        fs_err = max(round(26 * s), 10)
        self._error_lbl.setStyleSheet(
            f"color:#C0392B;font-size:{fs_err}px;"            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"            f"font-weight:400;background:transparent;")
        self._error_lbl.setText(message)
        self._error_lbl.setVisible(True)

        # 1.5초 후 자동 복원
        self._error_timer.stop()
        self._error_timer.start(1500)

    def _clear_error(self):                                    # ★ NEW ★
        """에러 표시 해제 — 입력창 원래 스타일 복원."""
        self._error_lbl.setVisible(False)
        self._error_lbl.setText("")
        # 입력창 스타일 원복 (apply_content_scale 과 동일)
        self._apply_content_scale(self._s)

    def _key_pressed(self, key: str):
        if key == "⌫":
            self._composer.backspace()
        elif key == "한/영":
            pass
        else:
            self._composer.push(key)
        self._input.setText(self._composer.text())

    def _do_search(self):                                    # ★ CHANGED ★
        # 조합 중인 글자 확정
        self._composer.flush()
        self._input.setText(self._composer.text())
        query = self._input.text().strip()

        error = validate_query(query)                          # ★ NEW ★
        if error:                                              # ★ NEW ★
            self._show_error(error)                            # ★ NEW ★
            return                                             # ★ NEW ★

        self._clear_error()                                    # ★ NEW ★
        self._on_search(query)

    # ── resize ───────────────────────────────────────────────
    def resizeEvent(self, event):                          # ★ CHANGED ★
        super().resizeEvent(event)
        self._do_scale()

    def _do_scale(self):                                   # ★ NEW ★
        s = min(self.width() / REF_W, self.height() / REF_H)
        self._s = s
        self._topbar.apply_scale(s)
        self._apply_content_scale(s)
        self._keyboard.apply_scale(s)

    def _apply_content_scale(self, s):
        hm = max(round(70*s), 20)

        # Prompt 좌우 여백
        self._mid_lo.setContentsMargins(hm, 0, hm, 0)

        fs_p = max(round(40*s), 14)
        self._prompt.setStyleSheet(
            f"color:{C_DARK};font-size:{fs_p}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"font-weight:300;line-height:1.6;background:transparent;")

        # Input
        input_h  = max(round(96*s), 34)
        enter_w  = max(round(150*s), 50)
        fs_input = max(round(30*s), 11)
        r        = max(round(12*s), 4)

        self._input.setFixedHeight(input_h)
        self._input.setStyleSheet(
            f"QLineEdit{{background:{C_BG};color:{C_DARK};"
            f"border:1.5px solid {C_BORDER};border-right:none;"
            f"border-top-left-radius:{r}px;border-bottom-left-radius:{r}px;"
            f"font-size:{fs_input}px;"
            f"font-family:'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;"
            f"padding:0px {max(round(20*s),6)}px;}}"
            f"QLineEdit:focus{{border:1.5px solid {C_DARK};border-right:none;}}")

        self._enter_btn.setFixedSize(enter_w, input_h)
        fs_e = max(round(26*s), 9)
        iw   = max(round(26*s), 10); ih = max(round(18*s), 7)
        self._enter_icon.setFixedSize(iw, ih)
        self._enter_lbl.setStyleSheet(
            f"color:{C_BG};font-size:{fs_e}px;"
            f"font-family:'Helvetica Neue',Arial;font-weight:500;background:transparent;")
        self._enter_btn.setStyleSheet(
            f"QPushButton{{background:{C_DARK};color:{C_BG};border:none;"
            f"border-top-right-radius:{r}px;border-bottom-right-radius:{r}px;}}"
            f"QPushButton:hover{{background:#2E2E2E;}}")

        # prompt ↔ input 간격
        self._mid_lo.setSpacing(0)
        # 내부 스페이서는 고정이므로 폰트 크기에 따라 proportional하게
        sp = max(round(28*s), 10)
        self._mid_lo.itemAt(2).spacerItem() # noop — spacing managed by addSpacing


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ShoeSearchPage(
        on_home=lambda: print("→ Home"),
        on_back=lambda: print("→ Back"),     # ★ CHANGED ★
        on_search=lambda q: print(f"→ 검색: [{q}]"),
    )
    win.show()
    sys.exit(app.exec())
