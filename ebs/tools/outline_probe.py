"""기즈모 외곽선 점검. Kit Script Editor 에 붙여 넣고 실행한다.

1. 시뮬을 돌려 기즈모(/EbsGrip)가 떠 있는 상태로 만든다
2. 이 스크립트를 한 번 실행하면 probe() 가 돈다 -> 콘솔 출력을 그대로 보낸다
3. 그 다음 셀에서 root_only() / per_piece() / clear() 를 하나씩 불러 보고 눈으로 확인한다
"""
import omni.usd

GRIP = "/EbsGrip"
PIECES = {
    "a": ["shaft_a", "head_a"],
    "bead": ["bead"],
    "b": ["shaft_b", "head_b"],
}
BLUE = (0.12, 0.45, 1.0, 1.0)
NO_SHADE = (0.0, 0.0, 0.0, 0.0)

ctx = omni.usd.get_context()
_slots = {}


def probe():
    """API 가 실제로 있는지, 기즈모가 있는지, 외곽선 두께 설정이 어디 있는지"""
    try:
        import omni.kit.app
        app = omni.kit.app.get_app()
        print("[probe] kit", app.get_build_version())
        manager = app.get_extension_manager()
        for ext in ("omni.usd", "omni.kit.viewport.window", "omni.hydra.rtx"):
            print(f"[probe] {ext} enabled={manager.is_extension_enabled(ext)}")
    except Exception as e:
        print("[probe] version lookup failed:", e)

    names = sorted(n for n in dir(ctx) if "selection_group" in n or "outline" in n)
    print("[probe] UsdContext methods:", names)
    for need in ("register_selection_group", "set_selection_group",
                 "set_selection_group_outline_color",
                 "set_selection_group_shade_color"):
        print(f"[probe]   {need}: {'OK' if hasattr(ctx, need) else 'MISSING'}")

    stage = ctx.get_stage()
    for piece in [GRIP] + [f"{GRIP}/{n}" for group in PIECES.values() for n in group]:
        prim = stage.GetPrimAtPath(piece) if stage else None
        ok = bool(prim and prim.IsValid())
        kind = prim.GetTypeName() if ok else "-"
        print(f"[probe] prim {piece}: {'OK' if ok else 'MISSING'} {kind}")

    try:
        import carb.settings
        settings = carb.settings.get_settings()
        found = []

        def walk(prefix, node):
            """설정 트리를 내려가며 outline 이나 selection 이 든 키만 모은다"""
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(f"{prefix}/{key}", value)
            elif "outline" in prefix.lower() or "selection" in prefix.lower():
                found.append((prefix, node))

        walk("", settings.get_settings_dictionary(""))
        for key, value in sorted(found)[:40]:
            print(f"[probe] setting {key} = {value}")
        if not found:
            print("[probe] no outline/selection settings found")
    except Exception as e:
        print("[probe] settings walk failed:", e)


def _new_group(slot, colour):
    """자리마다 그룹을 한 번만 받는다. 외곽선 색을 입히고 속은 안 칠한다"""
    group = _slots.get(slot)
    if group is None:
        group = ctx.register_selection_group()
        _slots[slot] = group
        print(f"[probe] group {group} registered for {slot}")
    ctx.set_selection_group_outline_color(group, colour)
    ctx.set_selection_group_shade_color(group, NO_SHADE)
    return group


def root_only():
    """시험 A: 뿌리 하나에만 건다. 자손까지 둘러싸이는지 본다"""
    clear()
    group = _new_group("root", BLUE)
    ctx.set_selection_group(group, GRIP)
    print(f"[probe] A: {GRIP} -> group {group}. 화살표 둘과 구체가 다 둘러싸이나?")


def per_piece():
    """시험 B: 좌화살표, 구체, 우화살표에 그룹을 따로 건다"""
    clear()
    for label, names in PIECES.items():
        group = _new_group(label, BLUE)
        for name in names:
            ctx.set_selection_group(group, f"{GRIP}/{name}")
        print(f"[probe] B: {label} {names} -> group {group}")
    print("[probe] B: 셋이 따로 둘러싸이나? 겹쳐 보이는 각도에서도?")


def clear():
    """건 것을 전부 뗀다. 그룹 0 으로 되돌린다"""
    for path in [GRIP] + [f"{GRIP}/{n}" for group in PIECES.values() for n in group]:
        try:
            ctx.set_selection_group(0, path)
        except Exception as e:
            print(f"[probe] clear {path} failed: {e}")
    print("[probe] cleared")


probe()
