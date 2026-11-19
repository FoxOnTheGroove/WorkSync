import asyncio
import functools

import omni.ui as ui

from .annotation_service import AnnotationService

__all__ = ["AnnotationDummyUI"]

WINDOW_TITLE = "Annotation"             # 시험 창 제목
WINDOW_WIDE = 380                       # 시험 창 폭
WINDOW_HIGH = 600                       # 시험 창 높이
LIST_HIGH = 120                         # 마커 목록 칸 높이
ROW_HIGH = 22                           # 마커 목록 한 줄 높이
FIELD_LABEL_WIDE = 80                   # 글 칸 앞 이름 폭
FONT_FIELD_WIDE = 50                    # 글자 크기 칸 폭
DESC_FIELD_HIGH = 70                    # description 칸 높이 (여러 줄, 엔터로 줄바꿈)
KIND_BUTTONS = (("label", "Label"), ("inlet", "Inlet"), ("outlet", "Outlet"))  # 만들기 버튼 (종류, 글)
PLANE_BUTTONS = (("xy", "XY"), ("yz", "YZ"), ("zx", "ZX"))  # 평면 만들기 버튼 (축, 글). 원점을 지나는 평면
PLANE_FIELD_WIDE = 70                   # 평면 위치, 크기 칸 폭
QUIET_REASONS = ("panel",)              # 시험 창이 다시 채우지 않는 알림 (끄는 중 잦은 것)
LIST_REASONS = ("add", "remove", "text", "load")  # 목록 줄을 다시 짓는 알림 (그 밖에는 줄 색만 바꾼다)
PICKED_ROW_BG = 0xFF7A5A2A              # 목록에서 고른 줄 바탕 (0xAABBGGRR)
ROW_BG = 0xFF2A2A2A                     # 목록 줄 바탕
EYE_WIDE = 50                           # 목록 줄 오른쪽 보이기 단추 폭
HIDDEN_TEXT_COLOR = 0xFF777777          # 숨긴 마커 줄 글자색
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
        self._arrow = None
        self._usd = None
        self._file = None
        self._plane_at = None
        self._plane_w = None
        self._plane_h = None
        self._shown_id = None
        self._rows = {}  # 마커 id -> (목록 줄 버튼, 보이기 단추)
        self._all_eye = None
        self._rebuild_task = None

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
                with ui.HStack(height=0, spacing=4):
                    for axis, text in PLANE_BUTTONS:
                        ui.Button(text, clicked_fn=functools.partial(AnnotationService.create_plane, axis))
                with ui.HStack(height=0, spacing=4):
                    ui.Label("형상 USD", width=FIELD_LABEL_WIDE)
                    self._usd = ui.StringField()
                    ui.Button("로드", width=60, clicked_fn=self._load_usd)
                with ui.HStack(height=0, spacing=4):
                    self._file = ui.Label("마커 파일: (형상 USD 없음)", elided_text=True,
                                          style={"color": STATUS_COLOR})
                    ui.Button("저장", width=60, clicked_fn=self._save)
                    ui.Button("불러오기", width=80, clicked_fn=AnnotationService.load)
                    ui.Button("진단", width=50, clicked_fn=self._diagnose)
                self._status = ui.Label("", height=0, word_wrap=True,
                                        style={"color": STATUS_COLOR})
                ui.Separator(height=6)
                with ui.HStack(height=0, spacing=4):
                    ui.Label("마커")
                    self._all_eye = ui.Button("전체 숨기기", width=90, clicked_fn=self._toggle_all)
                with ui.ScrollingFrame(height=LIST_HIGH):
                    self._list = ui.VStack(height=0, spacing=1)
                ui.Separator(height=6)
                with ui.HStack(height=0, spacing=4):
                    self._picked = ui.Label("선택: 없음")
                    ui.Label("화살표 x", width=0)
                    self._arrow = ui.FloatField(width=FONT_FIELD_WIDE)
                    self._arrow.model.add_end_edit_fn(lambda _m: self._apply_arrow())
                    ui.Label("글자 크기", width=0)
                    self._font = ui.FloatField(width=FONT_FIELD_WIDE)
                    self._font.model.add_end_edit_fn(lambda _m: self._apply_font())
                with ui.HStack(height=0, spacing=4):
                    ui.Label("label", width=FIELD_LABEL_WIDE)
                    self._label = ui.StringField()
                with ui.HStack(height=DESC_FIELD_HIGH, spacing=4):
                    ui.Label("description", width=FIELD_LABEL_WIDE, alignment=ui.Alignment.LEFT_TOP)
                    self._desc = ui.StringField(multiline=True)
                with ui.HStack(height=0, spacing=4):
                    ui.Label("위치", width=FIELD_LABEL_WIDE)
                    self._plane_at = ui.FloatField(width=PLANE_FIELD_WIDE)
                    self._plane_at.model.add_end_edit_fn(lambda _m: self._apply_plane_at())
                    ui.Label("크기", width=0)
                    self._plane_w = ui.FloatField(width=PLANE_FIELD_WIDE)
                    self._plane_w.model.add_end_edit_fn(lambda _m: self._apply_plane_size())
                    ui.Label("x", width=0)
                    self._plane_h = ui.FloatField(width=PLANE_FIELD_WIDE)
                    self._plane_h.model.add_end_edit_fn(lambda _m: self._apply_plane_size())
                with ui.HStack(height=0, spacing=4):
                    ui.Button("적용", clicked_fn=self._apply)
                    ui.Button("패널 원위치", clicked_fn=self._reset)
                    ui.Button("위치 재설정", clicked_fn=lambda: AnnotationService.relocate(AnnotationService.selected()))
                    ui.Button("삭제", clicked_fn=self._remove)
                    ui.Button("선택 해제", clicked_fn=functools.partial(AnnotationService.select, None))
        AnnotationService.add_listener(self._on_change)
        self._arrow.model.set_value(AnnotationService.arrow_scale())
        self._refresh(fill=True)

    # -- 버튼 -------------------------------------------------------------------

    def _load_usd(self):
        """시험용: 칸의 USD 를 씬에 열고, 열리면 그 경로를 마커의 형상으로 넘긴다.
        여는 것은 익스텐션 일이 아니라 이 창에서만 한다"""
        path = self._usd.model.get_value_as_string().strip()
        if not path:
            self._status.text = "형상 USD 경로를 넣으세요"
            return
        try:
            import omni.usd
            told = omni.usd.get_context().open_stage(path)
        except Exception as e:
            self._status.text = f"열지 못했습니다: {type(e).__name__}: {e}"
            return
        ok = told[0] if isinstance(told, tuple) else told
        if ok is False:
            why = str(told[1]) if isinstance(told, tuple) and len(told) > 1 else "열리지 않았습니다"
            self._status.text = f"열지 못했습니다: {why}"
            return
        AnnotationService.set_target_usd(path)

    def _diagnose(self):
        """지금 상태를 콘솔에 찍는다"""
        info = AnnotationService.diagnose()
        print("[annotation] ---- 진단 ----")
        print(f"[annotation] registered={info['registered']} target_usd={info['target_usd']!r}")
        print(f"[annotation] bindings={info['bindings']}")
        for v in info["views"]:
            print(f"[annotation] view {v}")
        for m in info["markers"]:
            print(f"[annotation] marker {m}")
        for h in info["hints"] or ["(어긋나는 점 없음)"]:
            print(f"[annotation] hint: {h}")
        self._status.text = (info["hints"][0] if info["hints"] else
                             f"뷰포트 {len(info['views'])}개, 마커 {len(info['markers'])}개. 콘솔 확인")

    def _save(self):
        """저장. 타겟이 아직 없으면 형상 USD 칸의 경로를 타겟으로 정하면서 저장한다 (찍어 둔 마커의 첫 저장)"""
        field = self._usd.model.get_value_as_string().strip()
        AnnotationService.save(field if field and not AnnotationService.target_usd() else None)

    def _apply(self):
        """칸의 글을 고른 마커에"""
        AnnotationService.set_text(AnnotationService.selected(),
                                   self._label.model.get_value_as_string(),
                                   self._desc.model.get_value_as_string())

    def _toggle_all(self):
        """전체 보이기 / 숨기기"""
        AnnotationService.set_all_visible(not AnnotationService.all_visible())

    def _toggle_one(self, marker_id):
        """그 마커 보이기 / 숨기기"""
        AnnotationService.set_visible(marker_id, not AnnotationService.is_visible(marker_id))

    def _apply_font(self):
        """칸의 글자 크기를 고른 마커에. 안 되면(범위 밖 등) 칸을 지금 값으로 되돌린다"""
        sel = AnnotationService.selected()
        if not AnnotationService.set_font_size(sel, self._font.model.get_value_as_float()):
            self._fill_font(AnnotationService.get(sel) if sel else None)

    def _apply_arrow(self):
        """칸의 배수를 모든 화살표에. 안 되면 칸을 지금 값으로 되돌린다"""
        if not AnnotationService.set_arrow_scale(self._arrow.model.get_value_as_float()):
            self._arrow.model.set_value(AnnotationService.arrow_scale())

    def _apply_plane_at(self):
        """칸의 위치를 고른 평면에. 평면이 아니거나 안 되면 칸을 되돌린다"""
        sel = AnnotationService.selected()
        if not AnnotationService.set_plane_at(sel, self._plane_at.model.get_value_as_float()):
            self._fill_plane(AnnotationService.get(sel) if sel else None)

    def _apply_plane_size(self):
        """칸의 크기를 고른 평면에. 평면이 아니거나 안 되면 칸을 되돌린다"""
        sel = AnnotationService.selected()
        if not AnnotationService.set_plane_size(sel, self._plane_w.model.get_value_as_float(),
                                                self._plane_h.model.get_value_as_float()):
            self._fill_plane(AnnotationService.get(sel) if sel else None)

    def _reset(self):
        """고른 마커의 패널을 처음 자리로"""
        AnnotationService.reset_panel(AnnotationService.selected())

    def _remove(self):
        """고른 마커를 지운다"""
        AnnotationService.remove(AnnotationService.selected())

    # -- 다시 채우기 --------------------------------------------------------------

    def _on_change(self, reason: str, marker_id=None):
        """끄는 중의 잦은 알림은 넘긴다. 패널을 클릭하면 그 패널의 글을 칸에 채운다"""
        if reason in QUIET_REASONS:
            return
        if reason == "visible":
            self._paint_rows()
            return
        if reason == "arrow":
            self._arrow.model.set_value(AnnotationService.arrow_scale())
            self._status.text = AnnotationService.status()
            return
        if reason == "plane":
            sel = AnnotationService.selected()
            self._fill_plane(AnnotationService.get(sel) if sel else None)
            self._status.text = AnnotationService.status()
            return
        if reason == "font":
            sel = AnnotationService.selected()
            self._fill_font(AnnotationService.get(sel) if sel else None)
            return
        self._refresh(fill=reason in ("text", "click"), rebuild=reason in LIST_REASONS)

    def _refresh(self, fill: bool = False, rebuild: bool = True):
        """상태, 목록, 고른 마커. 고른 것이 바뀌었을 때만 글 칸을 덮어쓴다.
        목록은 마커가 생기거나 없어지거나 글이 바뀔 때만 다시 짓고 (다음 프레임에), 선택만 바뀌면 줄 색만 바꾼다"""
        if self._window is None:
            return
        self._status.text = AnnotationService.status()
        self._file.text = f"마커 파일: {AnnotationService.marker_file() or '(형상 USD 없음)'}"
        if rebuild:
            self._rebuild_list_later()
        else:
            self._paint_rows()
        sel = AnnotationService.selected()
        one = AnnotationService.get(sel) if sel else None
        self._picked.text = f"선택: {sel} ({one['kind']})" if one else "선택: 없음"
        if fill or sel != self._shown_id:
            self._shown_id = sel
            self._label.model.set_value(one["label"] if one else "")
            self._desc.model.set_value(one["description"] if one else "")
            self._fill_font(one)
            self._fill_plane(one)

    def _fill_plane(self, one):
        """평면 위치, 크기 칸. 평면이 아니면 위치 0, 크기는 새 평면 기본값"""
        if self._plane_at is None:
            return
        if one is not None and one.get("kind") == "plane":
            at, (w, h) = one["at"], one["size"]
        else:
            at, (w, h) = 0.0, (100.0, 100.0)
        self._plane_at.model.set_value(float(at))
        self._plane_w.model.set_value(float(w))
        self._plane_h.model.set_value(float(h))

    def _fill_font(self, one):
        """글자 크기 칸. 고른 것이 없으면 0"""
        if self._font is not None:
            self._font.model.set_value(float(one.get("font_px") or 0.0) if one else 0.0)

    def _rebuild_list_later(self):
        """목록을 다음 프레임에 다시 짓는다. 목록 버튼의 클릭 콜백 안에서 그 버튼을 지우면
        omni.ui 가 새 줄을 제대로 못 그려 고른 줄만 남았다"""
        if self._rebuild_task is not None and not self._rebuild_task.done():
            return

        async def later():
            try:
                import omni.kit.app
                await omni.kit.app.get_app().next_update_async()
            except Exception:
                await asyncio.sleep(0)
            self._rebuild_task = None
            self._fill_list()

        try:
            self._rebuild_task = asyncio.ensure_future(later())
        except RuntimeError:
            self._fill_list()

    def _fill_list(self):
        """마커마다 한 줄. 누르면 고른다"""
        if self._list is None:
            return
        self._list.clear()
        self._rows = {}
        with self._list:
            markers = AnnotationService.markers()
            if not markers:
                ui.Label("(없음)", height=ROW_HIGH, style={"color": STATUS_COLOR})
            for m in markers:
                text = f"[{m['kind']}] {m['label'] or '(이름 없음)'}   ({m['id']})"
                with ui.HStack(height=ROW_HIGH, spacing=2):
                    row = ui.Button(text, alignment=ui.Alignment.LEFT_CENTER,
                                    clicked_fn=functools.partial(AnnotationService.select, m["id"]))
                    eye = ui.Button("", width=EYE_WIDE,
                                    clicked_fn=functools.partial(self._toggle_one, m["id"]))
                self._rows[m["id"]] = (row, eye)
        self._paint_rows()

    def _paint_rows(self):
        """고른 줄 색, 보이기 단추 글을 바꾼다 (줄은 그대로 둔다)"""
        sel = AnnotationService.selected()
        every = AnnotationService.all_visible()
        for mid, (row, eye) in self._rows.items():
            on = AnnotationService.is_visible(mid)
            style = {"background_color": PICKED_ROW_BG if mid == sel else ROW_BG}
            if not (on and every):
                style["color"] = HIDDEN_TEXT_COLOR
            row.style = {"Button": style, "Button.Label": {"color": style.get("color", 0xFFFFFFFF)}}
            eye.text = "보임" if on else "숨김"
        if self._all_eye is not None:
            self._all_eye.text = "전체 숨기기" if every else "전체 보이기"

    # -- 창 ---------------------------------------------------------------------

    def show(self, on: bool) -> None:
        """창을 띄우거나 가린다"""
        if self._window is not None:
            self._window.visible = bool(on)

    def destroy(self):
        """알림을 끊고 창을 닫는다"""
        AnnotationService.remove_listener(self._on_change)
        if self._rebuild_task is not None and not self._rebuild_task.done():
            self._rebuild_task.cancel()
        self._rows = {}
        if self._window:
            self._window.destroy()
            self._window = None
