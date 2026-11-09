"""Desktop editor: "MORPH Annotations" omni.ui window + Window menu entry.

- ``EditorModel`` holds the form state and talks to the overlay API (``add`` / ``update`` /
  ``remove`` / ``select`` / ``load`` / ``save``). No omni.ui here, so it is unit-testable.
- ``EditorWindow`` is the omni.ui view of one model (rebuilt when the model notifies).
- ``EditorController`` owns the window lazily (``editorWindow`` setting, menu toggle).
- "Pick in view": the model arms one left click on every viewport (drawing ``arm_click``),
  picks there (``AnnotationOverlay._pick_api``) and fills the form. Nothing is applied until
  [Apply]. Clicks only give a position; nothing is bound to the hit prim.
- "Select face": the same one-click capture, but ``AnnotationOverlay._pick_face_api`` then
  ``select_face`` (highlighted on every viewport). [Face centre to form] fills the form from the
  selected face (label/inlet/outlet position = centre, inlet direction = -normal, outlet = +normal,
  plane origin/normal/size x 1.2).
- Font: by default no ``"font"`` style, the window follows the app font (Hangul comes from the app
  settings ``/app/font/customFontPath`` + ``customRegionFiles``). A styled font would be baked with
  Latin glyph ranges only ("??"). Only an explicit ``fontPath`` setting is applied as a style.
- Docking: when first opened, the window is docked as a tab into the right panel ("Property",
  else "Stage") a couple of frames later (``DOCK_TARGETS``). No such window: it stays floating.
"""

__all__ = [
    "EditorModel",
    "EditorWindow",
    "EditorController",
    "WINDOW_TITLE",
    "MENU_NAME",
    "MENU_PATH",
    "DOCK_TARGETS",
]

import asyncio
import functools
import os
from typing import Callable, Dict, List, Optional

import carb
import carb.settings

from .fonts import editor_style, font_setting, is_font_file
from .model import DEFAULT_COLORS, KINDS
from .overlay import (
    EXT_ID,
    S_SHOW_DESC,
    S_TEXT_SCALE,
    TEXT_SCALE_MAX,
    TEXT_SCALE_MIN,
    AnnotationOverlay,
    show_description_setting,
    text_scale_setting,
)

WINDOW_TITLE = "MORPH Annotations"
MENU_NAME = "MORPH Annotations"
MENU_PATH = "Window"
DOCK_TARGETS = ("Property", "Stage")  # right panel of the desktop layout, first one found wins
DOCK_DELAY_FRAMES = 2  # docking right after creation is often ignored: wait for the first draws

FACE_SIZE_MARGIN = 1.2  # plane size = face size x this
FACE_TEXT_COLOR = 0xFFFFF21A  # bright cyan (0xAABBGGRR), like the viewport highlight
ERROR_COLOR = 0xFF4444FF  # omni.ui colours are 0xAABBGGRR
STATUS_COLOR = 0xFFAAAAAA

# Numeric fields per kind: (name, component count). "size" of an arrow is a single number.
KIND_FIELDS: Dict[str, List[tuple]] = {
    "label": [("position", 3)],
    "inlet": [("position", 3), ("direction", 3), ("size", 1)],
    "outlet": [("position", 3), ("direction", 3), ("size", 1)],
    "plane": [("origin", 3), ("normal", 3), ("size", 2), ("up", 3)],
}
_DEFAULTS = {
    "position": [0.0, 0.0, 0.0],
    "direction": [1.0, 0.0, 0.0],
    "origin": [0.0, 0.0, 0.0],
    "normal": [1.0, 0.0, 0.0],
    "up": [0.0, 0.0, 1.0],
}
_ARROW_SIZE = 1.0
_PLANE_SIZE = [2.0, 2.0]


def _default_values(kind: str) -> Dict[str, list]:
    out = {}
    for name, n in KIND_FIELDS[kind]:
        if name == "size":
            out[name] = [_ARROW_SIZE] if n == 1 else list(_PLANE_SIZE)
        else:
            out[name] = list(_DEFAULTS[name])
    return out


def _floats(v, n: int) -> list:
    if n == 1:
        return [float(v)]
    return [float(x) for x in v]


class EditorModel:
    """Form state of the editor. All methods are main-thread only.

    ``arm_click(callback)`` / ``disarm_click()`` come from the drawing (one click on any viewport,
    ``callback(viewport_api, x, y)``). ``pick_at(viewport_api, x, y) -> Future`` and
    ``pick_face_at(viewport_api, x, y) -> Future`` default to the overlay's internal pickers. All are
    injectable for tests.
    """

    def __init__(
        self,
        overlay: AnnotationOverlay,
        *,
        arm_click: Optional[Callable] = None,
        disarm_click: Optional[Callable] = None,
        pick_at: Optional[Callable] = None,
        pick_face_at: Optional[Callable] = None,
    ):
        self.overlay = overlay
        self._arm = arm_click
        self._disarm = disarm_click
        self._pick_at = pick_at or overlay._pick_api
        self._pick_face_at = pick_face_at or overlay._pick_face_api
        self.pick_mode = "position"  # what the pending click is for: "position" | "face"
        self._listeners: List[Callable[[], None]] = []
        self.error = ""
        self.status = ""
        self.picking = False  # waiting for the view click or for the query
        self._pick_future = None
        self._reset("label")
        overlay._add_listener(self._on_overlay_changed)
        if overlay.selected_id is not None:
            self._load(overlay.selected_id)

    # ------------------------------------------------------------------ observers (view)
    def subscribe(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def unsubscribe(self, cb: Callable[[], None]) -> None:
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    def _changed(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                carb.log_error(f"[{EXT_ID}] editor view refresh failed: {e}")

    def destroy(self) -> None:
        self.cancel_pick()
        self.overlay._remove_listener(self._on_overlay_changed)
        self._listeners = []

    # ------------------------------------------------------------------ form state
    def _reset(self, kind: str) -> None:
        self.editing_id: Optional[str] = None
        self.kind = kind
        self.new_id = ""
        self.label = ""
        self.description = ""
        self.color = list(DEFAULT_COLORS[kind])
        self.values = _default_values(kind)
        self.use_up = False
        self.dirty = False

    def _load(self, ann_id: str) -> bool:
        ann = self.overlay.get(ann_id)
        if ann is None:
            return False
        kind = ann["kind"]
        self._reset(kind)
        self.editing_id = ann_id
        self.label = ann.get("label", "")
        self.description = ann.get("description", "")
        self.color = list(ann.get("color", DEFAULT_COLORS[kind]))
        for name, n in KIND_FIELDS[kind]:
            if name in ann:
                self.values[name] = _floats(ann[name], n)
        self.use_up = kind == "plane" and "up" in ann
        return True

    @property
    def is_new(self) -> bool:
        return self.editing_id is None

    @property
    def fields(self) -> List[tuple]:
        """(name, count) numeric fields of the current kind."""
        return list(KIND_FIELDS[self.kind])

    def set_kind(self, kind: str) -> None:
        """Kind of a *new* annotation (kind of an existing one cannot change). Shared fields stay."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        if not self.is_new or kind == self.kind:
            return
        keep = {k: v for k, v in self.values.items() if k in ("position", "origin")}
        point = keep.get("position") or keep.get("origin")
        recolor = self.color == DEFAULT_COLORS[self.kind]
        self.kind = kind
        self.values = _default_values(kind)
        if point is not None:
            self.values["origin" if kind == "plane" else "position"] = list(point)
        if recolor:
            self.color = list(DEFAULT_COLORS[kind])
        self.use_up = False
        self.dirty = True
        self._changed()

    def set_new_id(self, text: str) -> None:
        self.new_id = text
        self.dirty = True

    def set_label(self, text: str) -> None:
        self.label = text
        self.dirty = True

    def set_description(self, text: str) -> None:
        self.description = text
        self.dirty = True

    def set_color(self, rgb) -> None:
        self.color = [float(c) for c in list(rgb)[:3]]
        self.dirty = True

    def set_component(self, name: str, index: int, value: float) -> None:
        self.values[name][index] = float(value)
        self.dirty = True

    def set_use_up(self, use: bool) -> None:
        self.use_up = bool(use)
        self.dirty = True
        self._changed()

    def to_annotation(self) -> dict:
        """The form as an annotation dict (``id`` only for a new one with a typed id)."""
        ann = {"kind": self.kind, "label": self.label, "description": self.description, "color": list(self.color)}
        for name, n in KIND_FIELDS[self.kind]:
            if name == "up" and not self.use_up:
                continue
            v = self.values[name]
            ann[name] = v[0] if n == 1 else list(v)
        if self.is_new and self.new_id.strip():
            ann = {"id": self.new_id.strip(), **ann}
        return ann

    # ------------------------------------------------------------------ actions
    def _ok(self, status: str) -> None:
        self.error = ""
        self.status = status

    def _fail(self, error: str) -> None:
        self.error = error
        self.status = ""

    def new(self, kind: str) -> None:
        """Start a new annotation of ``kind`` (clears the selection)."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        self.cancel_pick(notify=False)
        self._reset(kind)
        self._ok(f"새 {kind} 주석: 값을 넣고 [적용]")
        self.overlay.select(None)
        self._changed()

    def select(self, ann_id: Optional[str]) -> None:
        try:
            self.overlay.select(ann_id)
        except KeyError:
            self._fail(f"주석 {ann_id!r}이(가) 없습니다")
            self._changed()

    def apply(self) -> bool:
        """[Apply]: ``update`` the selected annotation or ``add`` (and select) a new one."""
        ann = self.to_annotation()
        try:
            if self.is_new:
                new_id = self.overlay.add(ann)
                self.dirty = False
                self.overlay.select(new_id)  # the selection listener loads the stored values
                self._ok(f"추가함: {new_id}")
            else:
                fields = {k: v for k, v in ann.items() if k != "kind"}
                if self.kind == "plane" and not self.use_up:
                    fields["up"] = None  # back to the stage up axis
                self.overlay.update(self.editing_id, **fields)
                self.dirty = False
                self._load(self.editing_id)
                self._ok(f"적용함: {self.editing_id}")
        except KeyError:
            self._fail("편집 중인 주석이 삭제되었습니다")
        except ValueError as e:
            self._fail(str(e))
        self._changed()
        return not self.error

    def delete(self) -> bool:
        if self.is_new:
            self._fail("삭제할 주석을 목록에서 고르세요")
            self._changed()
            return False
        ann_id = self.editing_id
        try:
            self.overlay.remove(ann_id)  # drops the selection -> form becomes "new"
        except KeyError:
            self._fail(f"주석 {ann_id!r}이(가) 없습니다")
            self._changed()
            return False
        self._ok(f"삭제함: {ann_id}")
        self._changed()
        return True

    # top bar -----------------------------------------------------------------
    def set_visible(self, visible: bool) -> None:
        self.overlay.set_visible(bool(visible))

    @staticmethod
    def set_show_description(show: bool) -> None:
        carb.settings.get_settings().set_string(S_SHOW_DESC, "always" if show else "never")

    @staticmethod
    def set_text_scale(scale: float) -> None:
        scale = min(TEXT_SCALE_MAX, max(TEXT_SCALE_MIN, float(scale)))
        carb.settings.get_settings().set_float(S_TEXT_SCALE, scale)

    @property
    def show_description(self) -> bool:
        return show_description_setting()

    @property
    def text_scale(self) -> float:
        return text_scale_setting()

    @property
    def file_name(self) -> Optional[str]:
        path = self.overlay.current_file
        return os.path.basename(path) if path else None

    def reload(self) -> bool:
        try:
            count = self.overlay.load()
        except RuntimeError:
            self._fail("파일 장면이 아니라 다시 읽을 수 없습니다")
        except (ValueError, OSError) as e:
            self._fail(f"다시 읽기 실패: {e}")
        else:
            self._ok(f"다시 읽음: {count}개")
        self._changed()
        return not self.error

    def save(self) -> bool:
        try:
            path = self.overlay.save()
        except RuntimeError:
            self._fail("파일 장면이 아니라 저장할 수 없습니다")
        except OSError as e:
            self._fail(f"저장 실패: {e}")
        else:
            self._ok(f"저장함: {os.path.basename(path)}")
        self._changed()
        return not self.error

    # pick in view ------------------------------------------------------------
    def begin_pick(self) -> bool:
        """Arm the next left click in any viewport. Returns False if there is no viewport drawing."""
        return self._begin("position")

    def begin_face_pick(self) -> bool:
        """[Select face]: the next left click picks the face there and selects it."""
        return self._begin("face")

    def _begin(self, mode: str) -> bool:
        if self._arm is None:
            what = "면을 고를" if mode == "face" else "위치를 찍을"
            self._fail(f"뷰포트 그리기가 없어 {what} 수 없습니다")
            self._changed()
            return False
        self.cancel_pick(notify=False)
        self.picking = True
        self.pick_mode = mode
        self._arm(self._on_view_click)
        self._ok("뷰포트에서 면을 왼쪽 클릭하세요" if mode == "face" else "뷰포트에서 모델을 왼쪽 클릭하세요")
        self._changed()
        return True

    def cancel_pick(self, notify: bool = True) -> None:
        was = self.picking
        self.picking = False
        fut, self._pick_future = self._pick_future, None
        if fut is not None and not fut.done():
            fut.cancel()
        if was and self._disarm is not None:
            try:
                self._disarm()
            except Exception:  # noqa: BLE001
                pass
        if was and notify:
            self.status = ""
            self._changed()

    def _on_view_click(self, viewport_api, x: float, y: float) -> None:
        if not self.picking:
            return
        face = self.pick_mode == "face"
        what = "면 선택" if face else "위치 찍기"
        try:
            fut = (self._pick_face_at if face else self._pick_at)(viewport_api, x, y)
        except Exception as e:  # noqa: BLE001
            self.picking = False
            self._fail(f"{what} 실패: {e}")
            self._changed()
            return
        self._pick_future = fut
        self.status = "면 계산 중…" if face else "위치 확인 중…"
        self._changed()
        fut.add_done_callback(self._on_face_done if face else self._on_pick_done)

    def _take(self, fut) -> bool:
        """The pending pick future finished: True if its result should be used."""
        if fut is not self._pick_future:
            return False  # cancelled / superseded
        self._pick_future = None
        self.picking = False
        return not fut.cancelled()

    def _on_face_done(self, fut) -> None:
        if not self._take(fut):
            return
        exc = fut.exception()
        if exc is not None:
            self._fail(f"면 선택 실패: {exc}")
        elif fut.result() is None:
            self.overlay.select_face(None)
            self._fail("빈 곳을 클릭했습니다. 모델 위를 클릭하세요")
        else:
            face = fut.result()["face"]
            self.overlay.select_face(face)
            self._ok(f"면 선택: {self.face_text(face)}")
        self._changed()

    # selected face -------------------------------------------------------------
    @property
    def face(self) -> Optional[dict]:
        """The overlay's selected face (copy), or None."""
        return self.overlay.selected_face

    @staticmethod
    def face_text(face: Optional[dict]) -> str:
        """One-line summary: kind, centre, normal, area."""
        if face is None:
            return "(선택된 면 없음)"
        c, n = face["center"], face["normal"]
        return (f"{face['kind']}  중심 ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})  "
                f"법선 ({n[0]:.3f}, {n[1]:.3f}, {n[2]:.3f})  면적 {face['area']:.4g}")

    def clear_face(self) -> None:
        self.overlay.select_face(None)

    def apply_face(self, face: Optional[dict] = None) -> bool:
        """[Face centre to form]: fill the form from the selected face (nothing applied yet).

        label: position = centre. inlet: position = centre, direction = -normal. outlet: position =
        centre, direction = +normal. plane: origin = centre, normal = normal, size = face size x 1.2
        (a 0 component keeps the current value), up cleared (stage up axis).
        """
        face = self.face if face is None else face
        if face is None:
            self._fail("선택된 면이 없습니다. [면 선택]으로 먼저 고르세요")
            self._changed()
            return False
        center = [float(c) for c in face["center"]]
        normal = [float(c) for c in face["normal"]]
        if self.kind == "plane":
            self.values["origin"] = center
            self.values["normal"] = normal
            size = list(self.values["size"])
            for i in range(2):
                v = float(face["size"][i])
                if v > 0.0:
                    size[i] = v * FACE_SIZE_MARGIN
            self.values["size"] = size
            self.use_up = False
        else:
            self.values["position"] = center
            if self.kind in ("inlet", "outlet"):
                sign = -1.0 if self.kind == "inlet" else 1.0
                self.values["direction"] = [sign * c for c in normal]
        self.dirty = True
        self._ok("면 중심을 채웠습니다. [적용]을 눌러 반영하세요")
        self._changed()
        return True

    def _on_pick_done(self, fut) -> None:
        if not self._take(fut):
            return
        exc = fut.exception()
        if exc is not None:
            self._fail(f"위치 찍기 실패: {exc}")
        elif fut.result() is None:
            self._fail("빈 곳을 클릭했습니다. 모델 위를 클릭하세요")
        else:
            self.apply_pick(fut.result(), notify=False)
        self._changed()

    def apply_pick(self, result: dict, notify: bool = True) -> None:
        """Fill the form from a pick result (``{"position", "normal"}``). Nothing is applied yet.

        label: position. inlet: position, direction = -normal (flows into the model).
        outlet: position, direction = +normal. plane: origin only (normal unchanged).
        """
        pos = [float(c) for c in result["position"]]
        normal = result.get("normal")
        note = ""
        if self.kind == "plane":
            self.values["origin"] = pos
        else:
            self.values["position"] = pos
            if self.kind in ("inlet", "outlet"):
                if normal is not None:
                    sign = -1.0 if self.kind == "inlet" else 1.0
                    self.values["direction"] = [sign * float(c) for c in normal]
                else:
                    note = " (표면 방향을 못 구해 direction은 그대로)"
        self.dirty = True
        self._ok("위치를 채웠습니다. [적용]을 눌러 반영하세요" + note)
        if notify:
            self._changed()

    # overlay -> form ---------------------------------------------------------
    def _on_overlay_changed(self, reason: str) -> None:
        if reason == "face":  # face selection only: refresh the face info, keep the form
            self._changed()
            return
        sel = self.overlay.selected_id
        if sel != self.editing_id:
            if sel is not None:
                self._load(sel)
            else:
                kind = self.kind
                self._reset(kind)  # selection dropped (deleted / reloaded / cleared)
        elif sel is not None and reason != "select" and not self.dirty:
            self._load(sel)  # changed elsewhere (e.g. web update): show the stored values
        self._changed()

    def annotations(self) -> List[dict]:
        return self.overlay.list()


# ---------------------------------------------------------------------- omni.ui view
def _vec_label(name: str) -> str:
    return {"size": "size", "up": "up"}.get(name, name)


def _ignore_visibility(_visible: bool) -> None:
    pass


class EditorWindow:
    """omni.ui window for an ``EditorModel``. ``visible_changed(visible)`` is called on show / hide.

    ``font_path`` (``""`` = no style, app font) is set as the ``"font"`` style of every text widget.
    """

    def __init__(
        self,
        model: EditorModel,
        visible_changed: Optional[Callable[[bool], None]] = None,
        font_path: str = "",
    ):
        import omni.ui as ui

        self._model = model
        self._visible_changed = visible_changed
        self.font_path = font_path or ""
        if self.font_path and not is_font_file(self.font_path):
            # a style font omni.ui cannot load crashes Kit (setBytesData nullptr): use the app font
            carb.log_warn(f"[{EXT_ID}] editor font {self.font_path!r} is not a font file: using the app font")
            self.font_path = ""
        self.docked_to: Optional[str] = None
        self._dock_task = None
        self.visibility_fn_cleared = False
        self._window = ui.Window(WINDOW_TITLE, width=420, height=720, visible=True)
        style = editor_style(self.font_path)
        if style:
            self._window.frame.set_style(style)
        self._window.frame.set_build_fn(self._build)
        self._window.set_visibility_changed_fn(self._on_visibility)
        model.subscribe(self._refresh)
        settings = carb.settings.get_settings()
        self._setting_subs = [
            settings.subscribe_to_node_change_events(S_TEXT_SCALE, lambda *_: self._refresh()),
            settings.subscribe_to_node_change_events(S_SHOW_DESC, lambda *_: self._refresh()),
        ]
        self.build_count = 0

    @property
    def window(self):
        return self._window

    @property
    def visible(self) -> bool:
        return bool(self._window is not None and self._window.visible)

    @visible.setter
    def visible(self, value: bool) -> None:
        if self._window is not None:
            self._window.visible = bool(value)

    def _on_visibility(self, visible: bool) -> None:
        if not visible:
            self._model.cancel_pick()
        if self._visible_changed is not None:
            try:
                self._visible_changed(bool(visible))
            except Exception:  # noqa: BLE001
                pass

    def _refresh(self) -> None:
        if self._window is not None:
            self._window.frame.rebuild()

    # docking -------------------------------------------------------------------
    def dock_later(self, targets=DOCK_TARGETS, frames: int = DOCK_DELAY_FRAMES) -> None:
        """Dock into the first visible window of ``targets`` after ``frames`` updates."""
        if self._dock_task is not None and not self._dock_task.done():
            self._dock_task.cancel()
        self._dock_task = asyncio.ensure_future(self._dock_after(tuple(targets), frames))

    async def _dock_after(self, targets, frames: int) -> None:
        import omni.kit.app

        app = omni.kit.app.get_app()
        for _ in range(max(0, int(frames))):
            await app.next_update_async()
        self._dock_task = None
        self.dock_to_panel(targets)

    def dock_to_panel(self, targets=DOCK_TARGETS) -> Optional[str]:
        """Dock as a tab (``DockPosition.SAME``) into the first visible window of ``targets``.

        Returns that title, or None (no target, already docked, window gone: stays as it is).
        """
        import omni.ui as ui

        win = self._window
        if win is None:
            return None
        try:
            if win.docked:
                return None  # e.g. a restored workspace layout already placed it
        except Exception:  # noqa: BLE001
            pass
        for title in targets:
            try:
                target = ui.Workspace.get_window(title)
            except Exception:  # noqa: BLE001
                target = None
            if target is None or not getattr(target, "visible", True):
                continue
            try:
                win.dock_in(target, ui.DockPosition.SAME)
                win.focus()  # show our tab: the user just opened the window
            except Exception as e:  # noqa: BLE001
                carb.log_warn(f"[{EXT_ID}] docking the editor into {title!r} failed: {e}")
                return None
            self.docked_to = title
            return title
        return None

    def destroy(self) -> None:
        task, self._dock_task = self._dock_task, None
        if task is not None and not task.done():
            task.cancel()
        self._model.unsubscribe(self._refresh)
        settings = carb.settings.get_settings()
        for sub in self._setting_subs:
            try:
                settings.unsubscribe_to_change_events(sub)
            except Exception:  # noqa: BLE001
                pass
        self._setting_subs = []
        self._visible_changed = None
        win, self._window = self._window, None
        if win is not None:
            try:
                # drop our callback: a no-op rather than None (an empty std::function must not be called)
                win.set_visibility_changed_fn(_ignore_visibility)
                self.visibility_fn_cleared = True
            except Exception:  # noqa: BLE001
                pass
            win.frame.clear()
            win.destroy()

    # build ---------------------------------------------------------------------
    def _build(self) -> None:
        import omni.ui as ui

        m = self._model
        self.build_count += 1
        with ui.ScrollingFrame():
            with ui.VStack(spacing=4, height=0):
                self._build_top(ui, m)
                ui.Separator(height=6)
                self._build_list(ui, m)
                ui.Separator(height=6)
                self._build_new(ui, m)
                self._build_form(ui, m)
                self._build_buttons(ui, m)
                ui.Separator(height=6)
                self._build_face(ui, m)
                if m.error:
                    ui.Label(m.error, word_wrap=True, style={"color": ERROR_COLOR})
                if m.status:
                    ui.Label(m.status, word_wrap=True, style={"color": STATUS_COLOR})

    @staticmethod
    def _check(ui, value: bool, on_change: Callable[[bool], None], text: str) -> None:
        cb = ui.CheckBox(width=20)
        cb.model.set_value(bool(value))
        cb.model.add_value_changed_fn(lambda mm: on_change(mm.as_bool))
        ui.Label(text, width=0)

    def _build_top(self, ui, m: EditorModel) -> None:
        with ui.HStack(height=22, spacing=6):
            self._check(ui, m.overlay.visible, m.set_visible, "전체 표시")
            ui.Spacer(width=12)
            self._check(ui, m.show_description, m.set_show_description, "설명 표시")
            ui.Spacer()
        with ui.HStack(height=22, spacing=6):
            ui.Label("글자 크기", width=70)
            slider = ui.FloatSlider(min=TEXT_SCALE_MIN, max=TEXT_SCALE_MAX, step=0.05)
            slider.model.set_value(m.text_scale)
            slider.model.add_end_edit_fn(lambda mm: m.set_text_scale(mm.as_float))
        with ui.HStack(height=22, spacing=6):
            ui.Label(f"파일: {m.file_name or '(파일 장면 아님)'}", elided_text=True)
            ui.Button("다시 읽기", width=80, clicked_fn=m.reload)
            ui.Button("저장", width=60, clicked_fn=m.save)

    def _build_list(self, ui, m: EditorModel) -> None:
        anns = m.annotations()
        ui.Label(f"주석 목록 ({len(anns)})", height=20)
        with ui.ScrollingFrame(height=170):
            with ui.VStack(height=0, spacing=1):
                if not anns:
                    ui.Label("(주석 없음)", height=20, style={"color": STATUS_COLOR})
                sel = m.overlay.selected_id
                for ann in anns:
                    text = f"[{ann['kind']}] {ann.get('label') or '(이름 없음)'}   ({ann['id']})"
                    selected = ann["id"] == sel
                    ui.Button(
                        text,
                        height=22,
                        alignment=ui.Alignment.LEFT_CENTER,
                        checked=selected,
                        style={"Button": {"background_color": 0xFF7A5A2A if selected else 0xFF2A2A2A}},
                        clicked_fn=functools.partial(m.select, ann["id"]),
                    )

    def _build_new(self, ui, m: EditorModel) -> None:
        with ui.HStack(height=22, spacing=6):
            combo = ui.ComboBox(KINDS.index(m.kind), *KINDS, width=100)

            def on_new():
                m.new(KINDS[combo.model.get_item_value_model().as_int])

            ui.Button("새로 만들기", width=100, clicked_fn=on_new)
            if m.is_new:
                combo.model.add_item_changed_fn(
                    lambda mm, _item: m.set_kind(KINDS[mm.get_item_value_model().as_int])
                )
            ui.Spacer()

    def _build_form(self, ui, m: EditorModel) -> None:
        title = f"새 주석 ({m.kind})" if m.is_new else f"편집: {m.editing_id} ({m.kind})"
        ui.Label(title, height=22, style={"font_size": 16})
        if m.is_new:
            self._string_row(ui, "id", m.new_id, m.set_new_id, "(비우면 자동)")
        self._string_row(ui, "label", m.label, m.set_label)
        with ui.HStack(height=48, spacing=6):
            ui.Label("description", width=80)
            field = ui.StringField(multiline=True)
            field.model.set_value(m.description)
            field.model.add_value_changed_fn(lambda mm: m.set_description(mm.as_string))
        with ui.HStack(height=22, spacing=6):
            ui.Label("color", width=80)
            color = ui.ColorWidget(*m.color, width=60)
            cm = color.model

            def on_color(model, _item):
                kids = model.get_item_children()
                m.set_color([model.get_item_value_model(k).as_float for k in kids[:3]])

            cm.add_item_changed_fn(on_color)
            ui.Spacer()
        for name, n in m.fields:
            if name == "up":
                with ui.HStack(height=22, spacing=6):
                    self._check(ui, m.use_up, m.set_use_up, "up 지정 (끄면 스테이지 up 축)")
                if not m.use_up:
                    continue
            self._vec_row(ui, m, name, n)

    @staticmethod
    def _string_row(ui, name: str, value: str, on_change: Callable[[str], None], hint: str = "") -> None:
        with ui.HStack(height=22, spacing=6):
            ui.Label(name, width=80)
            field = ui.StringField()
            field.model.set_value(value)
            field.model.add_value_changed_fn(lambda mm: on_change(mm.as_string))
            if hint:
                ui.Label(hint, width=0, style={"color": STATUS_COLOR})

    @staticmethod
    def _vec_row(ui, m: EditorModel, name: str, n: int) -> None:
        with ui.HStack(height=22, spacing=4):
            ui.Label(_vec_label(name), width=80)
            for i in range(n):
                f = ui.FloatField()
                f.model.set_value(m.values[name][i])
                f.model.add_value_changed_fn(lambda mm, _n=name, _i=i: m.set_component(_n, _i, mm.as_float))
            for _ in range(3 - n):
                ui.Spacer()

    def _build_buttons(self, ui, m: EditorModel) -> None:
        with ui.HStack(height=26, spacing=6):
            ui.Button("적용", clicked_fn=m.apply)
            ui.Button("삭제", enabled=not m.is_new, clicked_fn=m.delete)
            if m.picking and m.pick_mode == "position":
                ui.Button("찍기 취소", clicked_fn=m.cancel_pick)
            else:
                ui.Button("뷰에서 위치 찍기", clicked_fn=m.begin_pick)
            if m.picking and m.pick_mode == "face":
                ui.Button("면 선택 취소", clicked_fn=m.cancel_pick)
            else:
                ui.Button("면 선택", clicked_fn=m.begin_face_pick)

    def _build_face(self, ui, m: EditorModel) -> None:
        face = m.face
        ui.Label("선택된 면", height=20)
        if face is None:
            ui.Label("(없음: [면 선택] 후 뷰포트를 클릭)", height=20, style={"color": STATUS_COLOR})
        else:
            c, n = face["center"], face["normal"]
            for text in (
                f"종류: {face['kind']}",
                f"중심: ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})",
                f"법선: ({n[0]:.3f}, {n[1]:.3f}, {n[2]:.3f})",
                f"면적: {face['area']:.4g}   크기: {face['size'][0]:.3f} x {face['size'][1]:.3f}",
            ):
                ui.Label(text, height=18, style={"color": FACE_TEXT_COLOR})
        with ui.HStack(height=26, spacing=6):
            ui.Button("면 중심을 폼에", enabled=face is not None, clicked_fn=m.apply_face)
            ui.Button("면 선택 해제", enabled=face is not None, clicked_fn=m.clear_face)


# ---------------------------------------------------------------------- controller (extension)
class EditorController:
    """Owns the editor window (created on first show) and the Window menu entry."""

    def __init__(self, overlay: AnnotationOverlay, drawing=None, font_path: Optional[str] = None,
                 dock_targets=DOCK_TARGETS):
        self._overlay = overlay
        self._drawing = drawing
        self._font_path = font_path  # None = explicit fontPath setting at first show ("" = app font)
        self._dock_targets = tuple(dock_targets or ())
        self._model: Optional[EditorModel] = None
        self._window: Optional[EditorWindow] = None
        self._menu = None

    @property
    def model(self) -> Optional[EditorModel]:
        return self._model

    @property
    def window(self) -> Optional[EditorWindow]:
        return self._window

    @property
    def visible(self) -> bool:
        return self._window is not None and self._window.visible

    def show(self) -> None:
        if self._window is None:
            d = self._drawing
            self._model = EditorModel(
                self._overlay,
                arm_click=d.arm_click if d is not None else None,
                disarm_click=d.disarm_click if d is not None else None,
            )
            if self._font_path is None:
                self._font_path = font_setting()
            self._window = EditorWindow(
                self._model, visible_changed=lambda _v: self._refresh_menu(), font_path=self._font_path
            )
            if self._dock_targets:
                self._window.dock_later(self._dock_targets)  # first open only
        self._window.visible = True
        self._refresh_menu()

    # pick in view (AnnotationOverlay.is_picking / cancel_pick) ---------------------
    @property
    def is_picking(self) -> bool:
        """True while [Pick in view] waits for the click or for its query."""
        return self._model is not None and self._model.picking

    def cancel_pick(self) -> None:
        """Cancel a pending [Pick in view] (Kit's click selection comes back). No-op otherwise."""
        if self._model is not None:
            self._model.cancel_pick()

    def hide(self) -> None:
        if self._window is not None:
            self._window.visible = False
        self._refresh_menu()

    def toggle(self, *_args) -> None:
        if self.visible:
            self.hide()
        else:
            self.show()

    # menu ------------------------------------------------------------------------
    def add_menu(self) -> bool:
        """Window > MORPH Annotations (needs omni.kit.menu.utils). Returns True if added."""
        try:
            from omni.kit.menu.utils import MenuItemDescription, add_menu_items
        except ImportError:
            return False
        try:
            self._menu = [
                MenuItemDescription(
                    name=MENU_NAME,
                    ticked=True,
                    ticked_fn=lambda: self.visible,
                    onclick_fn=self.toggle,
                )
            ]
            add_menu_items(self._menu, MENU_PATH)
            return True
        except Exception as e:  # noqa: BLE001
            carb.log_warn(f"[{EXT_ID}] adding the {MENU_PATH} menu item failed: {e}")
            self._menu = None
            return False

    def _refresh_menu(self) -> None:
        if self._menu is None:
            return
        try:
            from omni.kit.menu.utils import refresh_menu_items

            refresh_menu_items(MENU_PATH)
        except Exception:  # noqa: BLE001
            pass

    def destroy(self) -> None:
        menu, self._menu = self._menu, None
        if menu is not None:
            try:
                from omni.kit.menu.utils import remove_menu_items

                remove_menu_items(menu, MENU_PATH)
            except Exception:  # noqa: BLE001
                pass
        win, self._window = self._window, None
        if win is not None:
            win.destroy()
        model, self._model = self._model, None
        if model is not None:
            model.destroy()
