"""창이 있는 실제 Kit 앱에서 viewport.split + camera.control 연동 스모크 테스트.

py -3.12 tools/kit.py launch morph.lab.kit --/app/enableStdoutOutput=1 --exec tools/smoke_split.py (절대 경로) 로 실행. 결과는 'SMOKE ' 접두어 줄로 출력하고 종료한다.
"""
import asyncio
import traceback

import carb
import omni.kit.app
import omni.ui as ui
import omni.usd
from carb.eventdispatcher import get_eventdispatcher

app = omni.kit.app.get_app()
results = []


def log(msg):
    print(f"SMOKE {msg}", flush=True)


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    log(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


async def frames(n):
    for _ in range(n):
        await app.next_update_async()


async def wait_for(pred, max_frames=600):
    for _ in range(max_frames):
        if pred():
            return True
        await app.next_update_async()
    return pred()


async def main():
    from omni.kit.viewport.utility import get_active_viewport, get_viewport_from_window_name
    from morph.viewport.split import get_split_view
    from morph.camera.control import get_camera_controller

    await omni.usd.get_context().new_stage_async()
    await frames(60)

    events = []
    disp = get_eventdispatcher()
    subs = [
        disp.observe_event(event_name=n, on_event=lambda e, n=n: events.append((n, dict(e.payload) if hasattr(e, "payload") else {})),
                           observer_name="smoke")
        for n in ("morph.viewport.layout_changed", "morph.viewport.focus_changed",
                  "morph.viewport.view_camera_changed", "morph.camera.active_changed")
    ]

    split = get_split_view()
    cam = get_camera_controller()
    check("singletons", split is not None and cam is not None)
    if split is None:
        return
    await wait_for(lambda: not split.is_headless, 300)
    check("window mode (not headless)", not split.is_headless)
    v0 = split.list_views()[0]
    log(f"view0 {v0}")

    def layout_events():
        return [p for n, p in events if n == "morph.viewport.layout_changed"]

    expected_counts = {"1x2": 2, "2x1": 2, "2x2": 4, "1+3": 4, "1x1": 1}
    prev_snap = None
    prev_vps = []
    for layout in ("1x2", "2x1", "2x2", "1+3", "2x2", "1x1", "1+3", "2x2"):
        before = len(layout_events())
        prev_layout = split.get_layout()
        split.set_layout(layout)
        # 첫 추가 뷰포트는 렌더러 준비로 수 초(실측 ~900프레임) 걸린다
        got = await wait_for(lambda: len(layout_events()) > before, 3000 if prev_snap is None else 600)
        views = split.list_views()
        names_ok = all(v.viewport_name and get_viewport_from_window_name(v.viewport_name) is not None for v in views)
        # 실제 도킹 여부: 추가 창이 떠 있으면 안 됨
        handles = [ui.Workspace.get_window(v.viewport_name) for v in views]
        docked_ok = all(h is not None and h.docked for h in handles[1:])
        check(f"layout {layout}", got and len(views) == expected_counts[layout] and names_ok and docked_ok,
              f"event={got} docked={[getattr(h, 'docked', None) for h in handles]} "
              f"views={[(v.view_id, v.viewport_name, v.camera_path) for v in views]}")
        # 창 이름·뷰포트 id는 view_id마다 고정이라 다시 만들어도 같다 -> 뷰포트 API 객체 자체로 비교
        snap = ([get_viewport_from_window_name(v.viewport_name) for v in views],
                [v.camera_path for v in views], split.get_focused_view().view_id)
        if {prev_layout, layout} == {"2x2", "1+3"}:
            # 같은 뷰 수 전환(2x2<->1+3)은 창을 다시 만들지 않고 재도킹한다 (카메라·포커스 유지)
            same_vp = len(snap[0]) == len(prev_snap[0]) and all(a is b for a, b in zip(snap[0], prev_snap[0]))
            check(f"{prev_layout}->{layout} redock keeps viewports/cameras/focus",
                  same_vp and snap[1:] == prev_snap[1:],
                  f"same_vp={same_vp} cameras={prev_snap[1]}->{snap[1]} focus={prev_snap[2]}->{snap[2]}")
        if prev_layout == "1x1" and layout == "1+3" and prev_snap is not None and len(prev_vps) == 4:
            check("control: 1x1->1+3 recreates viewports", all(a is not b for a, b in zip(snap[0][1:], prev_vps[1:])))
        if len(snap[0]) == 4:
            prev_vps = snap[0]
        prev_snap = snap
        await frames(10)

    # 같은 프레임 연속 전환: 마지막 요청만 layout_changed
    before = len(layout_events())
    split.set_layout("1x2")
    split.set_layout("2x1")
    await wait_for(lambda: len(layout_events()) > before, 600)
    await frames(60)
    new = layout_events()[before:]
    check("merge rapid switch", len(new) == 1 and new[0].get("layout") == "2x1", f"events={[p.get('layout') for p in new]}")

    split.set_layout("2x2")
    await wait_for(lambda: split.list_views()[-1].viewport_name != "" and len(split.list_views()) == 4, 600)
    await frames(30)

    # 포커스 = Kit 활성 뷰포트, camera.control 대상 추적
    views = split.list_views()
    target = views[2]
    split.focus_view(2)
    await frames(10)
    active = get_active_viewport()
    vp2 = get_viewport_from_window_name(target.viewport_name)
    check("focus_view -> Kit active viewport", active is not None and vp2 is not None and active.id == vp2.id,
          f"active={getattr(active, 'camera_path', None)} view2={target.camera_path}")
    st = cam.get_state()
    check("camera.control follows focus", st is not None and str(st.camera_path) == target.camera_path,
          f"cam_state={getattr(st, 'camera_path', None)}")

    # 포커스 뷰만 움직이는지
    vp0 = get_viewport_from_window_name(views[0].viewport_name)
    stage = omni.usd.get_context().get_stage()

    def xf(path):
        from pxr import UsdGeom, Usd
        return UsdGeom.Xformable(stage.GetPrimAtPath(path)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())

    m0_before, m2_before = xf(views[0].camera_path), xf(target.camera_path)
    cam.orbit(15.0, 5.0)
    await frames(10)
    check("orbit moves focused view camera only",
          xf(target.camera_path) != m2_before and xf(views[0].camera_path) == m0_before)

    # set_view_camera / 외부 카메라 변경 감지
    n_vc = len([1 for n, _ in events if n == "morph.viewport.view_camera_changed"])
    split.set_view_camera(1, "/OmniverseKit_Persp")
    await frames(5)
    check("set_view_camera event", len([1 for n, _ in events if n == "morph.viewport.view_camera_changed"]) > n_vc)
    n_vc = len([1 for n, _ in events if n == "morph.viewport.view_camera_changed"])
    cam.set_active_camera("/OmniverseKit_Top")
    await frames(10)
    check("set_active_camera detected by split",
          split.list_views()[2].camera_path == "/OmniverseKit_Top"
          and len([1 for n, _ in events if n == "morph.viewport.view_camera_changed"]) > n_vc)

    # [리뷰 회귀 A] 전환 중 외부 카메라 변경이 덮어써지지 않아야 함
    split.focus_view(0)
    await wait_for(lambda: cam.get_state() is not None
                   and str(cam.get_state().camera_path) == split.list_views()[0].camera_path, 120)
    split.set_layout("1x2")
    cam.set_active_camera("/OmniverseKit_Right")
    await wait_for(lambda: len(split.list_views()) == 2 and split.list_views()[1].viewport_name != "", 600)
    await frames(30)
    vp0_cam = str(get_viewport_from_window_name(split.list_views()[0].viewport_name).camera_path)
    check("A: camera change during transition kept", vp0_cam == "/OmniverseKit_Right"
          and split.list_views()[0].camera_path == "/OmniverseKit_Right",
          f"kit={vp0_cam} split={split.list_views()[0].camera_path}")

    # [리뷰 회귀 B] focus_view 직후 같은 프레임 set_active_camera → 새 포커스 뷰에 적용
    before1 = split.list_views()[1].camera_path
    split.focus_view(1)
    cam.set_active_camera("/OmniverseKit_Front")
    await frames(10)
    v = split.list_views()
    check("B: set_active_camera right after focus_view hits new focus",
          v[1].camera_path == "/OmniverseKit_Front" and v[0].camera_path == "/OmniverseKit_Right",
          f"view0={v[0].camera_path} view1={v[1].camera_path} (view1 before={before1})")

    # [리뷰 회귀 C] 포커스 뷰가 사라지는 축소 중 camera.control이 빈 대상으로 떨어지지 않아야 함
    split.set_layout("2x2")
    await wait_for(lambda: len(split.list_views()) == 4 and split.list_views()[3].viewport_name != "", 600)
    await frames(30)
    split.focus_view(3)
    await frames(10)
    n_ev = len(events)
    split.set_layout("1x2")
    await wait_for(lambda: len(split.list_views()) == 2 and split.get_focused_view().view_id == 0, 600)
    await frames(30)
    empty_active = [p for n, p in events[n_ev:] if n == "morph.camera.active_changed" and not p.get("camera_path")]
    st = cam.get_state()
    check("C: shrink with focused view removed", not empty_active and st is not None
          and str(st.camera_path) == split.list_views()[0].camera_path,
          f"empty_active={len(empty_active)} cam={getattr(st, 'camera_path', None)}")

    # 1x1로 줄이면 포커스는 view 0
    split.set_layout("1x1")
    await wait_for(lambda: len(split.list_views()) == 1 and split.get_focused_view().view_id == 0, 600)
    await frames(30)
    check("shrink -> focus view 0", split.get_focused_view().view_id == 0)
    check("camera.control back on view 0", cam.get_state() is not None
          and str(cam.get_state().camera_path) == split.list_views()[0].camera_path)

    subs.clear()
    log(f"events {[(n, p.get('layout', p.get('view_id', p.get('camera_path')))) for n, p in events]}")


morph_errors = []


def _on_log(source, level, filename, line_number, message, *args):
    if level >= carb.logging.LEVEL_ERROR and str(source).startswith("morph."):
        morph_errors.append(f"{source}: {message}")


async def run():
    log_sub = carb.logging.acquire_logging().add_logger(_on_log)
    try:
        await main()
    except Exception:
        log("EXCEPTION " + traceback.format_exc().replace("\n", " | "))
        results.append(("exception", False))
    carb.logging.acquire_logging().remove_logger(log_sub)
    check("no morph.* error logs", not morph_errors, f"count={len(morph_errors)} first={morph_errors[:1]}")
    passed = sum(ok for _, ok in results)
    log(f"SUMMARY {passed}/{len(results)} passed")
    app.post_quit(0 if passed == len(results) else 1)


asyncio.ensure_future(run())
