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
#   usd_interpolation/data/icons/checkbox_h.png  (체크박스 hover — 현재 미사용, n/s만 토글)
#   usd_interpolation/data/icons/checkbox_s.png  (체크박스 선택됨)
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

# ── 패널 치수 명세 (전체 180x80) ────────────────────────────────────────
OVERLAY_W = 180
OVERLAY_H = 80
OVERLAY_W_MIN = 16          # 최소화 시 너비 (버튼만)
OVERLAY_H_MIN = 16          # 최소화 시 높이 (버튼만)
_MARGIN = 8                 # 뷰포트 가장자리 여백

# 행 높이 (세로 누적: 16 + 30 + 1 + 33 = 80)
_ROW1_H = 16                # 타이틀
_ROW2_H = 30                # 재생/슬라이더/배속
_DIV_H  = 1                 # 구분선
_ROW4_H = 33                # Reverse/Loop

_TITLE_BG = 0xFF000000      # 타이틀바: 진한 검정
_BODY_BG  = 0xFF3C3C3C      # 본문: 진한 회색
_WHITE    = 0xFFFFFFFF      # 본문/타이틀 텍스트·버튼 전부 흰색

SPEED_CYCLE = [1.0, 2.0, 4.0, 0.5]     # 클릭할 때마다 x1 → x2 → x4 → x0.5 → x1 ...


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
        self._hovering = False     # 마우스가 핸들(10x10) 위에 있는지
        self.model = ui.SimpleFloatModel(0.0)
        self.model.add_value_changed_fn(lambda m: self._update_visual())
        self._build()
        self._update_visual()

    def _build(self):
        with ui.ZStack(width=self._w, height=self._ks):
            # 드래그 안전망: 트랙 전체에서 move/release 받음 (press 없음 →
            # 드래그 시작은 핸들 위에서만). 맨 아래라 핸들 쪽이 우선.
            self._safety = ui.Rectangle(style={"background_color": 0x00000000})
            self._safety.set_mouse_moved_fn(self._on_move)
            self._safety.set_mouse_released_fn(self._on_release)

            # 트랙 + 채움 (세로 중앙)
            with ui.VStack():
                ui.Spacer()
                with ui.ZStack(height=self._th):
                    ui.Rectangle(style={"background_color": _SLIDER_TRACK_BG,
                                        "border_radius": self._th / 2})
                    # 채움: Placer 안에 두고 Rectangle.width로 크기 조절
                    self._fill_placer = ui.Placer()
                    with self._fill_placer:
                        self._fill = ui.Rectangle(
                            width=0, height=self._th,
                            style={"background_color": _SLIDER_FILL_BG,
                                   "border_radius": self._th / 2})
                ui.Spacer()

            # 핸들 (맨 위): Placer.offset_x로 위치. n/h 이미지 미리 로드 후
            # visible 토글(깜박임 없음). 투명 hit이 마우스 담당.
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
                    self._knob_hit = ui.Rectangle(
                        style={"background_color": 0x00000000})
                    self._knob_hit.set_mouse_hovered_fn(self._on_knob_hover)
                    self._knob_hit.set_mouse_pressed_fn(self._on_press)
                    self._knob_hit.set_mouse_moved_fn(self._on_move)
                    self._knob_hit.set_mouse_released_fn(self._on_release)

    # ── 값 ↔ 픽셀 ────────────────────────────────────────────────

    def _t_from_screen_x(self, x: float) -> float:
        # 커서를 핸들 중앙에 두는 매핑. safety는 트랙 왼쪽 끝(0)에 고정.
        local = x - self._safety.screen_position_x - self._ks / 2
        span = self._w - self._ks
        return max(0.0, min(1.0, local / span)) if span > 0 else 0.0

    def _update_visual(self):
        try:
            t = self.model.get_value_as_float()
            self._fill.visible = t > 0.0
            self._fill.width = ui.Pixel(t * self._w)
            self._knob_placer.offset_x = ui.Pixel(t * (self._w - self._ks))
            print(f"[_ImageSlider] update t={t:.3f} "
                  f"fill_w={t*self._w:.1f} knob_off={t*(self._w-self._ks):.1f}")
        except Exception as e:
            import traceback
            print("[_ImageSlider] _update_visual FAILED:", e)
            traceback.print_exc()

    def _refresh_knob_image(self):
        # 미리 로드된 두 이미지의 visible만 토글 (재로드 없음 → 깜박임 없음)
        hov = self._hovering or self._dragging
        self._knob_h.visible = hov
        self._knob_n.visible = not hov

    # ── 마우스 (핸들 위에서만 반응) ────────────────────────────────

    def _on_knob_hover(self, hovered: bool):
        self._hovering = hovered
        self._refresh_knob_image()

    def _on_press(self, x, y, button, modifier):
        if button != 0:
            return
        self._dragging = True
        self._refresh_knob_image()

    def _on_move(self, x, y, modifier, pressed):
        if self._dragging:
            self.model.set_value(self._t_from_screen_x(x))

    def _on_release(self, x, y, button, modifier):
        if button != 0:
            return
        self._dragging = False
        x0 = self._knob_hit.screen_position_x
        self._hovering = (x0 <= x <= x0 + self._ks)
        self._refresh_knob_image()


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

    def __init__(self, key: str, vph, mgr: 'OverlayManager', tab_id: 'str | None'):
        self._key = key
        self._mgr = mgr
        self._vph = vph
        self._tab_id = tab_id
        self._in_tick = False
        self._in_sync = False
        self._reposition_task = None
        self._minimized = False
        self._speed = 1.0
        self._reverse = False
        self._loop = False

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
        self._widgets: dict = {}
        self._build()

        vph.frame.set_computed_content_size_changed_fn(self._on_viewport_resized)

        sp = UVMixerService.get_shared_player(self._tab_id)
        sp.subscribe_tick(self._on_tick)
        sp.subscribe_stopped(self._on_stopped)

        mixer = UVMixerService.get_instance(key)
        if mixer:
            mixer.own_player.subscribe_tick(self._on_own_tick)
            mixer.own_player.subscribe_stopped(self._on_own_stopped)

    def _build(self) -> None:
        with self._window.frame:
            with ui.VStack(spacing=0, style=_STYLE):     # 스타일시트는 여기 한 번만

                # ── 행1 (y=0, h=16): 검정 타이틀바 ──────────────────────
                #   3 여백 | 최소화 10x10(중앙) | 2 여백 | Animation(폰트10,h15,윗점)
                with ui.ZStack(height=_ROW1_H):
                    ui.Rectangle(name="title_bg")
                    with ui.HStack():
                        ui.Spacer(width=3)
                        btn_min = _vcenter(10, lambda: ui.Button(
                            "", width=10, height=10, name="min_btn",
                            alignment=ui.Alignment.CENTER,
                            clicked_fn=self._on_toggle_minimize,
                            style={"image_url": _ICON_ARROW,
                                   "fill_policy": ui.FillPolicy.STRETCH}))
                        ui.Spacer(width=2)
                        title_content = ui.HStack()       # 최소화 시 숨김
                        with title_content:
                            with ui.VStack():             # 윗점(top) 정렬
                                ui.Label(self._key, height=15,
                                         style={"font_size": 10})
                                ui.Spacer()

                # ── 본문 (행2·구분선·행4). 최소화 시 통째로 숨김 ─────────
                body = ui.ZStack()
                with body:
                    ui.Rectangle(name="body_bg")
                    with ui.VStack(spacing=0):

                        # 행2 (y=16, h=30): 12 | 재생16 | 5 | 슬라이더112(h10) | 3 | 배속24 | 8
                        with ui.HStack(height=_ROW2_H):
                            ui.Spacer(width=12)
                            btn_play = _vcenter(16, lambda: ui.Button(
                                "", width=16, height=16,
                                alignment=ui.Alignment.CENTER,
                                clicked_fn=self._on_play,
                                style={"image_url": _ICON_PLAY,
                                       "fill_policy": ui.FillPolicy.STRETCH}))
                            ui.Spacer(width=5)
                            slider = _vcenter(112, lambda: _ImageSlider(
                                width=112, track_h=4, knob=10))
                            slider.model.add_value_changed_fn(self._on_slider)
                            ui.Spacer(width=3)
                            spd_btn = _vcenter(24, lambda: ui.Button(
                                _speed_label(self._speed), width=24, height=24,
                                alignment=ui.Alignment.CENTER,
                                clicked_fn=self._on_speed_cycle,
                                style={"font_size": 12}))
                            ui.Spacer(width=8)

                        # 구분선 (y=46, h=1): 8 | 흰선 164x1 | 8
                        with ui.HStack(height=_DIV_H):
                            ui.Spacer(width=8)
                            ui.Rectangle(name="separator", width=164, height=1)
                            ui.Spacer(width=8)

                        # 행4 (y=47, h=33): 12 | 체크14 | 8 | Reverse(폰트12) | 20 | 체크14 | 8 | Loop(폰트12)
                        # 체크박스는 재생 버튼과 같은 방식(ui.Button + image_url 인라인 style)으로
                        # 만든다. ui.CheckBox의 타입 캐스케이딩으로는 이미지가 안 나왔었다
                        # (CheckBox가 image_url 스타일을 지원 안 하는 것으로 보임).
                        with ui.HStack(height=_ROW4_H):
                            ui.Spacer(width=12)
                            rev_cb = _vcenter(14, lambda: ui.Button(
                                "", width=14, height=14, name="rev_cb",
                                alignment=ui.Alignment.CENTER,
                                clicked_fn=self._on_reverse,
                                style={"image_url": _ICON_CHECKBOX_N,
                                       "fill_policy": ui.FillPolicy.STRETCH}))
                            ui.Spacer(width=8)
                            ui.Label("Reverse", height=_ROW4_H,
                                     alignment=ui.Alignment.LEFT_CENTER,
                                     style={"font_size": 12})
                            ui.Spacer(width=20)
                            loop_cb = _vcenter(14, lambda: ui.Button(
                                "", width=14, height=14, name="loop_cb",
                                alignment=ui.Alignment.CENTER,
                                clicked_fn=self._on_loop,
                                style={"image_url": _ICON_CHECKBOX_N,
                                       "fill_policy": ui.FillPolicy.STRETCH}))
                            ui.Spacer(width=8)
                            ui.Label("Loop", height=_ROW4_H,
                                     alignment=ui.Alignment.LEFT_CENTER,
                                     style={"font_size": 12})
                            ui.Spacer()

        self._body = body
        self._title_content = title_content
        self._widgets = {
            'btn_play': btn_play,
            'btn_min':  btn_min,
            'rev_cb':   rev_cb,
            'loop_cb':  loop_cb,
            'slider':   slider,
            'spd_btn':  spd_btn,
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
        self._widgets['btn_play'].style = {
            "image_url": _ICON_STOP if playing else _ICON_PLAY,
            "fill_policy": ui.FillPolicy.STRETCH,
        }

    def _on_stopped(self) -> None:
        if UVMixerService.is_synced(self._tab_id):
            self._set_play_icon(False)

    def _on_own_stopped(self) -> None:
        if not UVMixerService.is_synced(self._tab_id):
            self._set_play_icon(False)

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

    def _set_checkbox_icon(self, widget, checked: bool) -> None:
        widget.style = {
            "image_url": _ICON_CHECKBOX_S if checked else _ICON_CHECKBOX_N,
            "fill_policy": ui.FillPolicy.STRETCH,
        }

    def _on_reverse(self) -> None:
        if self._in_sync:
            return
        self._reverse = not self._reverse
        self._set_checkbox_icon(self._widgets['rev_cb'], self._reverse)
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
        self._set_checkbox_icon(self._widgets['loop_cb'], self._loop)
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
        self._widgets['spd_btn'].text = _speed_label(spd)
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
        self._set_checkbox_icon(self._widgets['rev_cb'], self._reverse)
        self._loop = p.loop
        self._set_checkbox_icon(self._widgets['loop_cb'], self._loop)
        self._apply_speed(p.speed, broadcast=False)
        self._widgets['slider'].model.set_value(p.t)
        self._in_sync = False
        self._in_tick = False

    # ── 외부 sync 수신 ──────────────────────────────────────────

    def sync_reverse(self, reverse: bool) -> None:
        self._in_sync = True
        self._reverse = reverse
        self._set_checkbox_icon(self._widgets['rev_cb'], reverse)
        self._in_sync = False

    def sync_loop(self, loop: bool) -> None:
        self._in_sync = True
        self._loop = loop
        self._set_checkbox_icon(self._widgets['loop_cb'], loop)
        self._in_sync = False

    def sync_speed(self, spd: float) -> None:
        self._apply_speed(spd, broadcast=False)

    def sync_play(self, playing: bool) -> None:
        self._set_play_icon(playing)

    # ── 뷰포트 리사이즈 대응 ────────────────────────────────────

    def _on_toggle_minimize(self) -> None:
        self._minimized = not self._minimized
        self._title_content.visible = not self._minimized
        self._body.visible = not self._minimized
        self._widgets['btn_min'].style = {
            "image_url": _ICON_ARROW_R if self._minimized else _ICON_ARROW,
            "fill_policy": ui.FillPolicy.STRETCH,
        }
        if self._minimized:
            self._window.width = OVERLAY_W_MIN
            self._window.height = OVERLAY_H_MIN
        else:
            self._window.width = OVERLAY_W
            self._window.height = OVERLAY_H
        self._on_viewport_resized()

    def _on_viewport_resized(self) -> None:
        if self._window and self._vph:
            if self._minimized:
                x, y = _calc_overlay_pos(self._vph,
                                         width=OVERLAY_W_MIN,
                                         height=OVERLAY_H_MIN,
                                         extra_margin_y=_MARGIN)
            else:
                x, y = _calc_overlay_pos(self._vph)
            self._window.position_x = x
            self._window.position_y = y

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

    # ── 라이프사이클 ────────────────────────────────────────────

    def destroy(self) -> None:
        if self._reposition_task is not None and not self._reposition_task.done():
            self._reposition_task.cancel()
            self._reposition_task = None
        if self._vph:
            self._vph.frame.set_computed_content_size_changed_fn(None)
            self._vph = None
        sp = UVMixerService.get_shared_player(self._tab_id)
        sp.unsubscribe_tick(self._on_tick)
        sp.unsubscribe_stopped(self._on_stopped)
        mixer = UVMixerService.get_instance(self._key)
        if mixer:
            mixer.own_player.unsubscribe_tick(self._on_own_tick)
            mixer.own_player.unsubscribe_stopped(self._on_own_stopped)
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
                        *, visible: bool = True) -> None:
        self._remove_panel(key)
        vph = _find_vph(target_path)
        if vph is None:
            return
        panel = ViewportOverlayPanel(key, vph, mgr=self, tab_id=tab_id)
        panel.refresh_from_player()
        if not visible:
            panel._window.visible = False
        self._panels[key] = panel

    def on_mixer_destroyed(self, key: str) -> None:
        self._remove_panel(key)

    def show(self, key: str) -> None:
        panel = self._panels.get(key)
        if panel and panel._window:
            panel._window.visible = True
            panel._on_viewport_resized()
            panel.reposition_deferred()

    def hide(self, key: str) -> None:
        panel = self._panels.get(key)
        if panel and panel._window:
            panel._window.visible = False

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
