"""MORPH Lab 실제 앱 스모크 테스트. kit.exe --exec 로 실행한다 (python tools/kit.py smoke).

CONTRACT 공개 API와 웹 전송 채널(morph.stream.in/out)만 사용한다.
결과는 `[smoke] PASS|FAIL ...` 줄로 출력하고, 앱 창 스크린샷을 남긴 뒤 종료한다.
"""

import asyncio
import json
import os

import carb
import omni.kit.app
import omni.usd
from carb.eventdispatcher import get_eventdispatcher
from pxr import UsdGeom, UsdLux

SHOT = os.environ.get("MORPH_SMOKE_SHOT", "")
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"[smoke] {'PASS' if ok else 'FAIL'} {what}", flush=True)


async def frames(n: int) -> None:
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


def _grid_ok(rects: list[dict], cells: list[tuple[int, int, int, int]], cols: int, rows: int, tol: float = 0.02) -> bool:
    """rect들이 cols x rows 격자의 (열, 행, 열 폭, 행 높이) 칸과 맞는지. 격자 원점·크기는 rect 합집합 기준
    (편집 패널이 있는 morph.lab.kit은 분할 영역이 창 일부라서 합집합을 격자로 본다)."""
    x0 = min(r["x"] for r in rects); y0 = min(r["y"] for r in rects)
    w = max(r["x"] + r["w"] for r in rects) - x0; h = max(r["y"] + r["h"] for r in rects) - y0
    for r, (c, row, cw, rh) in zip(rects, cells):
        exp = (x0 + c * w / cols, y0 + row * h / rows, cw * w / cols, rh * h / rows)
        if any(abs(a - b) > tol for a, b in zip((r["x"], r["y"], r["w"], r["h"]), exp)):
            return False
    return w > 0.3 and h > 0.3


async def main() -> None:
    if os.environ.get("MORPH_SMOKE_EARLY"):  # 렌더러 준비 전 전환 (split 0.1.2 렌더 게이트 확인용)
        from morph.viewport.split import get_split_view as _gsv

        _gsv().set_layout("2x2")
        print("[smoke] early set_layout(2x2) before renderer ready", flush=True)
    await frames(30)
    from omni.kit.viewport.utility import get_active_viewport

    vp = get_active_viewport()
    if vp is not None:  # 렌더러가 실제 프레임을 낼 때까지 대기 (renderer.asyncInit)
        await vp.wait_for_rendered_frames(30)
    print("[smoke] renderer ready", flush=True)
    from morph.camera.control import get_camera_controller
    from morph.viewport.split import get_split_view

    ctx = omni.usd.get_context()
    await ctx.new_stage_async()
    await frames(10)
    stage = ctx.get_stage()
    UsdGeom.Cube.Define(stage, "/World/Cube").GetSizeAttr().Set(100.0)
    UsdLux.DomeLight.Define(stage, "/World/Dome").CreateIntensityAttr(1000.0)
    for i, pos in enumerate([(500, 300, 500), (-500, 300, 500)]):
        cam = UsdGeom.Camera.Define(stage, f"/World/Cam{i}")
        UsdGeom.XformCommonAPI(cam).SetTranslate(pos)
    await frames(10)

    split = get_split_view()
    cam = get_camera_controller()
    check(split is not None and cam is not None, "singletons available")
    check(not split.is_headless, "split view has real viewport windows")

    split.set_layout("2x2")
    await frames(120)
    views = split.list_views()
    check(split.get_layout() == "2x2" and len(views) == 4, f"layout 2x2 ({len(views)} views)")
    check(all(v.viewport_name for v in views), f"all views have windows {[v.viewport_name for v in views]}")

    split.set_view_camera(1, "/World/Cam0")
    split.set_view_camera(2, "/World/Cam1")
    split.focus_view(1)
    await frames(10)
    check(split.get_focused_view().view_id == 1, "focus view 1")
    state = cam.get_state()
    check(state is not None and state.camera_path == "/World/Cam0", f"camera follows focus ({state and state.camera_path})")

    cam.look_at(eye=(300, 200, 300), target=(0, 0, 0), duration_s=0.0)
    await frames(5)
    state = cam.get_state()
    check(state is not None and abs(state.eye[0] - 300) < 1e-3, f"look_at applied (eye={state and state.eye})")

    # 웹 클라이언트 흉내: morph.stream.in 으로 요청, morph.stream.out 회신 관찰
    out: list[dict] = []
    sub = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:out",
        event_name="morph.stream.out",
        on_event=lambda e: out.append(json.loads(e["message"])),
    )
    def web(id_: str, type_: str, payload: dict) -> None:
        env = {"type": type_, "v": 1, "id": id_, "payload": payload}
        get_eventdispatcher().dispatch_event("morph.stream.in", payload={"message": json.dumps(env)})

    cam.save_preset("smoke")
    web("hello", "morph.stream.messaging.hello", {})
    web("smoke-1", "morph.camera.control.apply_preset", {"name": "no-such"})
    web("smoke-2", "morph.camera.control.apply_preset", {"name": "smoke"})
    web("smoke-3", "morph.viewport.split.get_state", {})
    web("smoke-4", "morph.viewport.split.focus_view", {"view_id": 99})
    web("smoke-5", "morph.camera.orbit", {"d_yaw": 1, "d_pitch": 0})  # 1.0 이전 이름: 미등록 type
    await frames(10)
    sub = None
    replies = {m.get("id"): m for m in out if m.get("id")}
    p = {k: (replies[k]["payload"] if k in replies else None) for k in ("hello", "smoke-1", "smoke-2", "smoke-3", "smoke-4", "smoke-5")}
    check(p["hello"] and p["hello"].get("ok") is True, f"web hello ({p['hello']})")
    check(p["smoke-1"] and p["smoke-1"].get("ok") is False and p["smoke-1"].get("error") == "not_found", f"camera unknown preset -> not_found ({p['smoke-1']})")
    check(p["smoke-2"] and p["smoke-2"].get("ok") is True, f"camera apply_preset ok ({p['smoke-2']})")
    check(p["smoke-3"] and p["smoke-3"].get("ok") is True and p["smoke-3"].get("layout") == "2x2", f"split get_state ({p['smoke-3']})")
    check(p["smoke-4"] and p["smoke-4"].get("ok") is False and p["smoke-4"].get("error") == "not_found", f"split bad view_id -> not_found ({p['smoke-4']})")
    check(p["smoke-5"] and p["smoke-5"].get("ok") is False and p["smoke-5"].get("error") == "unknown_type", f"old type name rejected ({p['smoke-5']})")
    pushes = sorted({m.get("type") for m in out if not m.get("id")})
    check("morph.viewport.split.state" in pushes or "morph.camera.control.state" in pushes, f"state pushes after hello ({pushes})")

    # split 1.2 rect: 2x2는 같은 크기 네 칸, 겹치지 않음 (메뉴바가 있는 앱은 y가 그만큼 내려간다)
    rects = [v.get("rect") for v in (p["smoke-3"] or {}).get("views", [])]
    check(len(rects) == 4 and all(rects) and _grid_ok(rects, [(0, 0, 1, 1), (1, 0, 1, 1), (0, 1, 1, 1), (1, 1, 1, 1)], cols=2, rows=2), f"2x2 rects {rects}")

    async def shots(prefix: str = "") -> None:  # 뷰마다 뷰포트 캡처: <SHOT 폴더>/<prefix>view<N>.png
        from omni.kit.viewport.utility import capture_viewport_to_file, get_viewport_from_window_name

        for v in split.list_views():
            path = os.path.join(SHOT, f"{prefix}view{v.view_id}.png")
            try:
                if os.path.isfile(path):
                    os.remove(path)
                await capture_viewport_to_file(get_viewport_from_window_name(v.viewport_name), path).wait_for_result()
                for _ in range(120):  # 파일 쓰기는 캡처 완료 뒤 비동기로 끝난다
                    if os.path.isfile(path):
                        break
                    await frames(1)
                check(os.path.isfile(path), f"screenshot {path}")
            except Exception as e:  # 스크린샷 실패는 결과에만 남긴다
                check(False, f"screenshot {prefix}view{v.view_id} ({e!r})")

    if SHOT:
        await shots()

    # 1+3: 왼쪽 큰 뷰 + 오른쪽 세로 3개 (split 1.3, 기본 sideRatio 0.25)
    out.clear()
    sub = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:out2",
        event_name="morph.stream.out",
        on_event=lambda e: out.append(json.loads(e["message"])),
    )
    web("smoke-6", "morph.viewport.split.set_layout", {"layout": "1+3"})
    await frames(150)
    web("smoke-7", "morph.viewport.split.get_state", {})
    await frames(10)
    sub = None
    replies = {m.get("id"): m["payload"] for m in out if m.get("id")}
    r6, r7 = replies.get("smoke-6"), replies.get("smoke-7") or {}
    check(r6 and r6.get("ok") is True, f"web set_layout 1+3 ({r6})")
    views13 = r7.get("views", [])
    check(r7.get("layout") == "1+3" and len(views13) == 4 and split.get_layout() == "1+3", f"layout 1+3 ({r7.get('layout')}, {len(views13)} views)")
    rects = [v.get("rect") for v in views13]
    check(len(rects) == 4 and all(rects) and _grid_ok(rects, [(0, 0, 3, 3), (3, 0, 1, 1), (3, 1, 1, 1), (3, 2, 1, 1)], cols=4, rows=3), f"1+3 rects {rects}")

    # 웹: 카메라 목록 + 뷰 카메라 지정 (split 1.4), 프리셋 저장·목록·삭제 (camera 1.1)
    out.clear()
    sub = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:out3",
        event_name="morph.stream.out",
        on_event=lambda e: out.append(json.loads(e["message"])),
    )
    web("smoke-8", "morph.viewport.split.list_cameras", {})
    web("smoke-9", "morph.viewport.split.set_view_camera", {"view_id": 3, "camera_path": "/World/Cam1"})
    web("smoke-10", "morph.camera.control.save_preset", {"name": "web-smoke"})
    await frames(10)
    web("smoke-11", "morph.camera.control.list_presets", {})
    web("smoke-12", "morph.camera.control.delete_preset", {"name": "web-smoke"})
    await frames(5)
    web("smoke-13", "morph.camera.control.delete_preset", {"name": "web-smoke"})
    web("smoke-14", "morph.camera.control.save_preset", {"name": ""})
    await frames(10)
    sub = None
    replies = {m.get("id"): m["payload"] for m in out if m.get("id")}
    r = replies.get("smoke-8") or {}
    cams = [c.get("path") for c in r.get("cameras", [])]
    builtin = {c.get("path"): c.get("builtin") for c in r.get("cameras", [])}
    check(r.get("ok") is True and {"/World/Cam0", "/World/Cam1", "/OmniverseKit_Persp"} <= set(cams)
          and builtin.get("/World/Cam0") is False and builtin.get("/OmniverseKit_Persp") is True
          and cams.index("/World/Cam1") < cams.index("/OmniverseKit_Persp"), f"web list_cameras ({cams})")
    r = replies.get("smoke-9") or {}
    check(r.get("ok") is True and split.list_views()[3].camera_path == "/World/Cam1", f"web set_view_camera from list ({r}, {split.list_views()[3].camera_path})")
    r10, r11 = replies.get("smoke-10") or {}, replies.get("smoke-11") or {}
    names = [x.get("name") for x in r11.get("presets", [])]
    check(r10.get("ok") is True and r11.get("ok") is True and "web-smoke" in names, f"web save_preset + list_presets ({r10}, {names})")
    r12, r13 = replies.get("smoke-12") or {}, replies.get("smoke-13") or {}
    check(r12.get("ok") is True and r13.get("ok") is False and r13.get("error") == "not_found", f"web delete_preset ({r12}, {r13})")
    r14 = replies.get("smoke-14") or {}
    check(r14.get("ok") is False and r14.get("error") == "invalid_argument", f"web save_preset empty name ({r14})")

    # 웹: 1+3 오른쪽 열 폭 실시간 변경 (split 1.5). 창을 다시 만들지 않고 rect만 바뀐다
    from omni.kit.viewport.utility import get_viewport_from_window_name

    vps_before = [get_viewport_from_window_name(v.viewport_name) for v in split.list_views()]
    cams_before = [v.camera_path for v in split.list_views()]
    out.clear()
    sub = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:out4",
        event_name="morph.stream.out",
        on_event=lambda e: out.append(json.loads(e["message"])),
    )
    web("smoke-15", "morph.viewport.split.set_side_ratio", {"ratio": 0.3})
    web("smoke-15b", "morph.viewport.split.set_side_ratio", {"ratio": 0.4})  # 같은 프레임: 마지막 값만 반영
    await frames(60)
    web("smoke-16", "morph.viewport.split.get_state", {})
    web("smoke-17", "morph.viewport.split.set_side_ratio", {"ratio": 0.9})
    web("smoke-18", "morph.viewport.split.set_side_ratio", {"ratio": "wide"})
    await frames(10)
    web("smoke-19", "morph.viewport.split.set_side_ratio", {"ratio": 0.25})
    await frames(60)
    sub = None
    replies = {m.get("id"): m["payload"] for m in out if m.get("id")}
    r15, r16 = replies.get("smoke-15b") or {}, replies.get("smoke-16") or {}
    rects = [v.get("rect") for v in r16.get("views", [])]
    frac = rects[1]["w"] / (rects[0]["w"] + rects[1]["w"]) if len(rects) == 4 and all(rects) else None
    check(r15.get("ok") is True and abs(r15.get("side_ratio", 0) - 0.4) < 1e-9 and abs(r16.get("side_ratio", 0) - 0.4) < 1e-9
          and frac is not None and abs(frac - 0.4) < 0.03, f"web set_side_ratio 0.4 live ({r15}, state={r16.get('side_ratio')}, right frac={frac})")
    vps_after = [get_viewport_from_window_name(v.viewport_name) for v in split.list_views()]
    check(len(vps_after) == 4 and all(a is b for a, b in zip(vps_after, vps_before))
          and [v.camera_path for v in split.list_views()] == cams_before, "set_side_ratio keeps viewports and cameras")
    r17, r18 = replies.get("smoke-17") or {}, replies.get("smoke-18") or {}
    check(r17.get("ok") is True and r17.get("side_ratio") == 0.5, f"web set_side_ratio 0.9 -> clamped ({r17})")
    check(r18.get("ok") is False and r18.get("error") in ("invalid_argument", "invalid_payload"), f"web set_side_ratio bad value ({r18})")
    check(abs(split.get_side_ratio() - 0.25) < 1e-9, f"side ratio restored ({split.get_side_ratio()})")

    # 장면 (morph.stage.loader 0.1): 웹으로 목록·샘플 열기(지연 회신) → morph.stage.opened, 뷰 카메라 재지정, 뷰별 캡처
    out.clear()
    opened: list[dict] = []
    sub = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:out4", event_name="morph.stream.out",
        on_event=lambda e: out.append(json.loads(e["message"])))
    sub_open = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:opened", event_name="morph.stage.opened",
        on_event=lambda e: opened.append({k: e[k] for k in ("scene_id", "path", "source")}))
    DEMO = "samples/morph_demo.usda"
    web("smoke-20", "morph.stage.loader.list_scenes", {"refresh": True})
    web("smoke-21", "morph.stage.loader.open_scene", {"id": "samples/no_such.usda"})
    await frames(5)
    web("smoke-22", "morph.stage.loader.open_scene", {"id": DEMO})
    web("smoke-23", "morph.stage.loader.open_scene", {"id": DEMO})  # 진행 중 → failed_precondition
    for _ in range(1800):  # 열기 완료 회신(지연)까지
        await frames(1)
        if any(m.get("id") == "smoke-22" for m in out):
            break
    await frames(30)
    replies = {m.get("id"): m["payload"] for m in out if m.get("id")}
    r20, r21, r22, r23 = (replies.get(k) or {} for k in ("smoke-20", "smoke-21", "smoke-22", "smoke-23"))
    ids = [x.get("id") for x in r20.get("scenes", [])]
    check(r20.get("ok") is True and DEMO in ids and all("path" not in x for x in r20.get("scenes", [])), f"web list_scenes ({ids[:6]})")
    check(r21.get("ok") is False and r21.get("error") == "not_found", f"web open_scene unknown -> not_found ({r21})")
    check(r22.get("ok") is True and (r22.get("scene") or {}).get("id") == DEMO, f"web open_scene demo ({r22})")
    check(r23.get("ok") is False and r23.get("error") == "failed_precondition", f"web open_scene while loading -> failed_precondition ({r23})")
    check(any(o["scene_id"] == DEMO and o["source"] == "loader" for o in opened), f"morph.stage.opened ({opened})")
    st = [m["payload"] for m in out if m.get("type") == "morph.stage.loader.state" and not m.get("id")]
    check(any(x.get("loading") for x in st) and st and st[-1].get("scene_id") == DEMO and not st[-1].get("loading"), f"stage.loader.state pushes ({st})")
    stage = omni.usd.get_context().get_stage()
    cams_demo = ["/World/Cameras/CamFront", "/World/Cameras/CamTop", "/World/Cameras/CamSide"]
    check(stage is not None and all(stage.GetPrimAtPath(c).IsA(UsdGeom.Camera) for c in cams_demo), "demo stage has 3 cameras")
    split.set_layout("2x2")
    await frames(120)
    for vid, c in enumerate(cams_demo, start=1):
        split.set_view_camera(vid, c)
    await frames(60)
    check([v.camera_path for v in split.list_views()][1:] == cams_demo, f"views on demo cameras {[v.camera_path for v in split.list_views()]}")
    if SHOT:
        await shots("scene_")
    sub = None
    sub_open = None

    # 주석 (morph.annotation.overlay 0.1): 배관 샘플을 열면 사이드카 JSON 자동 읽기, 웹 왕복, 뷰별 캡처
    from morph.annotation.overlay import get_overlay

    out.clear()
    changed: list[dict] = []
    sub = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:out5", event_name="morph.stream.out",
        on_event=lambda e: out.append(json.loads(e["message"])))
    sub_anno = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:anno", event_name="morph.annotation.changed",
        on_event=lambda e: changed.append({"reason": e["reason"], "count": e["count"]}))
    web("smoke-24", "morph.stage.loader.open_scene", {"id": "samples/pipe_manifold.usda"})
    for _ in range(1800):
        await frames(1)
        if any(m.get("id") == "smoke-24" for m in out):
            break
    await frames(30)
    ov = get_overlay()
    anns = ov.list() if ov else []
    kinds = {k: sum(a["kind"] == k for a in anns) for k in ("inlet", "outlet", "plane", "label")}
    check(ov is not None and (ov.current_file or "").endswith("pipe_manifold.annotations.json"), f"overlay sidecar ({ov and ov.current_file})")
    check(kinds["inlet"] == 1 and kinds["outlet"] == 2 and kinds["plane"] == 2 and kinds["label"] >= 2, f"annotations auto-loaded {kinds}")
    check(any(c["reason"] == "load" and c["count"] == len(anns) for c in changed), f"morph.annotation.changed load ({changed})")
    stage = omni.usd.get_context().get_stage()
    check(not any("nnotation" in str(p.GetPath()) for p in stage.Traverse()), "no annotation prims in stage")
    web("smoke-25", "morph.annotation.overlay.list", {})
    web("smoke-26", "morph.annotation.overlay.add", {"annotation": {"id": "smoke_lbl", "kind": "label", "label": "smoke", "position": [0, 0, 1]}})
    web("smoke-27", "morph.annotation.overlay.add", {"annotation": {"kind": "outlet", "position": [0, 0, 0], "direction": [0, 0, 0]}})
    web("smoke-28", "morph.annotation.overlay.remove", {"id": "smoke_lbl"})
    web("smoke-29", "morph.annotation.overlay.remove", {"id": "smoke_lbl"})
    await frames(10)
    replies = {m.get("id"): m["payload"] for m in out if m.get("id")}
    r25, r26, r27, r28, r29 = (replies.get(k) or {} for k in ("smoke-25", "smoke-26", "smoke-27", "smoke-28", "smoke-29"))
    check(r25.get("ok") is True and len(r25.get("annotations", [])) == len(anns) and r25.get("file") == "pipe_manifold.annotations.json", f"web anno list ({len(r25.get('annotations', []))}, {r25.get('file')})")
    check(r26.get("ok") is True and r26.get("id") == "smoke_lbl" and r27.get("error") == "invalid_argument", f"web anno add ({r26}, {r27})")
    check(r28.get("ok") is True and r29.get("error") == "not_found", f"web anno remove ({r28}, {r29})")
    pushes = [m["payload"] for m in out if m.get("type") == "morph.annotation.overlay.state" and not m.get("id")]
    check(bool(pushes) and len(pushes[-1].get("annotations", [])) == len(anns), f"anno state pushes ({len(pushes)})")
    # 편집 (0.3): 뷰 중앙 pick(실제 뷰포트 쿼리) → 그 점에 라벨 추가 → 웹 select → 선택 해제 규칙. prim은 그대로
    n_prims = len(list(stage.Traverse()))
    v0 = split.list_views()[0]
    hit = None
    try:
        hit = await asyncio.wait_for(ov.pick(v0.viewport_name, 0.5, 0.5), 10.0)
    except Exception as e:
        check(False, f"pick view 0 ({e!r})")
    check(hit is not None and len(hit["position"]) == 3, f"pick view 0 center -> {hit}")
    sel_events: list = []
    sub_sel = get_eventdispatcher().observe_event(
        observer_name="morph.smoke:sel", event_name="morph.annotation.selection_changed",
        on_event=lambda e: sel_events.append(e["id"]))
    out.clear()
    if hit:
        web("smoke-30", "morph.annotation.overlay.add", {"annotation": {"id": "smoke_pick", "kind": "label", "label": "찍은 점", "description": "한글 표시 확인: 입구·출구·단면", "position": hit["position"]}})
    web("smoke-31", "morph.annotation.overlay.select", {"id": "smoke_pick"})
    web("smoke-32", "morph.annotation.overlay.select", {"id": "no_such"})
    web("smoke-33", "morph.annotation.overlay.pick", {"viewport_name": v0.viewport_name, "x": 0.5, "y": 0.5})
    web("smoke-34", "morph.annotation.overlay.pick", {"viewport_name": "No Such Viewport", "x": 0.5, "y": 0.5})
    for _ in range(300):
        await frames(1)
        if any(m.get("id") == "smoke-33" for m in out):
            break
    await frames(5)
    replies = {m.get("id"): m["payload"] for m in out if m.get("id")}
    r30, r31, r32, r33, r34 = (replies.get(k) or {} for k in ("smoke-30", "smoke-31", "smoke-32", "smoke-33", "smoke-34"))
    check(r30.get("ok") is True and r30.get("id") == "smoke_pick", f"web add at picked point ({r30})")
    check(r31.get("ok") is True and r31.get("selected_id") == "smoke_pick" and ov.selected_id == "smoke_pick", f"web select ({r31})")
    check(r32.get("error") == "not_found", f"web select unknown ({r32})")
    check(r33.get("ok") is True and r33.get("hit") is True and len(r33.get("position", [])) == 3, f"web pick ({r33})")
    check(r34.get("error") == "not_found", f"web pick unknown viewport ({r34})")
    st_sel = [m["payload"].get("selected_id") for m in out if m.get("type") == "morph.annotation.overlay.state" and not m.get("id")]
    check("smoke_pick" in st_sel, f"state push selected_id ({st_sel})")
    if SHOT:  # 선택 강조가 보이는 창 캡처
        try:
            import importlib

            rcap = importlib.import_module("omni.kit.renderer_capture")
            path = os.path.join(SHOT, "anno_select_window.png")
            if os.path.isfile(path):
                os.remove(path)
            rcap.acquire_renderer_capture_interface().capture_next_frame_swapchain(path)
            for _ in range(180):
                await frames(1)
                if os.path.isfile(path):
                    break
        except Exception:
            pass
    if ov.get("smoke_pick"):
        ov.remove("smoke_pick")
    await frames(5)
    check(ov.selected_id is None and sel_events[-1:] == [None], f"remove clears selection ({sel_events})")
    check(len(list(stage.Traverse())) == n_prims, "pick/select/edit left prims unchanged")
    # 면 선택 (0.4): 출구 B 플랜지 끝면(x=2.1 원판)을 화면에 투영해 pick_face → 원판 중심 (2.1, 0, 1), 법선 +X
    from omni.kit.viewport.utility import get_viewport_from_window_name
    from pxr import Gf

    ob = ov.get("outlet_b")
    vp0 = get_viewport_from_window_name(v0.viewport_name)
    face = None
    if ob and vp0:
        ndc = vp0.world_to_ndc.Transform(Gf.Vec3d(*ob["position"]))
        fx, fy = (ndc[0] + 1) / 2, (1 - ndc[1]) / 2
        try:
            res = await asyncio.wait_for(ov.pick_face(v0.viewport_name, fx, fy), 10.0)
            face = res and res["face"]
        except Exception as e:
            check(False, f"pick_face outlet B ({e!r})")
    ok_face = bool(face) and face["kind"] == "planar" and all(abs(a - b) < 0.02 for a, b in zip(face["center"], ob["position"])) \
        and face["normal"][0] > 0.99 and abs(face["area"] - 3.14159 * 0.3 ** 2) < 0.05 * 3.14159 * 0.3 ** 2 + 0.02
    check(ok_face, f"pick_face flange disk center {face and {k: face[k] for k in ('kind', 'center', 'normal', 'area', 'size')}} (outlet_b {ob and ob['position']})")
    out.clear()
    if ob and vp0:
        web("smoke-35", "morph.annotation.overlay.pick_face", {"viewport_name": v0.viewport_name, "x": fx, "y": fy})
        for _ in range(300):
            await frames(1)
            if any(m.get("id") == "smoke-35" for m in out):
                break
        await frames(5)
    r35 = next((m["payload"] for m in out if m.get("id") == "smoke-35"), {})
    fpush = [m["payload"].get("face") for m in out if m.get("type") == "morph.annotation.overlay.state" and not m.get("id")]
    check(r35.get("ok") is True and r35.get("hit") is True and ov.selected_face is not None and fpush and fpush[-1] is not None,
          f"web pick_face selects face ({r35.get('face', {}).get('center')}, pushes {len(fpush)})")
    if SHOT:  # 선택 면 외곽선·중심 좌표가 보이는 창 캡처
        try:
            import importlib

            rcap = importlib.import_module("omni.kit.renderer_capture")
            path = os.path.join(SHOT, "anno_face_window.png")
            if os.path.isfile(path):
                os.remove(path)
            rcap.acquire_renderer_capture_interface().capture_next_frame_swapchain(path)
            for _ in range(180):
                await frames(1)
                if os.path.isfile(path):
                    break
        except Exception:
            pass
    # 메시 경로: MountPlate(삼각형 메시) 윗면 8개 삼각형이 한 면으로 묶여 중심 (0.293185, -1.048236, 0.065), 면적 1.4 (stage.loader 0.1.3 CONTRACT)
    MP_C, MP_A = (0.293185, -1.048236, 0.065), 1.4
    mface = None
    if vp0:
        ndc = vp0.world_to_ndc.Transform(Gf.Vec3d(*MP_C))
        try:
            res = await asyncio.wait_for(ov.pick_face(v0.viewport_name, (ndc[0] + 1) / 2, (1 - ndc[1]) / 2), 15.0)
            mface = res and res["face"]
        except Exception as e:
            check(False, f"pick_face mount plate ({e!r})")
    check(bool(mface) and mface["kind"] == "planar" and all(abs(a - b) < 5e-3 for a, b in zip(mface["center"], MP_C))
          and mface["normal"][2] > 0.99 and abs(mface["area"] - MP_A) < 0.01,
          f"pick_face mesh top face merged {mface and {k: mface[k] for k in ('kind', 'center', 'normal', 'area', 'size')}}")
    out.clear()
    web("smoke-36", "morph.annotation.overlay.clear_face", {})
    await frames(5)
    r36 = next((m["payload"] for m in out if m.get("id") == "smoke-36"), {})
    check(r36.get("ok") is True and ov.selected_face is None, f"web clear_face ({r36})")
    check(len(list(stage.Traverse())) == n_prims, "pick_face/select_face left prims unchanged")
    sub_sel = None
    split.set_layout("2x2")
    await frames(120)
    cams_pipe = ["/World/Cameras/CamIso", "/World/Cameras/CamFront", "/World/Cameras/CamTop"]
    for vid, c in enumerate(cams_pipe, start=1):
        try:
            split.set_view_camera(vid, c)
        except Exception as e:
            check(False, f"set_view_camera {c} ({e!r})")
    await frames(60)
    if SHOT:
        await shots("anno_")  # 뷰포트 렌더 이미지만 (omni.ui.scene 주석 오버레이는 안 담김)
        # 앱 창 전체(스왑체인) 캡처: UI 레이어인 주석 오버레이까지 담긴다
        path = os.path.join(SHOT, "anno_window.png")
        try:
            import importlib

            rcap = importlib.import_module("omni.kit.renderer_capture")

            if os.path.isfile(path):
                os.remove(path)
            rcap.acquire_renderer_capture_interface().capture_next_frame_swapchain(path)
            for _ in range(180):
                await frames(1)
                if os.path.isfile(path):
                    break
            check(os.path.isfile(path), f"screenshot {path}")
        except Exception as e:
            check(False, f"window screenshot ({e!r})")
    sub = None
    sub_anno = None

    failed = [w for ok, w in results if not ok]
    print(f"[smoke] DONE {len(results) - len(failed)}/{len(results)} passed", flush=True)
    omni.kit.app.get_app().post_quit(1 if failed else 0)


async def guarded() -> None:
    try:
        await main()
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"[smoke] FAIL exception {e!r}", flush=True)
        omni.kit.app.get_app().post_quit(2)


asyncio.ensure_future(guarded())
