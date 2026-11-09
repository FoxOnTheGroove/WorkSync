"""카메라 수학 (순수 파이썬) 테스트."""
import math

import omni.kit.test

from morph.camera.control import camera_math as cm

from ._helpers import assert_vec

Y_UP = (0.0, 1.0, 0.0)
Z_UP = (0.0, 0.0, 1.0)
ORIGIN = (0.0, 0.0, 0.0)


class TestCameraMath(omni.kit.test.AsyncTestCase):
    def test_orbit_yaw_y_up(self):
        eye, target = cm.orbit((0.0, 0.0, 10.0), ORIGIN, Y_UP, 90.0, 0.0)
        assert_vec(self, eye, (10.0, 0.0, 0.0))
        assert_vec(self, target, ORIGIN)

    def test_orbit_yaw_z_up(self):
        eye, _ = cm.orbit((10.0, 0.0, 0.0), ORIGIN, Z_UP, 90.0, 0.0)
        assert_vec(self, eye, (0.0, 10.0, 0.0))

    def test_orbit_pitch_raises_eye_and_keeps_distance(self):
        eye, _ = cm.orbit((0.0, 0.0, 10.0), ORIGIN, Y_UP, 0.0, 30.0)
        assert_vec(self, eye, (0.0, 5.0, 10.0 * math.cos(math.radians(30.0))))
        self.assertAlmostEqual(cm.length(eye), 10.0, places=6)

    def test_orbit_pitch_clamped(self):
        eye, _ = cm.orbit((0.0, 0.0, 10.0), ORIGIN, Y_UP, 0.0, 200.0)
        elev = math.degrees(math.asin(eye[1] / cm.length(eye)))
        self.assertAlmostEqual(elev, cm.MAX_PITCH_DEG, places=5)
        eye, _ = cm.orbit((0.0, 0.0, 10.0), ORIGIN, Y_UP, 0.0, -200.0)
        elev = math.degrees(math.asin(eye[1] / cm.length(eye)))
        self.assertAlmostEqual(elev, -cm.MAX_PITCH_DEG, places=5)

    def test_orbit_from_straight_above_yaw_only_keeps_elevation(self):
        eye, _ = cm.orbit((0.0, 10.0, 0.0), ORIGIN, Y_UP, 45.0, 0.0)
        assert_vec(self, eye, (0.0, 10.0, 0.0))

    def test_orbit_around_offset_target(self):
        target = (5.0, 1.0, -3.0)
        eye, new_target = cm.orbit((5.0, 1.0, 7.0), target, Y_UP, 180.0, 0.0)
        assert_vec(self, eye, (5.0, 1.0, -13.0))
        assert_vec(self, new_target, target)

    def test_pan_moves_eye_and_target(self):
        eye, target = cm.pan((0.0, 0.0, 10.0), ORIGIN, Y_UP, 2.0, 3.0)
        assert_vec(self, eye, (2.0, 3.0, 10.0))
        assert_vec(self, target, (2.0, 3.0, 0.0))

    def test_zoom(self):
        eye, target = cm.zoom((0.0, 0.0, 10.0), ORIGIN, 0.5)
        assert_vec(self, eye, (0.0, 0.0, 5.0))
        assert_vec(self, target, ORIGIN)
        eye, _ = cm.zoom((0.0, 0.0, 10.0), ORIGIN, 1e-12)
        self.assertAlmostEqual(cm.length(eye), cm.MIN_DISTANCE, places=9)

    def test_camera_basis_orthonormal_and_degenerate(self):
        for eye, up in (((0.0, 0.0, 10.0), Y_UP), ((0.0, 10.0, 0.0), Y_UP), ((0.0, 0.0, 10.0), Z_UP)):
            r, u, f = cm.camera_basis(eye, ORIGIN, up)
            for v in (r, u, f):
                self.assertAlmostEqual(cm.length(v), 1.0, places=6)
            self.assertAlmostEqual(cm.dot(r, u), 0.0, places=6)
            self.assertAlmostEqual(cm.dot(r, f), 0.0, places=6)
            self.assertAlmostEqual(cm.dot(u, f), 0.0, places=6)
            assert_vec(self, cm.cross(r, u), cm.scale(f, -1.0))  # 카메라는 -Z를 본다

    def test_interpolate_endpoints(self):
        a_eye, a_t, b_eye, b_t = (0.0, 0.0, 10.0), ORIGIN, (10.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        e, t = cm.interpolate(a_eye, a_t, b_eye, b_t, 0.0)
        assert_vec(self, e, a_eye)
        assert_vec(self, t, a_t)
        e, t = cm.interpolate(a_eye, a_t, b_eye, b_t, 0.5)
        assert_vec(self, t, (0.5, 0.5, 0.5))  # target은 직선
        e, t = cm.interpolate(a_eye, a_t, b_eye, b_t, 2.0)
        assert_vec(self, e, b_eye)
        assert_vec(self, t, b_t)
        self.assertLess(cm.ease(0.25), 0.25)  # ease-in

    def test_interpolate_eye_moves_on_sphere_around_target(self):
        e, _ = cm.interpolate((0.0, 0.0, 10.0), ORIGIN, (10.0, 0.0, 0.0), ORIGIN, 0.5)
        h = 10.0 * math.sqrt(0.5)
        assert_vec(self, e, (h, 0.0, h))

    def test_interpolate_pure_translation_is_linear(self):
        e, t = cm.interpolate((0.0, 0.0, 10.0), ORIGIN, (10.0, 0.0, 10.0), (10.0, 0.0, 0.0), 0.5)
        assert_vec(self, e, (5.0, 0.0, 10.0))
        assert_vec(self, t, (5.0, 0.0, 0.0))

    def test_interpolate_through_target_keeps_distance(self):
        # eye가 target 반대편으로 가는 경우: 직선 보간이면 중간에 eye == target
        for s in (0.25, 0.5, 0.75):
            e, t = cm.interpolate((0.0, 0.0, 10.0), ORIGIN, (0.0, 0.0, -10.0), ORIGIN, s, Y_UP)
            self.assertAlmostEqual(cm.distance(e, t), 10.0, places=6)
        e, _ = cm.interpolate((0.0, 0.0, 10.0), ORIGIN, (0.0, 0.0, -10.0), ORIGIN, 0.5, Y_UP)
        assert_vec(self, e, (0.0, 10.0, 0.0))  # up 쪽으로 넘어감

    def test_interpolate_distance_lerp(self):
        e, _ = cm.interpolate((0.0, 0.0, 10.0), ORIGIN, (0.0, 0.0, 20.0), ORIGIN, 0.5)
        assert_vec(self, e, (0.0, 0.0, 15.0))

    def test_vec3_validation(self):
        self.assertEqual(cm.vec3([1, 2, 3]), (1.0, 2.0, 3.0))
        for bad in ([1, 2], [1, 2, float("nan")], [1, 2, float("inf")], 5, ["a", 1, 2]):
            with self.assertRaises(ValueError):
                cm.vec3(bad)
