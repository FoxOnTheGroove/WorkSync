__all__ = ["TestAnnotationFonts"]

import os
import tempfile

import carb.settings
import omni.kit.app
import omni.kit.test

from .. import AnnotationOverlay
from .. import fonts as fonts_mod
from ..editor import EditorController, EditorModel, EditorWindow
from ..fonts import (
    CANDIDATES,
    KOREAN_REGION_FILE,
    S_APP_FONT_PATH,
    S_FONT_PATH,
    app_font_warning,
    editor_style,
    find_font,
    font_setting,
    is_font_file,
    warn_app_font_once,
)


class TestAnnotationFonts(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.font = os.path.join(self._tmp.name, "kfont.ttf")
        with open(self.font, "wb") as f:
            f.write(b"\0")
        self.settings = carb.settings.get_settings()
        self.old = self.settings.get(S_FONT_PATH)
        self.old_app = self.settings.get(S_APP_FONT_PATH)

    async def tearDown(self):
        self.settings.set_string(S_FONT_PATH, self.old if isinstance(self.old, str) else "")
        self.settings.set_string(S_APP_FONT_PATH, self.old_app if isinstance(self.old_app, str) else "")
        fonts_mod._warned = False
        self._tmp.cleanup()

    async def test_configured_path(self):
        self.assertEqual(find_font(self.font), self.font)  # exists: that path
        missing = os.path.join(self._tmp.name, "nope.ttf")
        self.assertEqual(find_font(missing, candidates=[self.font]), "")  # missing: Kit default, no fallback

    async def test_auto_detect(self):
        missing = os.path.join(self._tmp.name, "nope.ttf")
        self.assertEqual(find_font("", candidates=[missing, self.font]), self.font)  # first existing
        self.assertEqual(find_font(None, candidates=[self.font]), self.font)
        self.assertEqual(find_font("  ", candidates=[missing]), "")  # nothing found: ""
        self.assertEqual(find_font("", candidates=[]), "")
        seen = []
        self.assertEqual(find_font("", candidates=["a", "b"], exists=lambda p: seen.append(p) or p == "b"), "b")
        self.assertEqual(seen, ["a", "b"])

    async def test_windows_malgun_first(self):
        self.assertTrue(CANDIDATES[0].lower().endswith("malgun.ttf"))
        malgun = CANDIDATES[0]
        if os.path.isfile(malgun):
            self.assertEqual(find_font(""), malgun)  # this PC (Windows): Malgun Gothic

    async def test_setting(self):
        self.settings.set_string(S_FONT_PATH, self.font)
        self.assertEqual(font_setting(valid=lambda p: True), self.font)  # explicit + font file: editor style
        self.assertEqual(font_setting(), "")  # 1-byte file is not a font: app font
        self.settings.set_string(S_FONT_PATH, os.path.join(self._tmp.name, "nope.ttf"))
        self.assertEqual(font_setting(), "")  # explicit but missing: app font

    async def test_setting_empty_no_auto_detect(self):
        """Default "" = app font: an auto-detected font (Malgun Gothic) is never put on the style."""
        for value in ("", "   "):
            self.settings.set_string(S_FONT_PATH, value)
            self.assertEqual(font_setting(exists=lambda p: True), "", repr(value))
            self.assertEqual(editor_style(font_setting()), {})

    async def test_app_font_warning(self):
        msg = app_font_warning("", "")
        self.assertIsNotNone(msg)
        for part in ("한글이 ??로 보일 수 있음", "font.customFontPath", "customRegionFiles"):
            self.assertIn(part, msg)
        self.assertIn("D:/f/malgun.ttf", app_font_warning(None, None, "D:\\f\\malgun.ttf"))
        self.assertIsNone(app_font_warning("C:/Windows/Fonts/malgun.ttf", ""))  # app font set
        self.assertIsNone(app_font_warning("", self.font))  # fontPath set explicitly
        self.assertIsNotNone(app_font_warning("  ", 3))

    async def test_warn_once(self):
        logged = []
        self.settings.set_string(S_APP_FONT_PATH, "")
        self.settings.set_string(S_FONT_PATH, "")
        msg = warn_app_font_once(warn=logged.append, reset=True)
        self.assertIsNotNone(msg)
        self.assertIsNone(warn_app_font_once(warn=logged.append))  # only once
        self.assertEqual(logged, [msg])
        self.settings.set_string(S_APP_FONT_PATH, self.font)
        self.assertIsNone(warn_app_font_once(warn=logged.append, reset=True))  # app font set: silent
        self.settings.set_string(S_APP_FONT_PATH, "")
        self.settings.set_string(S_FONT_PATH, self.font)
        self.assertIsNone(warn_app_font_once(warn=logged.append, reset=True))  # fontPath set: silent
        self.assertEqual(len(logged), 1)

    async def test_editor_style(self):
        self.assertEqual(editor_style(""), {})
        style = editor_style(self.font)
        self.assertEqual(style["font"], self.font)
        for key in ("Label", "Button.Label", "Field", "StringField", "FloatField", "ComboBox"):
            self.assertEqual(style[key], {"font": self.font}, key)

    async def test_region_file_has_hangul(self):
        import morph.annotation.overlay as pkg

        root = os.path.abspath(os.path.join(os.path.dirname(pkg.__file__), "..", "..", ".."))
        path = os.path.join(root, KOREAN_REGION_FILE)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        chars = set(text) - {"\n"}
        for ch in "가힣ㄱㅎ한글주석입구출구냉각수…":
            self.assertIn(ch, chars, ch)
        self.assertLess(len(chars), 65536)  # glyph manager limit
        self.assertTrue(all(len(line) <= 64 for line in text.splitlines()))

    # Windows are only built WITHOUT a style font here: building omni.ui with a style "font" in the
    # headless test app (--no-window) / with an unloadable file crashed Kit (setBytesData nullptr).
    # Font styles are checked as dicts only.

    async def _frames(self, n=2):
        for _ in range(n):
            await omni.kit.app.get_app().next_update_async()

    async def test_is_font_file(self):
        self.assertFalse(is_font_file(self.font))  # 1 byte: not a font
        self.assertFalse(is_font_file(os.path.join(self._tmp.name, "nope.ttf")))
        self.assertFalse(is_font_file(""))
        self.assertFalse(is_font_file(None))
        self.assertFalse(is_font_file(self._tmp.name))  # directory
        bad = self._write("bad.ttf", b"PK\x03\x04" + b"\0" * 60)
        self.assertFalse(is_font_file(bad))
        for name, magic in (("a.ttf", b"\x00\x01\x00\x00"), ("b.otf", b"OTTO"), ("c.ttc", b"ttcf"), ("d.ttf", b"true")):
            self.assertTrue(is_font_file(self._write(name, magic + b"\0" * 60)), name)
        self.assertFalse(is_font_file(self._write("short.ttf", b"OTTO")))  # shorter than the offset table
        malgun = CANDIDATES[0]
        if os.path.isfile(malgun):
            self.assertTrue(is_font_file(malgun))

    def _write(self, name, data):
        path = os.path.join(self._tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    async def test_setting_invalid_file_no_style(self):
        """fontPath pointing at a file that is not a font: no style (that crashed omni.ui)."""
        self.settings.set_string(S_FONT_PATH, self.font)
        self.assertEqual(font_setting(), "")
        self.assertEqual(editor_style(font_setting()), {})

    async def test_window_app_font(self):
        """Default: no font style, the editor follows the app font."""
        ov = AnnotationOverlay(scene_file_provider=lambda: None)
        ov.add({"kind": "label", "label": "밸브", "description": "냉각수 입구", "position": [0, 0, 0]})
        model = EditorModel(ov, arm_click=lambda cb: None, disarm_click=lambda: None)
        win = EditorWindow(model, font_path="")
        try:
            self.assertEqual(win.font_path, "")
            win.window.frame.clear()
            with win.window.frame:
                win._build()
            await self._frames(3)
        finally:
            win.destroy()
            model.destroy()
            ov._shutdown()
        await self._frames()

    async def test_window_rejects_non_font(self):
        """EditorWindow given a non-font file falls back to the app font instead of styling it."""
        ov = AnnotationOverlay(scene_file_provider=lambda: None)
        model = EditorModel(ov, arm_click=lambda cb: None, disarm_click=lambda: None)
        win = EditorWindow(model, font_path=self.font)
        try:
            self.assertEqual(win.font_path, "")
            await self._frames()
        finally:
            win.destroy()
            model.destroy()
            ov._shutdown()
        await self._frames()

    async def test_controller_default_no_style(self):
        """Controller with the default fontPath "": window has no font style even if Malgun Gothic exists."""
        self.settings.set_string(S_FONT_PATH, "")
        ov = AnnotationOverlay(scene_file_provider=lambda: None)
        ctl = EditorController(ov, drawing=None, dock_targets=())
        try:
            ctl.show()
            self.assertEqual(ctl.window.font_path, "")
            await self._frames()  # let the window build before it is destroyed
        finally:
            ctl.destroy()
            ov._shutdown()
        await self._frames()

    async def test_explicit_font_style_dict(self):
        """Explicit fontPath to a font file: font_setting returns it and editor_style puts it on the text widgets.

        Checked as a dict only (no window build with a style font in the headless test app).
        """
        path = self._write("real.ttf", b"\x00\x01\x00\x00" + b"\0" * 60)
        self.settings.set_string(S_FONT_PATH, path)
        self.assertEqual(font_setting(), path)
        style = editor_style(font_setting())
        self.assertEqual(style["font"], path)
        for key in ("Label", "Button.Label", "Field", "StringField", "FloatField", "ComboBox", "Tooltip"):
            self.assertEqual(style[key], {"font": path}, key)
