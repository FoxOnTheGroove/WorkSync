import os

import omni.ui as ui
import omni.kit.app
import omni.kit.async_engine

from .UVMixer_service import UVMixerService

try:
    import morph.hytwin_viewportwidget_extension as _hytwin_vp_wg
except ImportError:
    _hytwin_vp_wg = None

# 아이콘: 이 파일과 같은 usd_interpolation/ 아래 data/icons/ 에 둔다.
#   usd_interpolation/data/icons/arrow.png       (최소화 화살표, 펼침 상태)
#   usd_interpolation/data/icons/arrow_r.png     (180도 회전본, 접힘 상태)
#   usd_interpolation/data/icons/play.png        (재생)
#   usd_interpolation/data/icons/stop.png        (정지)
#   usd_interpolation/data/icons/checkbox_n.png  (체크박스 기본)
#   usd_interpolation/data/icons/checkbox_h.png  (체크박스 hover)
#   usd_interpolation/data/icons/checkbox_s.png  (체크박스 선택됨)
#   usd_interpolation/data/icons/tooltip.png     (슬라이더 press 시 값 말풍선)
#   usd_interpolation/data/icons/drop_arrow.png   (드롭다운 닫힘)
#   usd_interpolation/data/icons/drop_arrow_r.png (드롭다운 열림)
_ICON_DIR       = os.path.join(os.path.dirname(__file__), "data", "icons")
_ICON_ARROW     = os.path.join(_ICON_DIR, "arrow.png")
_ICON_ARROW_R   = os.path.join(_ICON_DIR, "arrow_r.png")
_ICON_PLAY      = os.path.join(_ICON_DIR, "play.png")
_ICON_STOP      = os.path.join(_ICON_DIR, "stop.png")
_ICON_CHECKBOX_N = os.path.join(_ICON_DIR, "checkbox_n.png")
_ICON_CHECKBOX_H = os.path.join(_ICON_DIR, "checkbox_h.png")
_ICON_CHECKBOX_S = os.path.join(_ICON_DIR, "checkbox_s.png")
_ICON_OV_N       = os.path.join(_ICON_DIR, "ov_n.png")      # 슬라이더 핸들 기본
_ICON_OV_H       = os.path.join(_ICON_DIR, "ov_h.png")      # 슬라이더 핸들 hover
_ICON_TOOLTIP    = os.path.join(_ICON_DIR, "tooltip.png")
_ICON_DROP_ARROW   = os.path.join(_ICON_DIR, "drop_arrow.png")    # 닫힘
_ICON_DROP_ARROW_R = os.path.join(_ICON_DIR, "drop_arrow_r.png")  # 열림

_TIP_W = 30           # 말풍선 폭 (tooltip.png 크기에 맞게 조정)
_TIP_H = 20           # 말풍선 높이
_TIP_FONT = 10
_TIP_LABEL_OFFSET_Y = -2   # 라벨 세로 미세조정(px). 음수=위로, 양수=아래로

# 툴팁은 패널 밖(윗선)으로 넘어가야 해서 별도 최상위 윈도우로 띄운다.
_TIP_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR
    | ui.WINDOW_FLAGS_NO_BACKGROUND
    | ui.WINDOW_FLAGS_NO_RESIZE
    | ui.WINDOW_FLAGS_NO_SCROLLBAR
    | ui.WINDOW_FLAGS_NO_COLLAPSE
    | ui.WINDOW_FLAGS_NO_MOVE
    | ui.WINDOW_FLAGS_NO_FOCUS_ON_APPEARING
)

# ── 패널 치수 명세 (전체 180x114) ───────────────────────────────────────
OVERLAY_W = 180
OVERLAY_H = 114
OVERLAY_W_MIN = 16          # 최소화 시 너비 (버튼만)
OVERLAY_H_MIN = 16          # 최소화 시 높이 (버튼만)
_MARGIN = 8                 # 뷰포트 가장자리 여백

# 행 높이 (세로 누적: 20 + 30 + 1 + 33 + 30 = 114)
_ROW1_H = 20                # 타이틀
_ROW2_H = 30                # 재생/슬라이더/배속
_DIV_H  = 1                 # 구분선
_ROW4_H = 33                # Reverse/Loop
_ROW5_H = 30                # 재생 속도 (라벨 + 드롭다운 박스)

# 두 번째 열 시작 x (패널 왼쪽 끝 기준) — 행4 Reverse 체크박스와 행5 박스가 맞춰짐
_COL2_X = 78
# 행5 가로 배치: 12 여백 | 라벨 | x78 박스 90x18 | 12 여백  (합 180)
_SPD_BOX_W = 90
_SPD_BOX_H = 18
_SPD_LABEL_OFFSET_Y = -2    # "재생 속도" 세로 미세조정(px). 음수=위로, 양수=아래로

# 행2 구성요소 (8 + 24 + 8 + 13 + 6 + 90 + 6 + 13 + 12 = 180)
_PLAY_SIZE = 24             # 재생/정지 버튼
_NUM_W     = 13             # 슬라이더 좌/우 숫자 라벨 폭
_NUM_H     = 18             # 그 라벨 밴드 높이
_NUM_FONT  = 17
_SLIDER_W  = 90             # 좌측 59에서 시작해 149에서 끝남

# ── 재생 속도 드롭다운 ──────────────────────────────────────────────────
# 색은 0xAABBGGRR. (#8f9094 → 0xFF94908F 처럼 RGB 를 뒤집어 적는다)
_DROP_FONT       = 17       # 박스/아이템 텍스트 (2px 위로 보정해서 씀)
_DROP_ARROW_SIZE = 10       # 우측 화살표 이미지 크기
_DROP_PAD        = 8        # 박스 좌/우 안쪽 여백
_DROP_GAP        = 4        # 박스와 펼쳐진 목록 사이 간격
_DROP_N   = 0xFF94908F      # #8f9094  기본 (박스 테두리 + 텍스트)
_DROP_H   = 0xFFF18D95      # #958df1  hover / 펼쳐진 상태
_DROP_P   = 0xFFB34747      # #4747b3  press
# 아이템: 반투명 상태색을 덧칠하므로 불투명 바탕을 먼저 깐다
_DROP_ITEM_BG  = 0xFF34302F # #2f3034  아이템 바탕
_DROP_ITEM_HOV = 0x26F1A9AE # #aea9f1 26  hover 덧칠
_DROP_ITEM_PRS = 0x59F1A9AE # #aea9f1 59  press 덧칠

_TITLE_BG = 0xFF000000      # 타이틀바: 진한 검정
_BODY_BG  = 0xFF3C3C3C      # 본문: 진한 회색
_WHITE    = 0xFFFFFFFF      # 본문/타이틀 텍스트·버튼 전부 흰색

# 색은 이 파일 전체에서 0xAABBGGRR 순서 (예: #958df1 → 0xFFF18D95).
_HOVER_OVERLAY = 0x22FFFFFF        # 반투명 흰색 (hover 오버레이)
_PRESS_TINT    = 0xFF0000FF        # 빨강 (press 시 흰 아이콘에 씌우는 tint)

SPEED_CYCLE = [1.0, 2.0, 4.0, 0.5, 0.25]    # 드롭다운 아이템 목록(순서 그대로)


def _speed_label(spd: float) -> str:
    return f"x{spd:g}"


def _vcenter(width, factory):
    """고정 크기 위젯을 부모 행(row) 높이 안에서 세로 중앙에 배치하고 반환.
    HStack 안 자식은 기본 상단정렬이라, 위/아래 Spacer로 감싸 중앙에 맞춘다."""
    with ui.VStack(width=width):
        ui.Spacer()
        w = factory()
        ui.Spacer()
    return w


def _point_in_widget(widget, x, y, w, h) -> bool:
    wx, wy = widget.screen_position_x, widget.screen_position_y
    return wx <= x <= wx + w and wy <= y <= wy + h


def _build_tri_icon(width, height, img_n, img_h, img_s):
    """n/h/s 세 이미지를 미리 로드해 겹쳐두고(깜박임 없는 visible 토글용) 그
    위에 투명 hit Rectangle을 얹어 (n, h, s, hit) 튜플로 반환.
    마우스 핸들러는 호출부에서 hit에 건다."""
    with ui.ZStack(width=width, height=height):
        n = ui.Image(img_n, width=width, height=height,
                     fill_policy=ui.FillPolicy.STRETCH)
        h = ui.Image(img_h, width=width, height=height,
                     fill_policy=ui.FillPolicy.STRETCH, visible=False)
        s = ui.Image(img_s, width=width, height=height,
                     fill_policy=ui.FillPolicy.STRETCH, visible=False)
        hit = ui.Rectangle(style={"background_color": 0x00000000})
    return n, h, s, hit


_SLIDER_TRACK_BG   = 0xFF8A8A8A     # 트랙 (밝은 회색 — 어두운 본문 대비 ↑, 캡슐 실루엣 강조. 원래 스펙은 #6a6a6a)
_SLIDER_FILL_BG    = 0xFFF18D95     # 채움 (#958df1 → 0xAABBGGRR)


class _ImageSlider:
    """커스텀 슬라이더 (t 0~1, 값 텍스트 없음).
    트랙 112x4(#6a6a6a) + 채움(#958df1) + 10x10 이미지 핸들(ov_n/ov_h).
    트랙 클릭으로 점프하는 기능은 없음 — 핸들 자체만 누르고 드래그 가능.
    FloatSlider 대체품 — .model(SimpleFloatModel)을 노출해서
    기존 set_value / add_value_changed_fn 호출부가 그대로 동작한다."""

    def __init__(self, width=112, track_h=4, knob=10):
        self._w, self._th, self._ks = width, track_h, knob
        self._dragging = False
        self._hovering = False     # 마우스가 핸들(10x10) 위에 있는지 (press와 무관)
        self._tip_win = None       # 값 말풍선 (별도 윈도우, 최초 press 때 생성)
        self._tip_label = None
        self.model = ui.SimpleFloatModel(0.0)
        self.model.add_value_changed_fn(lambda m: self._update_visual())
        self._build()
        self._update_visual()

    def destroy(self):
        if self._tip_win:
            self._tip_win.destroy()
            self._tip_win = None

    def _crisp_caps(self, color):
        """현재 컨테이너(폭은 부모가 결정)에 crisp 캡슐 실루엣을 그린다.
        세로로 꽉 찬 몸통(좌우 1px 인셋) + 세로 중앙 밴드(풀 폭)의 합집합이라
        네 끝 모서리 1px만 비고 나머지는 꽉 찬다. border_radius를 안 써서
        안티앨리어싱 없이 픽셀이 딱 잘린다(4px 기준 각 끝 위/아래 1px씩 컷)."""
        with ui.HStack():                       # 몸통: 좌우 1px 들어감, 세로 풀
            ui.Spacer(width=1)
            ui.Rectangle(style={"background_color": color})
            ui.Spacer(width=1)
        with ui.VStack():                       # 밴드: 세로 중앙 (th-2)px, 가로 풀
            ui.Spacer(height=1)
            ui.Rectangle(height=self._th - 2, style={"background_color": color})
            ui.Spacer(height=1)

    def _build(self):
        with ui.ZStack(width=self._w, height=self._ks):
            # 드래그 안전망(맨 아래, 영구): 드래그 중 마우스가 핸들 밖으로 나가도
            # move/release를 계속 받는다. press/hover는 없음.
            self._catcher = ui.Rectangle(style={"background_color": 0x00000000})
            self._catcher.set_mouse_moved_fn(self._on_move)
            self._catcher.set_mouse_released_fn(self._on_release)

            # 트랙 + 채움 (세로 중앙). 채움은 Placer 안 왼쪽 고정, width로 크기.
            # border_radius(항상 AA되어 4px에선 흐릿)는 안 쓰고, 일반 Rectangle
            # 조합으로 양 끝 모서리 1px만 하드하게 잘라낸다(crisp, AA 없음).
            with ui.VStack():
                ui.Spacer()
                with ui.ZStack(height=self._th):
                    self._crisp_caps(_SLIDER_TRACK_BG)          # 트랙 (풀 폭)
                    with ui.Placer():
                        self._fill = ui.ZStack(width=0, height=self._th)
                        with self._fill:
                            self._crisp_caps(_SLIDER_FILL_BG)   # 채움 (폭 동적)
                ui.Spacer()

            # 핸들: Placer.offset_x로 위치. n/h 이미지 미리 로드 후 visible 토글(깜박임 X).
            self._knob_placer = ui.Placer(width=self._w, height=self._ks)
            with self._knob_placer:
                with ui.ZStack(width=self._ks, height=self._ks):
                    self._knob_n = ui.Image(_ICON_OV_N,
                                            width=self._ks, height=self._ks,
                                            fill_policy=ui.FillPolicy.STRETCH)
                    self._knob_h = ui.Image(_ICON_OV_H,
                                            width=self._ks, height=self._ks,
                                            fill_policy=ui.FillPolicy.STRETCH,
                                            visible=False)
                    # hit: 핸들 위에서만 hover/press 판정 (핸들과 함께 이동)
                    self._hit = ui.Rectangle(style={"background_color": 0x00000000})
                    self._hit.set_mouse_hovered_fn(self._on_hover)
                    self._hit.set_mouse_pressed_fn(self._on_press)
                    self._hit.set_mouse_moved_fn(self._on_move)
                    self._hit.set_mouse_released_fn(self._on_release)

    def _update_visual(self):
        # 위치/크기만 갱신 (rebuild 없음 → 깜박임 없음).
        # .width / .offset_x 는 ui.Length 필요 (raw 숫자는 타입 에러).
        t = self.model.get_value_as_float()
        self._fill.visible = t > 0.0
        self._fill.width = ui.Pixel(t * self._w)
        self._knob_placer.offset_x = ui.Pixel(t * (self._w - self._ks))

    def _set_knob_img(self):
        # hover 여부만 반영 (press/drag와 완전 별개)
        self._knob_h.visible = self._hovering
        self._knob_n.visible = not self._hovering

    def _t_from_screen_x(self, x: float) -> float:
        local = x - self._catcher.screen_position_x - self._ks / 2
        span = self._w - self._ks
        return max(0.0, min(1.0, local / span)) if span > 0 else 0.0

    # ── 값 말풍선 (별도 최상위 윈도우, press 중에만 표시) ───────────────

    def _ensure_tip_win(self):
        if self._tip_win is not None:
            return
        self._tip_win = ui.Window(f"__slider_tip_{id(self)}__",
                                  width=_TIP_W, height=_TIP_H, flags=_TIP_FLAGS)
        self._tip_win.padding_x = 0
        self._tip_win.padding_y = 0
        self._tip_win.visible = False
        with self._tip_win.frame:
            with ui.ZStack():
                ui.Image(_ICON_TOOLTIP, width=_TIP_W, height=_TIP_H,
                         fill_policy=ui.FillPolicy.STRETCH)
                with ui.Placer(offset_x=0, offset_y=ui.Pixel(_TIP_LABEL_OFFSET_Y)):
                    self._tip_label = ui.Label(
                        "", width=_TIP_W, height=_TIP_H,
                        alignment=ui.Alignment.CENTER,
                        style={"font_size": _TIP_FONT, "color": _WHITE})

    def _position_tip(self):
        kx = self._catcher.screen_position_x + \
            self.model.get_value_as_float() * (self._w - self._ks)
        ky = self._catcher.screen_position_y
        self._tip_win.position_x = kx + self._ks / 2 - _TIP_W / 2
        self._tip_win.position_y = ky - _TIP_H - 2
        self._tip_label.text = f"{self.model.get_value_as_float():.2f}"

    # ── hover: 오직 "마우스가 핸들 위" 여부. press와 무관 ──────────────

    def _on_hover(self, hovered: bool):
        self._hovering = hovered
        self._set_knob_img()

    # ── press/drag: hover/이미지를 건드리지 않는다 ────────────────────

    def _on_press(self, x, y, button, modifier):
        if button == 0:
            self._dragging = True
            self._ensure_tip_win()
            self._position_tip()
            self._tip_win.visible = True

    def _on_move(self, x, y, modifier, pressed):
        if self._dragging:
            self.model.set_value(self._t_from_screen_x(x))
            self._position_tip()

    def _on_release(self, x, y, button, modifier):
        if button == 0:
            self._dragging = False
            if self._tip_win:
                self._tip_win.visible = False


# ── 스타일시트 ──────────────────────────────────────────────────────────
# 타입 선택자("Button", "Label")는 그 스타일이 걸린 컨테이너 하위 전체에
# 캐스케이딩된다. 인스턴스별로 다르게 주려면 위젯에 `.name`을 달고
# "타입::이름" 형태로 오버라이드한다(CSS의 태그 선택자 / .class 선택자와 대응).
# 이 dict를 최상위 VStack(_build 안)에 한 번만 걸면 전체에 적용된다.
_STYLE = {
    "Button":            {"background_color": 0x00000000, "color": _WHITE, "font_size": 10,
                          "margin": 0, "padding": 0, "border_width": 0},   # 기본 여백 제거 — width/height 그대로 콘텐츠 영역
    "Button:hovered":     {"background_color": 0x22FFFFFF},
    "Button:pressed":     {"background_color": 0x44FFFFFF},
    "Label":              {"color": _WHITE, "font_size": 10},
    # 기본 테두리 제거 (Rectangle::* 배경 뒤로 window 기본 테두리가 비치는 것 방지)
    "Rectangle":            {"border_width": 0, "border_color": 0x00000000},
    "Rectangle::title_bg":  {"background_color": _TITLE_BG},
    "Rectangle::body_bg":   {"background_color": _BODY_BG},
    "Rectangle::separator": {"background_color": _WHITE},
    # 재생 속도 드롭다운 박스 — 배경 없이 1px 테두리 (색은 상태 따라 갱신)
    "Rectangle::spd_box":   {"background_color": 0x00000000,
                             "border_width": 1, "border_color": _DROP_N},
}


_WINDOW_FLAGS = (
    ui.WINDOW_FLAGS_NO_TITLE_BAR
    | ui.WINDOW_FLAGS_NO_BACKGROUND     # window 자체 기본 배경/테두리 제거 (Rectangle이 전부 대체)
    | ui.WINDOW_FLAGS_NO_RESIZE
    | ui.WINDOW_FLAGS_NO_SCROLLBAR
    | ui.WINDOW_FLAGS_NO_COLLAPSE
    | ui.WINDOW_FLAGS_NO_MOVE
)


def _find_vph(target_path: str):
    if _hytwin_vp_wg is None:
        return None
    for vph in _hytwin_vp_wg.ViewportWidgetHost.get_instances():
        if vph.prim_header_path.rstrip("/") == target_path.rstrip("/"):
            return vph
    return None


def _calc_overlay_pos(vph, width: int = OVERLAY_W, height: int = OVERLAY_H,
                      extra_margin_y: int = 0) -> tuple[int, int]:
    frame = vph.frame
    x = int(frame.screen_position_x + frame.computed_width  - width - _MARGIN)
    y = int(frame.screen_position_y + frame.computed_height - height - _MARGIN - extra_margin_y)
    return x, y


class ViewportOverlayPanel:

    def __init__(self, key: str, vph, mgr: 'OverlayManager', tab_id: 'str | None',
                 frame=None, top_frame=None):
        # frame: 뷰포트 오버레이 프레임(vph.frame)을 주면 창 대신 그 위에 얹는다.
        #   창이 아니라서 32px 최소크기 클램프도, 지워지지 않는 창 테두리도,
        #   런타임 리사이즈 불가 문제도 없다(최소화 = 행 높이 변경).
        # top_frame: 그보다 뒤에 선언돼 패널을 덮는 프레임(vph.topframe).
        #   커서가 패널 위일 때만 그 프레임 입력을 꺼서 클릭이 패널로 내려오게 한다.
        self._key = key
        self._mgr = mgr
        self._vph = vph
        self._frame = frame
        self._top_frame = top_frame
        self._top_muted = False      # 상위 프레임 입력을 꺼 둔 상태인지
        self._spd_label = None       # 드롭다운 박스의 현재 값 라벨 (_apply_speed 가 갱신)
        self._spd_box = None         # 드롭다운 접힘 박스 (테두리 = 상태색)
        self._drop_popup = None      # 펼친 목록 (Placer)
        self._drop_hit = None
        self._drop_arrow = None      # 닫힘/열림 화살표 (visible 토글)
        self._drop_arrow_r = None
        self._drop_catcher = None    # 바깥 클릭 감지 (프레임 경로에서만)
        self._drop_open = False
        self._drop_hovering = False
        self._drop_pressing = False
        self._num_left = None        # 슬라이더 좌측 숫자 (추후 값1)
        self._num_right = None       # 슬라이더 우측 숫자 (추후 값2)
        self._ico_open = None        # 최소화 버튼 아이콘 (펼침/접힘, visible 토글)
        self._ico_min = None
        self._min_hover_ov = None    # 최소화 버튼 hover 하이라이트
        self._root = None            # 프레임 경로에서 우리 서브트리 루트
        self._panel_row = None       # 프레임 경로에서 패널 높이를 쥔 행
        self._min_window = None
        self._window = None
        self._tab_id = tab_id
        self._in_tick = False
        self._in_sync = False
        self._reposition_task = None
        self._minimized = False
        self._speed = 1.0
        self._reverse = False
        self._loop = False
        # 재생/체크박스 hover·press 상태 (default/hover/press 3단 표현용)
        self._play_hovering = False
        self._play_pressing = False
        self._spd_hovering = False
        self._spd_pressing = False
        self._rev_hovering = False
        self._rev_pressing = False
        self._loop_hovering = False
        self._loop_pressing = False

        self._widgets: dict = {}
        if frame is not None:
            self._build_in_frame()
            self._subscribe_players()
            return

        x, y = _calc_overlay_pos(vph)
        self._window = ui.Window(
            f"__overlay_{key}__",
            width=OVERLAY_W, height=OVERLAY_H,
            position_x=x, position_y=y,
            flags=_WINDOW_FLAGS,
        )
        # NO_BACKGROUND는 배경/테두리만 지울 뿐, Window의 기본 content padding은
        # 남아있어 좌/상단에 뿌연 여백으로 보이고 width/height를 갉아먹는다.
        self._window.padding_x = 0
        self._window.padding_y = 0
        self._build()

        # 최소화 상태 창(16x16, 복원 버튼만). 본 창과 visible을 순환시킨다 —
        # 런타임 window 리사이즈가 안 먹어서, 리사이즈 대신 두 창을 스왑한다.
        mx, my = _calc_overlay_pos(vph, width=OVERLAY_W_MIN, height=OVERLAY_H_MIN,
                                   extra_margin_y=_MARGIN)
        self._min_window = ui.Window(
            f"__overlay_min_{key}__",
            width=OVERLAY_W_MIN, height=OVERLAY_H_MIN,
            position_x=mx, position_y=my,
            flags=_WINDOW_FLAGS,
        )
        self._min_window.padding_x = 0
        self._min_window.padding_y = 0
        self._min_window.visible = False
        # 창에는 최소 크기 clamp가 있어(정확한 값은 미확인, ImGui 기본은 32x32)
        # 16x16 지정에도 창 자체는 더 크게 남는다. 창은 NO_BACKGROUND라 투명하니,
        # 보이는 콘텐츠만 16x16으로 좌상단에
        # 고정하고 나머지는 비워 시각적으로 16x16이 되게 한다.
        with self._min_window.frame:
            with ui.VStack():
                with ui.HStack(height=OVERLAY_H_MIN):
                    with ui.ZStack(width=OVERLAY_W_MIN, height=OVERLAY_H_MIN,
                                   style=_STYLE):
                        ui.Rectangle(name="title_bg")   # 타이틀바와 같은 검정 배경
                        with ui.HStack():
                            ui.Spacer(width=3)
                            _vcenter(10, lambda: ui.Button(
                                "", width=10, height=10, name="min_btn",
                                alignment=ui.Alignment.CENTER,
                                clicked_fn=self._on_toggle_minimize,
                                style={"image_url": _ICON_ARROW_R,
                                       "fill_policy": ui.FillPolicy.STRETCH}))
                            ui.Spacer()
                    ui.Spacer()      # 우측 나머지(투명)
                ui.Spacer()          # 하단 나머지(투명)

        vph.frame.set_computed_content_size_changed_fn(self._on_viewport_resized)
        self._subscribe_players()

    def _subscribe_players(self) -> None:
        sp = UVMixerService.get_shared_player(self._tab_id)
        sp.subscribe_tick(self._on_tick)
        sp.subscribe_stopped(self._on_stopped)

        mixer = UVMixerService.get_instance(self._key)
        if mixer:
            mixer.own_player.subscribe_tick(self._on_own_tick)
            mixer.own_player.subscribe_stopped(self._on_own_stopped)

    def _build_in_frame(self) -> None:
        # 우하단 앵커. 스페이서로 밀어내므로 위치 계산도, 리사이즈 콜백도 필요
        # 없다(_calc_overlay_pos / _on_viewport_resized 는 창 경로 전용).
        # 패널 높이는 _panel_row 가 쥐고, 최소화 때 그 행만 줄인다.
        #
        # ⚠ 넘겨줄 컨테이너는 ZStack 을 권장한다. ui.Frame 은 자식을 하나만
        #   가지므로, 이미 다른 UI 가 쓰고 있는 Frame 을 주면 그 내용이 우리
        #   위젯으로 '교체'되어 사라진다. ZStack 이면 한 자식으로 얹혀서 서로
        #   영향 없이 겹쳐 그려진다(우리 영역 밖은 전부 투명 Spacer).
        #
        # opaque 는 패널 영역만 잡고 host 는 통과시켜야 패널 밖 클릭이 아래
        # 프레임(뷰포트/픽킹)으로 내려간다. Stack 엔 이 속성이 없을 수 있어 가드.
        if hasattr(self._frame, "opaque_for_mouse_events"):
            self._frame.opaque_for_mouse_events = False
        with self._frame:
            # 루트를 ZStack 으로 둬서 패널 위/아래에 레이어를 하나씩 깐다.
            #  - 맨 아래: 드롭다운 바깥 클릭 감지용 catcher (열렸을 때만 보임)
            #  - 가운데: 패널
            #  - 맨 위:  드롭다운 펼침 목록 (패널 밖으로 나가도 안 잘림)
            self._root = ui.ZStack()      # 우리 서브트리 루트 (정리는 이것만)
            with self._root:
                self._drop_catcher = ui.Rectangle(
                    style={"background_color": 0x00000000}, visible=False)
                self._drop_catcher.set_mouse_pressed_fn(
                    lambda x, y, b, m: self._set_drop_open(False) if b == 0 else None)

                with ui.VStack():
                    ui.Spacer()                               # 위쪽 흡수
                    self._panel_row = ui.HStack(height=OVERLAY_H)
                    with self._panel_row:
                        ui.Spacer()                           # 왼쪽 흡수
                        panel_frame = ui.Frame(width=OVERLAY_W)
                        panel_frame.opaque_for_mouse_events = True
                        with panel_frame:
                            with ui.ZStack():
                                self._build_blocker()   # 뒤로 클릭 새는 것 차단
                                self._build_panel()
                        ui.Spacer(width=_MARGIN)              # 우측 여백
                    ui.Spacer(height=_MARGIN)                 # 하단 여백

                # 패널 '밖'(프레임 레벨)에 두어야 패널 경계에서 안 잘린다.
                self._build_drop_popup()

    def _build_min_btn(self):
        # ui.Button 대신 이미지 2장 + 투명 Rectangle. Rectangle 은 상위 프레임에
        # 가려져도 마우스를 받고, 아이콘도 visible 토글이라 재로드로 깜박이지
        # 않는다(슬라이더 노브·체크박스와 같은 패턴).
        with ui.ZStack(width=10, height=10):
            self._ico_open = ui.Image(_ICON_ARROW, width=10, height=10,
                                      fill_policy=ui.FillPolicy.STRETCH)
            self._ico_min  = ui.Image(_ICON_ARROW_R, width=10, height=10,
                                      fill_policy=ui.FillPolicy.STRETCH,
                                      visible=False)
            self._min_hover_ov = ui.Rectangle(              # hover 하이라이트
                style={"background_color": _HOVER_OVERLAY}, visible=False)
            hit = ui.Rectangle(style={"background_color": 0x00000000})
            hit.set_mouse_pressed_fn(
                lambda x, y, b, m: self._on_toggle_minimize() if b == 0 else None)
            hit.set_mouse_hovered_fn(self._on_min_hover)
        return hit

    def _on_min_hover(self, hovered: bool) -> None:
        self._min_hover_ov.visible = hovered

    # ── 재생 속도 드롭다운 ──────────────────────────────────────────────

    def _build_spd_box(self):
        # 접힌 상태 박스 (90x18): 8 여백 | 현재 값 | ... | 화살표 | 8 여백.
        # 테두리·텍스트 색은 상태(_refresh_drop_colors)에 따라 바뀐다.
        with ui.ZStack(height=_SPD_BOX_H):
            self._spd_box = ui.Rectangle(name="spd_box")
            with ui.HStack():
                ui.Spacer(width=_DROP_PAD)
                # 17pt 라인박스가 18px 밴드보다 커서 글자가 아래로 밀린다 →
                # 다른 라벨들과 같은 Placer 픽셀 보정.
                with ui.ZStack():
                    with ui.Placer(offset_x=0,
                                   offset_y=ui.Pixel(_TIP_LABEL_OFFSET_Y)):
                        self._spd_label = ui.Label(
                            _speed_label(self._speed), height=_SPD_BOX_H,
                            alignment=ui.Alignment.LEFT_CENTER,
                            style={"font_size": _DROP_FONT, "color": _DROP_N})
                ui.Spacer()

                def _arrows():
                    with ui.ZStack(width=_DROP_ARROW_SIZE, height=_DROP_ARROW_SIZE):
                        self._drop_arrow = ui.Image(
                            _ICON_DROP_ARROW,
                            width=_DROP_ARROW_SIZE, height=_DROP_ARROW_SIZE,
                            fill_policy=ui.FillPolicy.STRETCH)
                        self._drop_arrow_r = ui.Image(
                            _ICON_DROP_ARROW_R,
                            width=_DROP_ARROW_SIZE, height=_DROP_ARROW_SIZE,
                            fill_policy=ui.FillPolicy.STRETCH, visible=False)
                _vcenter(_DROP_ARROW_SIZE, _arrows)
                ui.Spacer(width=_DROP_PAD)

            hit = ui.Rectangle(style={"background_color": 0x00000000})
            hit.set_mouse_hovered_fn(self._on_drop_hover)
            hit.set_mouse_pressed_fn(self._on_drop_press)
            hit.set_mouse_released_fn(self._on_drop_release)
            self._drop_hit = hit

    def _drop_list_h(self):
        return len(SPEED_CYCLE) * _SPD_BOX_H

    def _build_drop_popup(self):
        # 위쪽으로 펼친다. 창 경로(body 안)일 때의 기본 좌표 — 프레임 경로에선
        # 열 때마다 _position_drop_popup() 이 화면좌표로 다시 잡는다.
        rows_above = _ROW2_H + _DIV_H + _ROW4_H          # 박스 상단 y (body 기준)
        self._drop_popup = ui.Placer(
            offset_x=ui.Pixel(_COL2_X),
            offset_y=ui.Pixel(rows_above - _DROP_GAP - self._drop_list_h()))
        self._drop_popup.visible = False
        with self._drop_popup:
            with ui.VStack(width=_SPD_BOX_W, spacing=0):
                for value in SPEED_CYCLE:
                    self._build_drop_item(value)

    def _build_drop_item(self, value):
        # 아이템 (90x18): 불투명 바탕 위에 반투명 상태색을 덧칠. 텍스트는 흰색 고정.
        with ui.ZStack(height=_SPD_BOX_H):
            ui.Rectangle(style={"background_color": _DROP_ITEM_BG})
            ov = ui.Rectangle(style={"background_color": 0x00000000})
            with ui.HStack():
                ui.Spacer(width=_DROP_PAD)
                with ui.ZStack():                     # 박스 라벨과 같은 2px 보정
                    with ui.Placer(offset_x=0,
                                   offset_y=ui.Pixel(_TIP_LABEL_OFFSET_Y)):
                        ui.Label(_speed_label(value), height=_SPD_BOX_H,
                                 alignment=ui.Alignment.LEFT_CENTER,
                                 style={"font_size": _DROP_FONT, "color": _WHITE})
                ui.Spacer(width=_DROP_PAD)
            hit = ui.Rectangle(style={"background_color": 0x00000000})
            hit.set_mouse_hovered_fn(
                lambda h, o=ov: self._set_item_tint(o, _DROP_ITEM_HOV if h else 0x00000000))
            hit.set_mouse_pressed_fn(
                lambda x, y, b, m, o=ov: self._on_item_press(o, b))
            hit.set_mouse_released_fn(
                lambda x, y, b, m, o=ov, v=value, t=hit: self._on_item_release(o, v, t, x, y, b))

    def _set_item_tint(self, ov, color):
        ov.style = {"background_color": color}

    def _on_item_press(self, ov, button):
        if button == 0:
            self._set_item_tint(ov, _DROP_ITEM_PRS)

    def _on_item_release(self, ov, value, hit, x, y, button):
        if button != 0:
            return
        inside = _point_in_widget(hit, x, y, _SPD_BOX_W, _SPD_BOX_H)
        self._set_item_tint(ov, _DROP_ITEM_HOV if inside else 0x00000000)
        if inside:
            self._apply_speed(value)
            self._set_drop_open(False)

    def _position_drop_popup(self) -> None:
        # 프레임 경로: 팝업이 패널 밖(프레임 루트)에 있으므로 프레임 로컬 좌표로
        # 다시 잡는다. 박스/프레임의 화면좌표 차이 = 프레임 안에서의 위치.
        if self._frame is None or self._spd_box is None:
            return
        try:
            fx, fy = self._frame.screen_position_x, self._frame.screen_position_y
            bx, by = self._spd_box.screen_position_x, self._spd_box.screen_position_y
        except Exception:
            return
        self._drop_popup.offset_x = ui.Pixel(bx - fx)
        self._drop_popup.offset_y = ui.Pixel(by - fy - _DROP_GAP - self._drop_list_h())

    def _set_drop_open(self, is_open: bool) -> None:
        self._drop_open = is_open
        if is_open:
            self._position_drop_popup()
        self._drop_popup.visible = is_open
        if self._drop_catcher is not None:      # 바깥 클릭으로 닫기
            self._drop_catcher.visible = is_open
        self._drop_arrow.visible = not is_open
        self._drop_arrow_r.visible = is_open
        self._refresh_drop_colors()

    def _refresh_drop_colors(self) -> None:
        # press > (열림 또는 hover) > 기본. 열린 상태는 hover 와 같은 색.
        if self._drop_pressing:
            c = _DROP_P
        elif self._drop_open or self._drop_hovering:
            c = _DROP_H
        else:
            c = _DROP_N
        if self._spd_box is not None:
            self._spd_box.style = {"background_color": 0x00000000,
                                   "border_width": 1, "border_color": c}
        if self._spd_label is not None:
            self._spd_label.style = {"font_size": _DROP_FONT, "color": c}

    def _on_drop_hover(self, hovered: bool) -> None:
        self._drop_hovering = hovered
        self._refresh_drop_colors()

    def _on_drop_press(self, x, y, button, modifier) -> None:
        if button == 0:
            self._drop_pressing = True
            self._refresh_drop_colors()

    def _on_drop_release(self, x, y, button, modifier) -> None:
        if button != 0:
            return
        was_pressing = self._drop_pressing
        self._drop_pressing = False
        inside = _point_in_widget(self._drop_hit, x, y, _SPD_BOX_W, _SPD_BOX_H)
        self._drop_hovering = inside
        if was_pressing and inside:
            self._set_drop_open(not self._drop_open)
        else:
            self._refresh_drop_colors()

    def _build_num_label(self, alignment):
        # 슬라이더 좌/우 숫자("00"). 17pt 라인박스가 밴드보다 커서 글자가 아래로
        # 밀리므로, 툴팁 라벨과 같은 Placer 픽셀 보정을 적용한다.
        # 정렬은 '슬라이더에 붙는 쪽' 기준 — 한 자리 숫자가 와도 왼쪽 라벨은
        # 오른쪽 0 자리에, 오른쪽 라벨은 왼쪽 0 자리에 놓이게 한다.
        with ui.ZStack(height=_NUM_H):
            with ui.Placer(offset_x=0,
                           offset_y=ui.Pixel(_TIP_LABEL_OFFSET_Y)):
                lbl = ui.Label("00", width=_NUM_W, height=_NUM_H,
                               alignment=alignment,
                               style={"font_size": _NUM_FONT})
        return lbl

    def _build_blocker(self):
        # 패널 영역 전체를 덮는 투명 Rectangle. 콜백이 붙어 있어야 omni.ui 가
        # 이벤트를 '처리됨'으로 보고 뒤(뷰포트)로 안 넘긴다. ZStack 맨 아래라
        # 버튼/슬라이더가 먼저 가져가고 빈 틈만 여기서 먹는다.
        blocker = ui.Rectangle(style={"background_color": 0x00000000})
        for setter in ("set_mouse_pressed_fn", "set_mouse_released_fn",
                       "set_mouse_moved_fn", "set_mouse_double_clicked_fn",
                       "set_mouse_wheel_fn"):
            fn = getattr(blocker, setter, None)
            if fn:
                fn(lambda *args: None)
        # 상위 프레임에 가려져도 hover 는 들어오므로, 이걸로 그 입력을 껐다 켠다.
        blocker.set_mouse_hovered_fn(self._on_panel_hover)
        return blocker

    # ── 상위 프레임(topframe)에 가려질 때: 커서가 패널 위면 그 입력만 잠깐 끈다 ──

    def set_top_frame(self, frame) -> None:
        self._top_frame = frame

    def _on_panel_hover(self, hovered: bool) -> None:
        self._mute_top(hovered)

    def _mute_top(self, mute: bool) -> None:
        if self._top_frame is None or mute == self._top_muted:
            return
        self._top_frame.opaque_for_mouse_events = not mute
        self._top_muted = mute

    def _build(self) -> None:
        with self._window.frame:
            self._build_panel()

    def _build_panel(self) -> None:
        with ui.VStack(spacing=0, style=_STYLE):     # 스타일시트는 여기 한 번만

            # ── 행1 (y=0, h=20): 검정 타이틀바 ──────────────────────
            #   3 여백 | 최소화 10x10(중앙) | 2 여백 | Animation(폰트10,h15,윗점)
            with ui.ZStack(height=_ROW1_H):
                ui.Rectangle(name="title_bg")
                with ui.HStack():
                    ui.Spacer(width=3)
                    btn_min = _vcenter(10, self._build_min_btn)
                    ui.Spacer(width=2)
                    title_content = ui.HStack()       # 최소화 시 숨김
                    with title_content:
                        with ui.VStack():             # 윗점(top) 정렬
                            ui.Label(self._key, height=15,
                                     style={"font_size": 10})
                            ui.Spacer()

            # ── 본문 (행2·구분선·행4). 최소화 시 통째로 숨김 ─────────
            body = ui.ZStack(height=OVERLAY_H - _ROW1_H)
            with body:
                ui.Rectangle(name="body_bg")
                with ui.VStack(spacing=0):

                    # 행2 (y=20, h=30):
                    #   8 | 재생24 | 8 | 숫자13 | 6 | 슬라이더90 | 6 | 숫자13 | 12
                    #   = 8+24+8+13+6+90+6+13+12 = 180. 슬라이더는 좌측 59에서 시작.
                    #   배속 버튼은 제거(핸들러/사이클 로직은 남겨둠).
                    with ui.HStack(height=_ROW2_H):
                        ui.Spacer(width=8)
                        # 재생/정지 아이콘 미리 로드 + visible 토글(깜박임 없음, 노브와 동일
                        # 패턴). hover는 반투명 오버레이, press는 흰 아이콘에 빨강 tint.
                        def _build_play():
                            with ui.ZStack(width=_PLAY_SIZE, height=_PLAY_SIZE):
                                self._play_img = ui.Image(
                                    _ICON_PLAY, width=_PLAY_SIZE, height=_PLAY_SIZE,
                                    fill_policy=ui.FillPolicy.STRETCH,
                                    style={"color": _WHITE})
                                self._stop_img = ui.Image(
                                    _ICON_STOP, width=_PLAY_SIZE, height=_PLAY_SIZE,
                                    fill_policy=ui.FillPolicy.STRETCH,
                                    style={"color": _WHITE}, visible=False)
                                self._play_hover_ov = ui.Rectangle(
                                    style={"background_color": _HOVER_OVERLAY},
                                    visible=False)
                                hit = ui.Rectangle(
                                    style={"background_color": 0x00000000})
                                hit.set_mouse_hovered_fn(self._on_play_hover)
                                hit.set_mouse_pressed_fn(self._on_play_press)
                                hit.set_mouse_released_fn(self._on_play_release)
                                self._play_hit = hit
                            return hit
                        btn_play = _vcenter(_PLAY_SIZE, _build_play)
                        ui.Spacer(width=8)
                        self._num_left = _vcenter(_NUM_W, lambda: self._build_num_label(
                            ui.Alignment.RIGHT_CENTER))     # 슬라이더 쪽(우)으로 붙임
                        ui.Spacer(width=6)
                        slider = _vcenter(_SLIDER_W, lambda: _ImageSlider(
                            width=_SLIDER_W, track_h=4, knob=10))
                        slider.model.add_value_changed_fn(self._on_slider)
                        ui.Spacer(width=6)
                        self._num_right = _vcenter(_NUM_W, lambda: self._build_num_label(
                            ui.Alignment.LEFT_CENTER))      # 슬라이더 쪽(좌)으로 붙임
                        ui.Spacer(width=12)

                    # 구분선 (y=50, h=1): 8 | 흰선 164x1 | 8
                    with ui.HStack(height=_DIV_H):
                        ui.Spacer(width=8)
                        ui.Rectangle(name="separator", width=164, height=1)
                        ui.Spacer(width=8)

                    # 행4 (y=51, h=33): 12 | 체크14 | 8 | Loop(폰트12) | x78 체크14 | 8 | Reverse
                    #   Reverse 체크박스가 행5 박스와 같은 x(78)에서 시작한다.
                    # 체크박스는 n(기본)/h(hover)/s(체크됨 또는 press) 세 이미지를
                    # 미리 로드해 visible만 토글한다(깜박임 없음, 노브와 동일 패턴).
                    with ui.HStack(height=_ROW4_H):
                        ui.Spacer(width=12)
                        def _build_loop():
                            n, h, s, hit = _build_tri_icon(
                                14, 14, _ICON_CHECKBOX_N,
                                _ICON_CHECKBOX_H, _ICON_CHECKBOX_S)
                            self._loop_imgs = (n, h, s)
                            hit.set_mouse_hovered_fn(self._on_loop_hover)
                            hit.set_mouse_pressed_fn(self._on_loop_press)
                            hit.set_mouse_released_fn(self._on_loop_release)
                            self._loop_hit = hit
                            return hit
                        loop_cb = _vcenter(14, _build_loop)
                        ui.Spacer(width=8)
                        # 라벨 폭으로 다음 체크박스를 x=78 에 맞춘다 (78-12-14-8=44)
                        ui.Label("Loop", width=_COL2_X - (12 + 14 + 8),
                                 height=_ROW4_H,
                                 alignment=ui.Alignment.LEFT_CENTER,
                                 style={"font_size": 12})
                        def _build_rev():
                            n, h, s, hit = _build_tri_icon(
                                14, 14, _ICON_CHECKBOX_N,
                                _ICON_CHECKBOX_H, _ICON_CHECKBOX_S)
                            self._rev_imgs = (n, h, s)
                            hit.set_mouse_hovered_fn(self._on_rev_hover)
                            hit.set_mouse_pressed_fn(self._on_rev_press)
                            hit.set_mouse_released_fn(self._on_rev_release)
                            self._rev_hit = hit
                            return hit
                        rev_cb = _vcenter(14, _build_rev)
                        ui.Spacer(width=8)
                        ui.Label("Reverse", height=_ROW4_H,
                                 alignment=ui.Alignment.LEFT_CENTER,
                                 style={"font_size": 12})
                        ui.Spacer()

                    # 행5 (y=84, h=30): 12 | "재생 속도"(폰트17) | x78 박스 90x18 | 12
                    #   가로 12 + 66 + 90 + 12 = 180. 박스는 행 상단 기준이라
                    #   아래로 30 - 18 = 12px 여백이 남는다.
                    with ui.HStack(height=_ROW5_H):
                        ui.Spacer(width=12)
                        with ui.VStack(width=_COL2_X - 12):
                            # 17pt 라인박스(≈20px)가 18px 밴드보다 커서 글자가
                            # 아래로 밀려 보인다. 툴팁 라벨과 같은 방식으로 Placer
                            # 픽셀 보정(_SPD_LABEL_OFFSET_Y)해서 박스와 눈높이를 맞춤.
                            with ui.ZStack(height=_SPD_BOX_H):
                                with ui.Placer(offset_x=0,
                                               offset_y=ui.Pixel(_SPD_LABEL_OFFSET_Y)):
                                    ui.Label("재생 속도", height=_SPD_BOX_H,
                                             alignment=ui.Alignment.LEFT_CENTER,
                                             style={"font_size": 17})
                            ui.Spacer()                  # 아래 12
                        with ui.VStack(width=_SPD_BOX_W):
                            self._build_spd_box()
                            ui.Spacer()                  # 아래 12
                        ui.Spacer(width=12)

                # 창 경로에선 프레임 레벨이 없으니 여기(body 마지막 자식)에 둔다.
                # 프레임 경로는 _build_in_frame 에서 패널 밖에 만든다(안 잘리게).
                if self._frame is None:
                    self._build_drop_popup()

        self._refresh_drop_colors()          # 초기 색 반영
        self._body = body
        self._title_content = title_content
        self._widgets = {
            'btn_play': btn_play,
            'btn_min':  btn_min,
            'rev_cb':   rev_cb,
            'loop_cb':  loop_cb,
            'slider':   slider,
            'num_left':  self._num_left,
            'num_right': self._num_right,
        }

    def _own_player(self):
        mixer = UVMixerService.get_instance(self._key)
        return mixer.own_player if mixer else None

    # ── player 콜백 ─────────────────────────────────────────────

    def _on_tick(self, t: float, correction: bool) -> None:
        if not UVMixerService.is_synced(self._tab_id):
            return
        self._in_tick = True
        self._widgets['slider'].model.set_value(t)
        self._in_tick = False

    def _on_own_tick(self, t: float, correction: bool) -> None:
        if UVMixerService.is_synced(self._tab_id):
            return
        self._in_tick = True
        self._widgets['slider'].model.set_value(t)
        self._in_tick = False

    def _set_play_icon(self, playing: bool) -> None:
        # 재생/정지 어느 아이콘을 보여줄지만 결정 (visible 토글, 깜박임 없음)
        self._stop_img.visible = playing
        self._play_img.visible = not playing

    def _set_play_tint(self, color: int) -> None:
        # color 스타일만 바꾸는 건 이미지 재로드가 아니라 즉시 반영됨(깜박임 없음)
        self._play_img.style = {"color": color}
        self._stop_img.style = {"color": color}

    def _on_stopped(self) -> None:
        if UVMixerService.is_synced(self._tab_id):
            self._set_play_icon(False)

    def _on_own_stopped(self) -> None:
        if not UVMixerService.is_synced(self._tab_id):
            self._set_play_icon(False)

    # ── 재생 버튼 hover/press (default/hover/press 3단) ────────────────

    def _on_play_hover(self, hovered: bool) -> None:
        self._play_hovering = hovered
        if not self._play_pressing:
            self._play_hover_ov.visible = hovered

    def _on_play_press(self, x, y, button, modifier) -> None:
        if button != 0:
            return
        self._play_pressing = True
        self._play_hover_ov.visible = False
        self._set_play_tint(_PRESS_TINT)

    def _on_play_release(self, x, y, button, modifier) -> None:
        if button != 0:
            return
        was_pressing = self._play_pressing
        self._play_pressing = False
        self._set_play_tint(_WHITE)
        inside = _point_in_widget(self._play_hit, x, y, 16, 16)
        self._play_hovering = inside
        self._play_hover_ov.visible = inside
        if was_pressing and inside:
            self._on_play()

    # ── 배속 버튼 hover/press (play와 동일 색상: hover=반투명 흰색, press=빨강) ──

    def _on_spd_hover(self, hovered: bool) -> None:
        self._spd_hovering = hovered
        if not self._spd_pressing:
            self._spd_hover_ov.visible = hovered

    def _on_spd_press(self, x, y, button, modifier) -> None:
        if button != 0:
            return
        self._spd_pressing = True
        self._spd_hover_ov.visible = False
        self._spd_label.style = {"font_size": 12, "color": _PRESS_TINT}

    def _on_spd_release(self, x, y, button, modifier) -> None:
        if button != 0:
            return
        was_pressing = self._spd_pressing
        self._spd_pressing = False
        self._spd_label.style = {"font_size": 12, "color": _WHITE}
        inside = _point_in_widget(self._spd_hit, x, y, 24, 24)
        self._spd_hovering = inside
        self._spd_hover_ov.visible = inside
        if was_pressing and inside:
            self._on_speed_cycle()

    # ── 컨트롤 콜백 ────────────────────────────────────────────

    def _on_play(self) -> None:
        if UVMixerService.is_synced(self._tab_id):
            sp = UVMixerService.get_shared_player(self._tab_id)
            if sp.is_playing():
                sp.stop()
            else:
                sp.play()
                self._set_play_icon(True)
                self._mgr._sync_play(self._key, playing=True)
        else:
            op = self._own_player()
            if not op:
                return
            if op.is_playing():
                op.stop()
            else:
                op.play()
                self._set_play_icon(True)

    def _on_slider(self, model) -> None:
        if self._in_tick:
            return
        t = model.get_value_as_float()
        if UVMixerService.is_synced(self._tab_id):
            sp = UVMixerService.get_shared_player(self._tab_id)
            if not sp.is_playing():
                sp.set_t(t)
        else:
            op = self._own_player()
            if op and not op.is_playing():
                op.set_t(t)

    def _paint_tri(self, imgs, checked: bool, hovering: bool, pressing: bool) -> None:
        # 체크됨 또는 누르는 중 → s(선택), 아니면 hover 중 → h, 아니면 n.
        n, h, s = imgs
        show_s = checked or pressing
        show_h = hovering and not show_s
        s.visible = show_s
        h.visible = show_h
        n.visible = not (show_s or show_h)

    def _refresh_rev(self) -> None:
        self._paint_tri(self._rev_imgs, self._reverse,
                        self._rev_hovering, self._rev_pressing)

    def _refresh_loop(self) -> None:
        self._paint_tri(self._loop_imgs, self._loop,
                        self._loop_hovering, self._loop_pressing)

    # ── 체크박스 hover/press (default/hover/press+checked) ─────────────

    def _on_rev_hover(self, hovered: bool) -> None:
        self._rev_hovering = hovered
        self._refresh_rev()

    def _on_rev_press(self, x, y, button, modifier) -> None:
        if button == 0:
            self._rev_pressing = True
            self._refresh_rev()

    def _on_rev_release(self, x, y, button, modifier) -> None:
        if button != 0:
            return
        was_pressing = self._rev_pressing
        self._rev_pressing = False
        self._rev_hovering = _point_in_widget(self._rev_hit, x, y, 14, 14)
        if was_pressing and self._rev_hovering:
            self._on_reverse()      # 내부에서 _refresh_rev() 호출됨
        else:
            self._refresh_rev()

    def _on_loop_hover(self, hovered: bool) -> None:
        self._loop_hovering = hovered
        self._refresh_loop()

    def _on_loop_press(self, x, y, button, modifier) -> None:
        if button == 0:
            self._loop_pressing = True
            self._refresh_loop()

    def _on_loop_release(self, x, y, button, modifier) -> None:
        if button != 0:
            return
        was_pressing = self._loop_pressing
        self._loop_pressing = False
        self._loop_hovering = _point_in_widget(self._loop_hit, x, y, 14, 14)
        if was_pressing and self._loop_hovering:
            self._on_loop()         # 내부에서 _refresh_loop() 호출됨
        else:
            self._refresh_loop()

    def _on_reverse(self) -> None:
        if self._in_sync:
            return
        self._reverse = not self._reverse
        self._refresh_rev()
        if UVMixerService.is_synced(self._tab_id):
            UVMixerService.get_shared_player(self._tab_id).set_forward(not self._reverse)
            self._mgr._sync_reverse(self._key, self._reverse)
        else:
            op = self._own_player()
            if op:
                op.set_forward(not self._reverse)

    def _on_loop(self) -> None:
        if self._in_sync:
            return
        self._loop = not self._loop
        self._refresh_loop()
        if UVMixerService.is_synced(self._tab_id):
            UVMixerService.get_shared_player(self._tab_id).set_loop(self._loop)
            self._mgr._sync_loop(self._key, self._loop)
        else:
            op = self._own_player()
            if op:
                op.set_loop(self._loop)

    def _on_speed_cycle(self) -> None:
        idx = SPEED_CYCLE.index(self._speed) if self._speed in SPEED_CYCLE else -1
        self._apply_speed(SPEED_CYCLE[(idx + 1) % len(SPEED_CYCLE)])

    def _apply_speed(self, spd: float, broadcast: bool = True) -> None:
        self._speed = spd
        if self._spd_label is not None:      # 배속 버튼 제거됨 — 있을 때만 갱신
            self._spd_label.text = _speed_label(spd)
        if not broadcast:
            return
        if UVMixerService.is_synced(self._tab_id):
            UVMixerService.get_shared_player(self._tab_id).set_speed(spd)
            self._mgr._sync_speed(self._key, spd)
        else:
            op = self._own_player()
            if op:
                op.set_speed(spd)

    # ── sync 상태 변경 시 즉각 갱신 ─────────────────────────────

    def refresh_from_player(self) -> None:
        """sync ON/OFF 토글 후 현재 활성 player 상태를 위젯에 반영한다."""
        if UVMixerService.is_synced(self._tab_id):
            p = UVMixerService.get_shared_player(self._tab_id)
        else:
            mixer = UVMixerService.get_instance(self._key)
            p = mixer.own_player if mixer else UVMixerService.get_shared_player(self._tab_id)
        self._in_sync = True
        self._in_tick = True
        self._reverse = not p.forward
        self._refresh_rev()
        self._loop = p.loop
        self._refresh_loop()
        self._apply_speed(p.speed, broadcast=False)
        self._widgets['slider'].model.set_value(p.t)
        self._in_sync = False
        self._in_tick = False

    # ── 외부 sync 수신 ──────────────────────────────────────────

    def sync_reverse(self, reverse: bool) -> None:
        self._in_sync = True
        self._reverse = reverse
        self._refresh_rev()
        self._in_sync = False

    def sync_loop(self, loop: bool) -> None:
        self._in_sync = True
        self._loop = loop
        self._refresh_loop()
        self._in_sync = False

    def sync_speed(self, spd: float) -> None:
        self._apply_speed(spd, broadcast=False)

    def sync_play(self, playing: bool) -> None:
        self._set_play_icon(playing)

    # ── 뷰포트 리사이즈 대응 ────────────────────────────────────

    def _on_toggle_minimize(self) -> None:
        self._minimized = not self._minimized
        # 프레임 경로엔 mini 창이 없으니 이 버튼이 상태를 표시한다(창 경로는
        # 최소화 시 본 창째로 숨겨져서 어느 쪽이 보이든 무관).
        self._ico_min.visible  = self._minimized
        self._ico_open.visible = not self._minimized
        if self._panel_row is not None:
            # 프레임 경로: 창이 없으니 스왑도 불필요. 본문을 접고 행을 줄인다.
            # 깜박임 방지로 줄일 땐 안→밖, 키울 땐 밖→안 순서.
            if self._minimized:
                self._body.visible = False
                self._body.height = ui.Pixel(0)
                self._panel_row.height = ui.Pixel(_ROW1_H)
            else:
                self._panel_row.height = ui.Pixel(OVERLAY_H)
                self._body.height = ui.Pixel(OVERLAY_H - _ROW1_H)
                self._body.visible = True
            return
        # 창 경로: 런타임 window 리사이즈가 안 먹으므로 본 창 ↔ 16x16 mini 창 스왑.
        self._window.visible = not self._minimized
        self._min_window.visible = self._minimized
        self._on_viewport_resized()

    def _on_viewport_resized(self) -> None:
        # 두 창 모두 우하단 앵커. 각자 자기 크기 기준으로 위치 계산.
        # 프레임 경로는 레이아웃이 앵커하므로 할 일이 없다.
        if not self._vph or self._frame is not None:
            return
        if self._window:
            x, y = _calc_overlay_pos(self._vph)
            self._window.position_x = x
            self._window.position_y = y
        if self._min_window:
            mx, my = _calc_overlay_pos(self._vph,
                                       width=OVERLAY_W_MIN,
                                       height=OVERLAY_H_MIN,
                                       extra_margin_y=_MARGIN)
            self._min_window.position_x = mx
            self._min_window.position_y = my

    def reposition_deferred(self, frames: int = 2) -> None:
        """show 직후 frame이 새 레이아웃을 반영하기 전이라 위치가 stale할 수 있다.
        computed-size 콜백이 매번 울린다는 보장이 없으므로, 몇 프레임 뒤
        레이아웃이 적용된 시점에 위치를 강제로 재계산한다."""
        if self._reposition_task is not None and not self._reposition_task.done():
            self._reposition_task.cancel()
        self._reposition_task = omni.kit.async_engine.run_coroutine(
            self._reposition_async(frames))

    async def _reposition_async(self, frames: int) -> None:
        app = omni.kit.app.get_app()
        for _ in range(frames):
            await app.next_update_async()
        self._on_viewport_resized()

    # ── 표시/숨김 (최소화 상태에 맞는 창만) ──────────────────────────

    def set_visible(self, visible: bool) -> None:
        if self._panel_row is not None:
            self._panel_row.visible = visible      # 프레임 경로: 패널 행만 토글
            return
        # 창 경로: 현재 최소화 상태에 맞는 창만 보이게 한다.
        if not visible:
            self._window.visible = False
            self._min_window.visible = False
            return
        self._window.visible = not self._minimized
        self._min_window.visible = self._minimized

    # ── 라이프사이클 ────────────────────────────────────────────

    def destroy(self) -> None:
        if self._reposition_task is not None and not self._reposition_task.done():
            self._reposition_task.cancel()
            self._reposition_task = None
        if self._vph:
            if self._frame is None:                # 창 경로에서만 걸어둔 콜백
                self._vph.frame.set_computed_content_size_changed_fn(None)
            self._vph = None
        self._mute_top(False)                      # 꺼뒀던 상위 프레임 입력 복구
        self._top_frame = None
        if self._frame is not None:
            # host 를 clear 하면 같은 컨테이너를 쓰는 다른 UI 까지 날아간다.
            # 우리가 만든 루트만 비우고 숨긴다.
            if self._root is not None:
                self._root.clear()
                self._root.visible = False
                self._root = None
            self._frame = None
            self._panel_row = None
        sp = UVMixerService.get_shared_player(self._tab_id)
        sp.unsubscribe_tick(self._on_tick)
        sp.unsubscribe_stopped(self._on_stopped)
        mixer = UVMixerService.get_instance(self._key)
        if mixer:
            mixer.own_player.unsubscribe_tick(self._on_own_tick)
            mixer.own_player.unsubscribe_stopped(self._on_own_stopped)
        slider = self._widgets.get('slider')
        if slider:
            slider.destroy()          # 슬라이더 툴팁 윈도우 정리
        if self._min_window:
            self._min_window.destroy()
            self._min_window = None
        if self._window:
            self._window.destroy()
            self._window = None


class OverlayManager:

    def __init__(self):
        self._panels: dict[str, ViewportOverlayPanel] = {}

    def is_on(self, key: str) -> bool:
        return key in self._panels

    def refresh_all(self) -> None:
        for panel in self._panels.values():
            panel.refresh_from_player()

    def on_mixer_loaded(self, key: str, target_path: str, tab_id: 'str | None',
                        *, visible: bool = True, frame=None, top_frame=None) -> None:
        # frame 을 주면 창 대신 그 뷰포트 오버레이 프레임 위에 패널을 얹는다.
        # top_frame(= 뒤에 선언돼 패널을 덮는 프레임)을 같이 주면, 커서가 패널
        # 위일 때만 그 프레임 입력을 꺼서 클릭이 패널로 내려온다.
        self._remove_panel(key)
        vph = _find_vph(target_path)
        if vph is None:
            return
        panel = ViewportOverlayPanel(key, vph, mgr=self, tab_id=tab_id,
                                     frame=frame, top_frame=top_frame)
        panel.refresh_from_player()
        if not visible:
            panel.set_visible(False)
        self._panels[key] = panel

    def on_mixer_destroyed(self, key: str) -> None:
        self._remove_panel(key)

    def show(self, key: str) -> None:
        panel = self._panels.get(key)
        if panel:
            panel.set_visible(True)
            panel._on_viewport_resized()
            panel.reposition_deferred()

    def hide(self, key: str) -> None:
        panel = self._panels.get(key)
        if panel:
            panel.set_visible(False)

    def _remove_panel(self, key: str) -> None:
        panel = self._panels.pop(key, None)
        if panel:
            panel.destroy()

    # ── 교차 패널 동기화 ────────────────────────────────────────

    def _sync_reverse(self, src: str, reverse: bool) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_reverse(reverse)

    def _sync_loop(self, src: str, loop: bool) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_loop(loop)

    def _sync_speed(self, src: str, spd: float) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_speed(spd)

    def _sync_play(self, src: str, playing: bool) -> None:
        for k, p in self._panels.items():
            if k != src:
                p.sync_play(playing)

    def clear_panels(self) -> None:
        """모든 패널을 destroy하고 레지스트리를 비운다 (mixer Clear 시 호출)."""
        for panel in list(self._panels.values()):
            panel.destroy()
        self._panels.clear()

    def destroy(self) -> None:
        for panel in list(self._panels.values()):
            panel.destroy()
        self._panels.clear()
