import functools

import omni.ui as ui

from .annotation_service import AnnotationService

__all__ = ["AnnotationDummyUI"]

WINDOW_TITLE = "Annotation"             # 시험 창 제목
WINDOW_WIDE = 380                       # 시험 창 폭
WINDOW_HIGH = 420                       # 시험 창 높이
LIST_HIGH = 120                         # 마커 목록 칸 높이
ROW_HIGH = 22                           # 마커 목록 한 줄 높이
FIELD_LABEL_WIDE = 80                   # 글 칸 앞 이름 폭
FONT_FIELD_WIDE = 50                    # 글자 크기 칸 폭
KIND_BUTTONS = (("label", "Label"), ("inlet", "Inlet"), ("outlet", "Outlet"))  # 만들기 버튼 (종류, 글)
QUIET_REASONS = ("panel",)              # 시험 창이 다시 채우지 않는 알림 (끄는 중 잦은 것)
PICKED_ROW_BG = 0xFF7A5A2A              # 목록에서 고른 줄 바탕 (0xAABBGGRR)
ROW_BG = 0xFF2A2A2A                     # 목록 줄 바탕
STATUS_COLOR = 0xFFAAAAAA               # 상태 글자색


class AnnotationDummyUI:
    """시험용 창. AnnotationService 만 부른다"""

    def __init__(self):
        """위젯 참조 자리만. 구성은 build_ui"""
        self._window = None
        self._status = None
        self._list = None
        self._picked = None
        self._label = None
        self._desc = None
        self._font = None
        self._shown_id = None

    def build_ui(self):
        """만들기 버튼, 상태, 마커 목록, 고른 마커의 글 칸"""
        self._window = ui.Window(WINDOW_TITLE, width=WINDOW_WIDE, height=WINDOW_HIGH,
                                 dockPreference=ui.DockPreference.RIGHT_BOTTOM)
        with self._window.frame:
            with ui.VStack(spacing=5, style={"margin": 3}):
                with ui.HStack(height=0, spacing=4):
                    for kind, text in KIND_BUTTONS:
                        ui.Button(text, clicked_fn=functools.partial(AnnotationService.create, kind))
                    ui.Button("취소", clicked_fn=AnnotationService.cancel_create)
                self._status = ui.Label("", height=0, word_wrap=True,
                                        style={"color": STATUS_COLOR})
                ui.Separator(height=6)
                ui.Label("마커", height=0)
                with ui.ScrollingFrame(height=LIST_HIGH):
                    self._list = ui.VStack(height=0, spacing=1)
                ui.Separator(height=6)
                with ui.HStack(height=0, spacing=4):
                    self._picked = ui.Label("선택: 없음")
                    ui.Label("글자 크기", width=0)
                    self._font = ui.FloatField(width=FONT_FIELD_WIDE)
                    self._font.model.add_end_edit_fn(lambda _m: self._apply_font())
                with ui.HStack(height=0, spacing=4):
                    ui.Label("label", width=FIELD_LABEL_WIDE)
                    self._label = ui.StringField()
                with ui.HStack(height=0, spacing=4):
                    ui.Label("description", width=FIELD_LABEL_WIDE)
                    self._desc = ui.StringField()
                with ui.HStack(height=0, spacing=4):
                    ui.Button("적용", clicked_fn=self._apply)
                    ui.Button("패널 원위치", clicked_fn=self._reset)
                    ui.Button("삭제", clicked_fn=self._remove)
                    ui.Button("선택 해제", clicked_fn=functools.partial(AnnotationService.select, None))
        AnnotationService.add_listener(self._on_change)
        self._refresh(fill=True)

    # -- 버튼 -------------------------------------------------------------------

    def _apply(self):
        """칸의 글을 고른 마커에"""
        AnnotationService.set_text(AnnotationService.selected(),
                                   self._label.model.get_value_as_string(),
                                   self._desc.model.get_value_as_string())

    def _apply_font(self):
        """칸의 글자 크기를 고른 마커에. 안 되면(범위 밖 등) 칸을 지금 값으로 되돌린다"""
        sel = AnnotationService.selected()
        if not AnnotationService.set_font_size(sel, self._font.model.get_value_as_float()):
            self._fill_font(AnnotationService.get(sel) if sel else None)

    def _reset(self):
        """고른 마커의 패널을 처음 자리로"""
        AnnotationService.reset_panel(AnnotationService.selected())

    def _remove(self):
        """고른 마커를 지운다"""
        AnnotationService.remove(AnnotationService.selected())

    # -- 다시 채우기 --------------------------------------------------------------

    def _on_change(self, reason: str):
        """끄는 중의 잦은 알림은 넘긴다. 패널을 클릭하면 그 패널의 글을 칸에 채운다"""
        if reason in QUIET_REASONS:
            return
        if reason == "font":
            sel = AnnotationService.selected()
            self._fill_font(AnnotationService.get(sel) if sel else None)
            return
        self._refresh(fill=reason in ("text", "click"))

    def _refresh(self, fill: bool = False):
        """상태, 목록, 고른 마커. 고른 것이 바뀌었을 때만 글 칸을 덮어쓴다"""
        if self._window is None:
            return
        self._status.text = AnnotationService.status()
        self._fill_list()
        sel = AnnotationService.selected()
        one = AnnotationService.get(sel) if sel else None
        self._picked.text = f"선택: {sel} ({one['kind']})" if one else "선택: 없음"
        if fill or sel != self._shown_id:
            self._shown_id = sel
            self._label.model.set_value(one["label"] if one else "")
            self._desc.model.set_value(one["description"] if one else "")
            self._fill_font(one)

    def _fill_font(self, one):
        """글자 크기 칸. 고른 것이 없으면 0"""
        if self._font is not None:
            self._font.model.set_value(float(one.get("font_px") or 0.0) if one else 0.0)

    def _fill_list(self):
        """마커마다 한 줄. 누르면 고른다"""
        self._list.clear()
        sel = AnnotationService.selected()
        with self._list:
            markers = AnnotationService.markers()
            if not markers:
                ui.Label("(없음)", height=ROW_HIGH, style={"color": STATUS_COLOR})
            for m in markers:
                text = f"[{m['kind']}] {m['label'] or '(이름 없음)'}   ({m['id']})"
                ui.Button(text, height=ROW_HIGH, alignment=ui.Alignment.LEFT_CENTER,
                          style={"Button": {"background_color": PICKED_ROW_BG if m["id"] == sel else ROW_BG}},
                          clicked_fn=functools.partial(AnnotationService.select, m["id"]))

    # -- 창 ---------------------------------------------------------------------

    def show(self, on: bool) -> None:
        """창을 띄우거나 가린다"""
        if self._window is not None:
            self._window.visible = bool(on)

    def destroy(self):
        """알림을 끊고 창을 닫는다"""
        AnnotationService.remove_listener(self._on_change)
        if self._window:
            self._window.destroy()
            self._window = None
