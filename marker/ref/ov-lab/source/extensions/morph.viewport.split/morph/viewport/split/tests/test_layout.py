from unittest import mock

import omni.kit.test

from .. import adapters as adapters_mod
from .. import extension as ext_mod
from ..adapters import read_side_ratio
from ..layout import (
    DEFAULT_SIDE_RATIO,
    LAYOUTS,
    SESSION_CAMERAS,
    check_layout,
    default_camera,
    dock_plan,
    side_ratio_from_setting,
    view_count,
)


class TestLayout(omni.kit.test.AsyncTestCase):
    async def test_layouts_and_counts(self):
        self.assertEqual(LAYOUTS, ("1x1", "1x2", "2x1", "2x2", "1+3"))
        self.assertEqual([view_count(l) for l in LAYOUTS], [1, 2, 2, 4, 4])
        self.assertEqual(check_layout("1+3"), "1+3")
        self.assertEqual(check_layout("2x1"), "2x1")

    async def test_invalid_layout(self):
        for bad in ("3x3", "", "1X2", "1+2", "3+1", "1 + 3", None, 2, ["1x1"]):
            with self.assertRaises(ValueError):
                check_layout(bad)
            with self.assertRaises(ValueError):
                view_count(bad)
            with self.assertRaises(ValueError):
                dock_plan(bad)

    async def test_dock_plan_grid_keeps_half_ratio(self):
        # 1.2.0까지와 같은 배치·비율 (0.5)
        self.assertEqual(dock_plan("1x1"), ())
        self.assertEqual(dock_plan("1x2"), ((1, 0, "RIGHT", 0.5),))  # 좌우
        self.assertEqual(dock_plan("2x1"), ((1, 0, "BOTTOM", 0.5),))  # 상하
        # 2x2: 0 1 / 2 3
        self.assertEqual(
            dock_plan("2x2"), ((1, 0, "RIGHT", 0.5), (2, 0, "BOTTOM", 0.5), (3, 1, "BOTTOM", 0.5))
        )
        # sideRatio는 격자 레이아웃에 영향 없음
        self.assertEqual(dock_plan("2x2", 0.4), dock_plan("2x2"))

    async def test_dock_plan_1p3(self):
        plan = dock_plan("1+3")
        self.assertEqual([e[:3] for e in plan], [(1, 0, "RIGHT"), (2, 1, "BOTTOM"), (3, 2, "BOTTOM")])
        self.assertAlmostEqual(plan[0][3], DEFAULT_SIDE_RATIO)
        self.assertAlmostEqual(plan[1][3], 2.0 / 3.0)
        self.assertAlmostEqual(plan[2][3], 0.5)
        self.assertAlmostEqual(dock_plan("1+3", 0.4)[0][3], 0.4)
        self.assertAlmostEqual(dock_plan("1+3", 0.01)[0][3], 0.1)  # 0.1..0.5로 자름
        self.assertAlmostEqual(dock_plan("1+3", 0.9)[0][3], 0.5)

    async def test_dock_plan_1p3_geometry(self):
        # ratio = 새로 붙는 창의 몫. 1920x1080에 적용하면 큰 뷰 1440x1080, 작은 뷰 480x360 (모두 4:3)
        rects = {0: (0.0, 0.0, 1920.0, 1080.0)}
        for view_id, target, pos, ratio in dock_plan("1+3"):
            x, y, w, h = rects[target]
            if pos == "RIGHT":
                rects[target] = (x, y, w * (1 - ratio), h)
                rects[view_id] = (x + w * (1 - ratio), y, w * ratio, h)
            else:
                rects[target] = (x, y, w, h * (1 - ratio))
                rects[view_id] = (x, y + h * (1 - ratio), w, h * ratio)
        expected = {
            0: (0, 0, 1440, 1080),
            1: (1440, 0, 480, 360),
            2: (1440, 360, 480, 360),
            3: (1440, 720, 480, 360),
        }
        for view_id, rect in expected.items():
            for got, want in zip(rects[view_id], rect):
                self.assertAlmostEqual(got, want, places=6)

    async def test_default_camera_rule(self):
        self.assertEqual([default_camera(i) for i in range(4)], list(SESSION_CAMERAS))
        self.assertEqual(default_camera(0, ["/A"]), "/A")
        self.assertEqual(default_camera(1, ["/A"]), "/OmniverseKit_Top")  # 모자라면 세션 카메라
        self.assertEqual(default_camera(1, ["/A", ""]), "/OmniverseKit_Top")  # 빈 값도 세션 카메라
        self.assertEqual(default_camera(2, ["/A", "/B", "/C"]), "/C")


class TestSideRatioSetting(omni.kit.test.AsyncTestCase):
    async def test_from_setting(self):
        self.assertEqual(side_ratio_from_setting(None), (0.25, None))  # 설정 없음: 경고 없이 기본값
        self.assertEqual(side_ratio_from_setting(0.3), (0.3, None))
        self.assertEqual(side_ratio_from_setting(0.1), (0.1, None))
        self.assertEqual(side_ratio_from_setting(0.5), (0.5, None))
        ratio, warning = side_ratio_from_setting(0.05)
        self.assertEqual((ratio, bool(warning)), (0.1, True))
        ratio, warning = side_ratio_from_setting(1)
        self.assertEqual((ratio, bool(warning)), (0.5, True))
        for bad in ("0.3", True, float("nan"), float("inf"), float("-inf"), [0.3]):  # 숫자 아님·유한하지 않음
            ratio, warning = side_ratio_from_setting(bad)
            self.assertEqual(ratio, DEFAULT_SIDE_RATIO, bad)
            self.assertTrue(warning, bad)

    async def test_read_side_ratio_warns_once_per_value(self):
        warnings = []
        settings = {"/exts/morph.viewport.split/sideRatio": "wide"}
        with mock.patch.object(adapters_mod, "_side_ratio_warned", set()), mock.patch.object(
            adapters_mod.carb, "log_warn", warnings.append
        ):
            self.assertEqual(read_side_ratio(settings.get), DEFAULT_SIDE_RATIO)
            self.assertEqual(read_side_ratio(settings.get), DEFAULT_SIDE_RATIO)
            self.assertEqual(len(warnings), 1)
            settings["/exts/morph.viewport.split/sideRatio"] = 0.7
            self.assertEqual(read_side_ratio(settings.get), 0.5)
            self.assertEqual(len(warnings), 2)
            settings["/exts/morph.viewport.split/sideRatio"] = 0.2
            self.assertEqual(read_side_ratio(settings.get), 0.2)
            self.assertEqual(read_side_ratio({}.get), DEFAULT_SIDE_RATIO)
            self.assertEqual(len(warnings), 2)


class TestDefaultLayoutSetting(omni.kit.test.AsyncTestCase):
    async def test_default_layout_accepts_1p3(self):
        warnings = []
        with mock.patch.object(ext_mod.carb, "log_warn", warnings.append):
            layout, _, _ = ext_mod._read_settings({"/exts/morph.viewport.split/defaultLayout": "1+3"}.get)
            self.assertEqual(layout, "1+3")
            self.assertEqual(warnings, [])
            layout, _, _ = ext_mod._read_settings({"/exts/morph.viewport.split/defaultLayout": "3x1"}.get)
            self.assertEqual(layout, "1x1")
            self.assertEqual(len(warnings), 1)
            layout, focus_on_click, cameras = ext_mod._read_settings({}.get)
            self.assertEqual((layout, focus_on_click, cameras), ("1x1", True, []))
