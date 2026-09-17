import array
import io
import json
import math
import os
import re
import time
import xml.parsers.expat as expat
from contextlib import contextmanager, nullcontext, redirect_stdout

from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt, Gf
import omni.usd

from .ebs_simulate_camera import EbsSimulateCamera
from .ebs_simulate_shared import *
from .ebs_simulate_collide import EbsSimulateCollide as Collide

__all__ = ["EbsSimulate"]

def _remote(path: str) -> bool:
    """omniverse:// 같은 원격 경로인가"""
    head = path.split("://", 1)
    return len(head) == 2 and head[0].isalpha()


def _client():
    """omni.client 를 그때 가서 부른다"""
    import omni.client
    return omni.client


def _stamp_of(path: str) -> list:
    """캐시가 아직 맞는지 볼 표식: 판본, 크기, 수정 시각"""
    if _remote(path):
        client = _client()
        result, entry = client.stat(path)
        if result != client.Result.OK:
            raise OSError(f"{result} on {path}")
        when = getattr(entry, "modified_time", None)
        moment = int(when.timestamp() * 1e9) if when is not None else 0
        return [CACHE_VERSION, int(entry.size), moment]
    stat = os.stat(path)
    return [CACHE_VERSION, stat.st_size, stat.st_mtime_ns]


def _read_bytes(path: str) -> bytes:
    """로컬이든 원격이든 파일을 통째로 읽는다"""
    if _remote(path):
        client = _client()
        result, _, content = client.read_file(path)
        if result != client.Result.OK:
            raise OSError(f"{result} on {path}")
        return memoryview(content).tobytes()
    with open(path, "rb") as handle:
        return handle.read()


def _write_text(path: str, text: str) -> None:
    """글을 쓴다. 로컬은 옆자리에 썼다가 바꿔치기해 반쪽 파일을 안 남긴다"""
    if _remote(path):
        client = _client()
        result = client.write_file(path, text.encode("utf-8"))
        if result != client.Result.OK:
            raise OSError(f"{result} on {path}")
        return
    spare = path + ".part"
    try:
        with open(spare, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(spare, path)
    except Exception:
        try:
            os.remove(spare)
        except OSError:
            pass
        raise


def _plain(name: str) -> str:
    """XML 이름에서 네임스페이스를 떼어낸다"""
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _as_float(text) -> "float | None":
    """숫자로. 안 되면 None"""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


class _PortScan:
    """XML 한 번 훑어 모은 것. 주소별 CAD 값과 다음 칸을 같이 들고 있다"""

    def __init__(self):
        """XML 을 훑으며 모은 것을 담을 자리"""
        self.addr_cad = {}
        self.addr_next = {}
        self.found = {}
        self._groups = []
        self._addrs = []
        self._text = []

    def start(self, tag, attrib):
        """여는 태그. 속성을 모으고 주소를 물려준다"""
        entries = {}
        for raw, value in attrib.items():
            entries[_plain(raw)] = value
        key = entries.get("key")
        if key is not None and self._groups:
            self._groups[-1][key.strip().lower()] = entries.get("value", "")
        self._groups.append(entries)
        found = ADDR_PATTERN.match((entries.get("name") or _plain(tag)).strip())
        self._addrs.append(int(found.group(1)) if found
                           else (self._addrs[-1] if self._addrs else None))
        del self._text[:]

    def data(self, text):
        """태그 사이 글을 모은다"""
        self._text.append(text)

    def end(self, tag):
        """닫는 태그. 여기서 CAD 좌표, 다음 주소, 포트를 거둔다"""
        entries = self._groups.pop()
        addr = self._addrs.pop()
        key = entries.get("key")
        if key is not None and not entries.get("value") and self._groups:
            written = "".join(self._text).strip()
            if written:
                self._groups[-1][key.strip().lower()] = written
        del self._text[:]

        found = ADDR_PATTERN.match((entries.get("name") or _plain(tag)).strip())
        if found:
            cadx = _as_float(entries.get(CADX_KEY))
            cady = _as_float(entries.get(CADY_KEY))
            if cadx is not None or cady is not None:
                self.addr_cad[int(found.group(1))] = (cadx or 0.0, cady or 0.0)
        target = _as_float(entries.get(NEXT_KEY))
        puls = _as_float(entries.get(PULS_KEY))
        if addr is not None and target is not None and puls is not None:
            self.addr_next.setdefault(addr, []).append((int(target), puls))
        port_id = entries.get(PORT_ID_KEY)
        if port_id:
            port = PORT_PATTERN.match(port_id.strip())
            if port:
                self.found.setdefault(port.group(1).upper(), {})[
                    int(port.group(2))] = (_as_float(entries.get(OFFSET_KEY)), addr)

    def close(self):
        """거둔 포트 표"""
        return self.found


class EbsSimulate:
    """EBS 시뮬레이션의 속. 서비스가 이것 하나를 들고 있다"""

    def __init__(self):
        """설정, 색인, 캐시 자리를 전부 비워 둔다"""
        self._xml_path: str = ""
        self._usd_path: str = ""
        self._ebs_path_2port: str = ""
        self._ebs_path_3port: str = ""
        self._search_root: str = ""
        self._eqp_index: dict = {}
        self._port_map: dict = {}
        self._port_offsets: dict = {}
        self._port_addr: dict = {}
        self._port_addr_of: dict = {}
        self._addr_cad: dict = {}
        self._addr_next: dict = {}
        self._offset_scale: str = SCALE_SNAP
        self._rail_root: str = ""
        self._rail_index: dict = None
        self._rail_frame = None
        self._triangles: dict = {}
        self._eqp_boxes: dict = None
        self._bounds = None
        self._stage_index = None
        self._ebs_box = None
        self._lasers: bool = False
        self._outer: bool = True
        self._inner: bool = True
        self._clash_on: bool = True
        self._skin: str = ""
        self._skin_made: str = ""
        self._skin_worn: tuple = ()
        self._skin_wrote: list = []
        self._skin_opened: list = []
        self._phases: dict = {}
        self._skin_use: bool = False
        self._nudge: float = 0.0
        self._base = None
        self._verdict: dict = {}
        self._progress: float = 0.0
        self._doing: str = ""
        self._legs: int = 1
        self._leg: int = 0
        self._settled: float = 0.0
        self._spent: dict = {}
        self._shares: dict = {}
        self._results: dict = {}
        self._min_gap = {FACE_CEILING: MIN_GAP_CEILING,
                         FACE_LEFT: MIN_GAP_SIDE,
                         FACE_RIGHT: MIN_GAP_SIDE}
        self._blockers: dict = {}
        self._local: dict = {}
        self._faces: dict = {}
        self._leaves: dict = {}
        self._parts: dict = {}
        self._boxed: dict = {}
        self._visible: dict = {}
        self._grid_shape: dict = {}
        self._port_world: dict = {}
        self._port_rail_z: float = 0.0
        self._face_planes: dict = {}
        self._camera = EbsSimulateCamera()
        self._precision: str = PRECISION_TRI
        self._timings: list = []
        self._notes: list = []
        self._blocked: str = ""
        self._why: str = ""
        self._started: float = 0.0
        self._ready: bool = False
        self._target: dict = None
        self._aligned: bool = False
        self._result: dict = {}
        self._marker_draw = None


    def set_usd_path(self, path: str) -> None:
        """열 스테이지 경로. 바뀌면 Init 을 다시 받아야 한다

        open_stage  여는 곳. 같은 경로가 이미 열려 있으면 안 연다
        """
        path = (path or "").strip()
        if path != self._usd_path:
            self._ready = False
        self._usd_path = path

    def open_stage(self) -> bool:
        """그 USD 를 연다. 같은 것이 이미 열려 있으면 안 연다"""
        if not self._usd_path:
            return True
        context = omni.usd.get_context()
        now = ""
        try:
            now = str(context.get_stage_url() or "")
        except Exception:
            pass
        if now and now.rstrip("/") == self._usd_path.rstrip("/"):
            self._note(f"stage already open: {now}")
            return True
        with self._stage_timer("USD: open"):
            try:
                told = context.open_stage(self._usd_path)
            except Exception as e:
                self._note(f"could not open {self._usd_path}: {e}")
                return False
        ok = told[0] if isinstance(told, tuple) else told
        if ok is False:
            self._note(f"could not open {self._usd_path}: "
                       + (str(told[1]) if isinstance(told, tuple) and len(told) > 1
                          else "the stage did not open"))
            return False
        self._note(f"stage opened: {self._usd_path}")
        return True

    def set_xml_path(self, path: str) -> None:
        """포트 XML 경로. 바뀌면 포트 표와 Init 을 버린다

        load_ports  파싱과 캐시 (<xml> + CACHE_SUFFIX 옆자리). 키 이름은 _PortScan
        _remote     원격 IO 는 _stamp_of / _read_bytes / _write_text 셋뿐이다
        """
        path = (path or "").strip()
        if path != self._xml_path:
            self._port_map = {}
            self._ready = False
        self._xml_path = path

    def set_ebs_paths(self, path_2port: str, path_3port: str) -> None:
        """2포트/3포트 EBS 프림 경로

        _do_prepare  포트 수로 둘 중 하나를 고르는 규칙
        """
        self._ebs_path_2port = (path_2port or "").strip()
        self._ebs_path_3port = (path_3port or "").strip()

    def hide_ebs(self) -> int:
        """EBS 둘 다 화면에서 끈다

        _show_ebs  끄고 켜는 곳. align 이 쓴 것 하나만 다시 켠다
        """
        return self._show_ebs([self._ebs_path_2port, self._ebs_path_3port], False)

    def set_visible(self, path: str, on: bool) -> int:
        """그 프림 하나를 켜고 끈다. 세션 레이어에만 쓴다"""
        return self._show_ebs([path], bool(on))

    def show_ebs(self, prim) -> int:
        """그 EBS 하나를 켠다"""
        return self._show_ebs([prim], True)

    def _show_ebs(self, wanted: list, visible: bool) -> int:
        """세션 레이어에 가시성을 쓴다. 켠 것은 상자를 다시 잰다"""
        stage = self._get_stage()
        if stage is None:
            return 0
        shown = UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible
        done, touched = 0, []
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                for one in wanted:
                    if isinstance(one, str):
                        if not one:
                            continue
                        one = stage.GetPrimAtPath(one)
                    prim = one
                    if prim is None or not prim.IsValid():
                        continue
                    imageable = UsdGeom.Imageable(prim)
                    if imageable:
                        imageable.CreateVisibilityAttr().Set(shown)
                        touched.append(str(prim.GetPath()))
                        done += 1
        except Exception as e:
            self._note(f"could not set the EBS visibility ({e})")
        Collide._forget_ebs(self, touched)
        return done

    def nudge(self, step: float) -> float:
        """놓을 자리를 EBS 좌우로 step(m) 만큼 더 민다. 누적 거리를 돌려준다

        _do_align  목표점에 이만큼 더해서 놓는다. 다음 align 도 민 자리다
        set_nudge  0 으로 되돌리는 곳. SIM, Clear, Camera 가 부른다
        NUDGE_LIMIT  좌우로 이 거리까지만. 넘으면 거기서 멈춘다
        """
        return self.set_nudge(self._nudge + float(step))

    def watch_grip(self, grip) -> None:
        """뷰포트 기즈모가 마우스를 먼저 보도록 카메라에 걸어 둔다"""
        self._camera.watch(grip)

    def hold_camera(self, on: bool) -> None:
        """궤도 조작을 잠깐 놓는다. 뷰포트 손잡이를 끄는 동안"""
        self._camera.hold(on)

    def hold_clash(self, on: bool) -> bool:
        """내부충돌연출을 켜고 끈다. 손잡이를 잡는 동안 끈다

        내부충돌연출  내부 장비와 겹친 메쉬를 빨간 상자로 그리고 깜박이는 것
        끌 때  상자를 걷는다. 미는 동안은 내부 충돌을 다시 재지 않는다
        켤 때  지금 선 자리에서 내부 충돌만 다시 재서 판정과 그림을 고친다
        """
        self._clash_on = bool(on)
        if not on:
            self._marks().hide_clash()
            return True
        return self._retest_inner()

    def _retest_inner(self, draw: bool = True) -> bool:
        """지금 자리에서 내부 충돌만 다시 잰다. 3면은 산수로 이미 맞아 있다

        draw  False 면 판정만 고친다. 부른 쪽이 이어서 한 번에 그린다.
                  미는 동안 매 걸음 오는 길이라 로그도 남기지 않는다
        """
        if self._target is None or not self._verdict or not self._inner:
            return False
        with self._stage_timer("equipment: retest"):
            try:
                meeting = Collide.check_equipment(self, self._target["ebs"],
                                               self._target["equipment"])
            except Exception as e:
                self._note(f"interference check failed: "
                           f"{type(e).__name__}: {e}")
                return False
        self._verdict["inside"] = bool(meeting["hit"])
        self._verdict["boxes"] = list(meeting.get("boxes") or ())
        self._verdict["placeable"] = (not meeting["hit"]
                                      and not self._verdict.get("faces"))
        if not draw:
            return True
        self._note(f"inner at {self._nudge:+.3f}: "
                   + ("hit" if meeting["hit"] else "clear")
                   + f" ({meeting['tests']} pairs tested)")
        self.show_markers(self._target["ebs"], self._slid_cells(),
                          self._verdict.get("marks"),
                          self._verdict.get("boxes"), fresh=False)
        return True

    def slide(self, metres: float) -> dict:
        """민 자리로 EBS 를 옮기고 판정을 산수로 고쳐 다시 그린다

        _slide_marks  검출 지점은 그대로 두고 거리와 선 끝만 옮긴다. 좌우로만
                     미는 한 다시 잴 필요가 없다 (재는 축과 미는 축이 같다)
        collide  천장은 발자국을 벗어날 수 있어 stale 로 적는다. 그때는 다시
        """
        if self._target is None or not self._aligned:
            return self._payload(False, "Run Align first")
        was = self._nudge
        shift = self.set_nudge(metres) - was
        if not shift:
            return self._payload(True, f"offset {self._nudge:+.3f}")
        box = Collide._ebs_bound(self, self._target["ebs"])
        if not self._place_nudged():
            return self._payload(False, "EBS could not be moved")
        self._slid_box(box, shift)
        if self._verdict:
            self._slide_marks(shift, box)
            self.show_markers(self._target["ebs"], self._slid_cells(),
                              self._verdict.get("marks"),
                              self._verdict.get("boxes") if self._clash_on
                              else None, fresh=False)
        return self._payload(True, f"offset {self._nudge:+.3f}")

    def _slid_box(self, box, shift: float) -> None:
        """EBS 상자를 다시 재지 않고 민 만큼 옮겨 적는다. shift 는 미터다"""
        right = (self._verdict or {}).get("right")
        path = self._path_of(self._target["ebs"])
        if right is None or not path:
            return
        paces = shift / (self._per_unit() or 1.0)
        step = Gf.Vec3d(*[right[i] * paces for i in range(3)])
        moved = Gf.Matrix4d(1.0)
        moved.SetTranslateOnly(step)
        self._ebs_box = (path, Gf.BBox3d(box.GetRange(),
                                         box.GetMatrix() * moved))

    def _place_nudged(self) -> bool:
        """지금 민 거리로 EBS 를 다시 놓는다. 포트 계산은 다시 안 한다"""
        ebs, anchor = self._target["ebs"], self._target["anchor"]
        if self._base is None:
            return self._align_prims(ebs, anchor)
        target = self._pushed(anchor, self._base)
        return self._place_ebs(ebs, target, anchor)

    def _slid_cells(self) -> dict:
        """지금 판정에서 면마다 막혔나. 그린 판 색에만 쓴다"""
        marks = {mark["face"]: mark for mark in self._verdict.get("marks") or ()}
        return {face: [marks.get(face, {}).get("state") == STATE_CLASH]
                for face in FACES}

    def _slide_marks(self, shift: float, box) -> None:
        """민 만큼 선을 늘이고 줄인다. 검출 지점과 안내선은 그대로 둔다

        way  면의 바깥 방향. 미는 방향과 나란한 면만 간격이 변한다
        stale  천장처럼 미는 축과 직각인 면은 검출 지점이 발자국을 벗어날 수 있다
        """
        stage = self._get_stage()
        right = self._verdict.get("right")
        if stage is None or not right:
            return
        per_unit = self._per_unit() or 1.0
        paces = shift / per_unit
        step = [right[i] * paces for i in range(3)]
        for mark in self._verdict.get("marks") or ():
            way, reach = mark.get("way"), mark.get("distance")
            if way is None or reach is None:
                if mark.get("at") is not None:
                    mark["at"] = tuple(mark["at"][i] + step[i]
                                       for i in range(3))
                continue
            along = sum(right[i] * way[i] for i in range(3)) * shift
            gap = mark["distance"] - along
            near = [mark["from"][i] + step[i] for i in range(3)]
            far = [near[i] + way[i] * (gap / per_unit) for i in range(3)]
            mark["from"] = tuple(near)
            mark["to"] = tuple(far)
            mark["at"] = tuple((near[i] + far[i]) * 0.5 for i in range(3))
            mark["distance"] = gap
            mark["state"] = (STATE_CLASH if mark["distance"] < 0.0 else
                             STATE_TIGHT
                             if mark["distance"] < mark.get("min_gap", 0.0)
                             else STATE_CLEAR)
            mark["stale"] = not self._still_inside(mark, box)
        for name in ("centre", "inside_at"):
            spot = self._verdict.get(name)
            if spot:
                self._verdict[name] = tuple(spot[i] + step[i] for i in range(3))
        grip = self._verdict.get("grip")
        if grip and grip.get("matrix") is not None:
            moved = Gf.Matrix4d(1.0)
            moved.SetTranslateOnly(Gf.Vec3d(*step))
            grip["matrix"] = grip["matrix"] * moved
        marks = self._verdict["marks"]
        self._verdict["faces"] = [{"face": one["face"], "name": one["name"],
                                   "state": one["state"]} for one in marks
                                  if one["state"] != STATE_CLEAR]
        self._verdict["blocked"] = len(self._verdict["faces"])
        self._verdict["offset"] = self._nudge
        self._verdict["placeable"] = (not self._verdict.get("inside")
                                      and not self._verdict["faces"])

    def _still_inside(self, mark: dict, bbox) -> bool:
        """검출 지점이 아직 그 면의 발자국 안인가. 벗어났으면 다시 재야 한다"""
        plane = self._face_planes.get(mark.get("face"))
        spot = mark.get("spot")
        if plane is None or spot is None:
            return True
        local_box = bbox.GetRange()
        if local_box.IsEmpty():
            return True
        here = bbox.GetMatrix().GetInverse().Transform(Gf.Vec3d(*spot))
        lo, hi = local_box.GetMin(), local_box.GetMax()
        _, _, _, row_axis, col_axis = plane
        return all(lo[one] - LEAD_TOL <= here[one] <= hi[one] + LEAD_TOL
                   for one in (row_axis, col_axis))

    def _per_unit(self) -> float:
        """스테이지 한 단위가 몇 미터인가"""
        try:
            return UsdGeom.GetStageMetersPerUnit(self._get_stage()) or 1.0
        except Exception:
            return 1.0

    def set_nudge(self, metres: float) -> float:
        """민 거리를 그 값으로. 한계 안으로 잘라서 돌려준다"""
        self._nudge = max(-NUDGE_LIMIT, min(NUDGE_LIMIT, float(metres)))
        return self._nudge

    def get_nudge(self) -> float:
        """지금 민 거리(m). 오른쪽이 양수"""
        return self._nudge

    def _pushed(self, anchor, target):
        """민 거리만큼 EBS 좌우로 옮긴 목표점"""
        if not self._nudge or target is None:
            return target
        right, _, _ = self._camera.axes(self._get_stage(), anchor)
        paces = self._nudge / (self._per_unit() or 1.0)
        moved = Gf.Vec3d(*[target[i] + right[i] * paces for i in range(3)])
        self._note(f"nudged {self._nudge:+.3f} along the EBS right axis")
        return moved

    def set_near_span(self, span: float) -> float:
        """근평면을 EBS 폭 절반의 몇 배 앞에 둘지

        EbsSimulateCamera._near  실제로 자르는 곳. 거리에서 이만큼 뺀다
        """
        return self._camera.set_near_span(span)

    def set_checks(self, outer: bool, inner: bool) -> None:
        """collide 가 무엇을 잴지

        _collide_steps  끄면 그 단계를 건너뛴다. 진행률과 판정은 그대로 돈다
        outer  좌/우/천장 세 면과 스테이지. warm, sides, faces, clearance
        inner  EBS 와 대상 장비끼리. equipment
        """
        self._outer = bool(outer)
        self._inner = bool(inner)

    def set_skin_use(self, on: bool) -> bool:
        """머티리얼을 갈아입힐지. 끄면 걷고, 켜도 그 자리에서 안 입힌다

        거는 값이 싸지 않다. 세션 레이어에 쓸 때마다 스테이지가 다시 짜인다.
        그래서 입히는 자리를 align 하나로 못 박는다. 이 함수는 끄는 쪽만 한다
        """
        want = bool(on)
        if want == self._skin_use:
            return want
        self._skin_use = want
        if not want:
            self.strip_skin()
        return want

    def set_skin(self, url: str) -> str:
        """대상 장비에 입힐 것. 빈 칸이면 원래 색 그대로

        색으로 읽히면(#ff0000, 0.8,0.1,0.1) 쓰던 셰이더의 색만 덮어쓴다.
        / 로 시작하면 씬 안 머티리얼 프림, 그 밖은 받아 올 .mdl 로 본다
        값만 적어 두고 미리 받아만 둔다. 입히는 것은 align 몫이다
        """
        want = (url or "").strip()
        if want == self._skin:
            return self._skin
        self._skin = want
        self._skin_made = ""
        self.strip_skin()
        if want and self._skin_use:
            self.warm_skin()
        return self._skin

    def get_skin(self) -> str:
        """지금 적어 둔 머티리얼 경로"""
        return self._skin

    def strip_skin(self) -> bool:
        """건 것을 푼다. 푼 인스턴스는 열어 둔 채로 둔다

        도로 묶는 것이 다시 여는 것만큼 비싸다. rprim 을 버리고 인스턴서를
        다시 배칭하는 일이라 Clear 가 SIM 만큼 걸렸다. 열어 두면 Clear 는
        언바인드만 하면 되고, 같은 장비를 다시 재면 열 것도 없다
        인스턴싱 여부는 세션 레이어에만 있고 눈에는 안 보인다. 색은 여기서
        돌아오므로 "Clear 면 원래대로" 는 그대로 지켜진다
        도로 묶는 것은 close_skin 이 teardown 에서 한 번만 한다
        """
        wrote, self._skin_wrote = self._skin_wrote, []
        self._skin_worn = ()
        if not wrote:
            return False
        stage = self._get_stage()
        if stage is None:
            return False
        try:
            with self._phase("skin"), self._stage_timer("skin: unbind"):
                picked = [one for one in
                          (stage.GetPrimAtPath(path) for path in wrote)
                          if one is not None and one.IsValid()]
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    with Sdf.ChangeBlock():
                        for one in picked:
                            UsdShade.MaterialBindingAPI(
                                one).UnbindAllBindings()
        except Exception as e:
            self._loud(f"skin: could not take it off: "
                       f"{type(e).__name__}: {e}")
            return False
        return True

    def close_skin(self) -> bool:
        """열어 둔 인스턴스를 도로 묶는다. teardown 만 여기까지 간다"""
        opened, self._skin_opened = self._skin_opened, []
        if not opened:
            return False
        stage = self._get_stage()
        if stage is None:
            return False
        try:
            layer = stage.GetSessionLayer()
            with Usd.EditContext(stage, layer):
                with Sdf.ChangeBlock():
                    for path in opened:
                        spec = layer.GetPrimAtPath(path)
                        if spec is not None:
                            spec.ClearInfo("instanceable")
        except Exception as e:
            self._loud(f"skin: could not close the instances: "
                       f"{type(e).__name__}: {e}")
            return False
        return True

    async def settle(self, name: str = "overlay") -> float:
        """킷이 다시 매끄러워질 때까지 프레임을 돌리고 그 시간을 얹는다

        저작이 끝나도 Hydra 는 다음 프레임부터 메인 스레드에서 rprim 을
        다시 짓는다. 멈춘 것처럼 보이는 구간이 거기고, 우리 호출이 돌아온
        뒤라 어떤 계측에도 안 잡힌다. 마지막 느린 프레임까지를 잰다
        그린 것이 화면에 뜨는 것도 그 프레임들 안이라, 작업중 표를 여기까지
        세워 두면 표가 사라진 자리에 아무것도 없는 틈이 안 생긴다
        _doing  도는 동안 그 단계의 이름을 세워 둔다. 작업중 표가 이걸 읽는다
        """
        import omni.kit.app
        app = omni.kit.app.get_app()
        guess = self._settled or SETTLE_GUESS
        started = last = time.perf_counter()
        busy, calm = started, 0
        before, self._doing = self._doing, name
        try:
            for _ in range(SETTLE_MOST):
                await app.next_update_async()
                now = time.perf_counter()
                self._progress = min(99.0, (now - started) / guess * 100.0)
                if now - last > SETTLE_FRAME:
                    busy, calm = now, 0
                else:
                    calm += 1
                last = now
                if calm >= SETTLE_CALM:
                    break
        finally:
            self._doing = before
        spent = max(0.0, busy - started)
        self._progress = 100.0
        self.add_phase(name, spent)
        return spent

    async def settle_skin(self) -> float:
        """머티리얼이 정착할 때까지. 걸린 시간을 다음 진행도의 눈금으로 쓴다"""
        spent = await self.settle("skin")
        self._settled = spent or self._settled
        return spent

    def warm_skin(self) -> bool:
        """적어 둔 머티리얼을 미리 챙겨 둔다. init 이 부른다

        .mdl 이면 세션 레이어에 프림을 세워 그 자리에서 읽는다. SIM 때는
        바인딩만 걸면 되므로 기다릴 일이 없다. 씬 안 프림이면 읽을 것도 없다
        """
        if not self._skin or not self._skin_use:
            return False
        if self._skin_colour(self._skin) is not None:
            return False
        stage = self._get_stage()
        if stage is None:
            return False
        with self._phase("skin"), self._stage_timer("skin: load"):
            return self._make_skin(stage) is not None

    def _skin_material(self, stage):
        """걸 머티리얼 하나. 색이면 세우고, 씬 프림이면 그것, .mdl 이면 받은 것"""
        colour = self._skin_colour(self._skin)
        if colour is not None:
            return self._make_colour(stage, colour)
        if self._skin.startswith("/"):
            found = stage.GetPrimAtPath(self._skin)
            if found is None or not found.IsValid():
                self._loud(f"skin: nothing stands at {self._skin}")
                return None
            material = UsdShade.Material(found)
            if not material:
                self._loud(f"skin: {self._skin} is not a material")
                return None
            return material
        return self._make_skin(stage)

    def _make_colour(self, stage, colour):
        """그 색 하나짜리 머티리얼. 세션 레이어에 세운다"""
        where = f"{SKIN_ROOT}/{SKIN_NAME}"
        if self._skin_made == self._skin:
            made = stage.GetPrimAtPath(where)
            if made is not None and made.IsValid():
                return UsdShade.Material(made)
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                UsdGeom.Scope.Define(stage, SKIN_ROOT)
                material = UsdShade.Material.Define(stage, where)
                shader = UsdShade.Shader.Define(stage, f"{where}/surface")
                shader.CreateIdAttr("UsdPreviewSurface")
                shader.CreateInput("diffuseColor",
                                   Sdf.ValueTypeNames.Color3f).Set(
                                       Gf.Vec3f(*colour))
                material.CreateSurfaceOutput().ConnectToSource(
                    shader.ConnectableAPI(), "surface")
        except Exception as e:
            self._loud(f"skin: could not build {self._skin}: "
                       f"{type(e).__name__}: {e}")
            return None
        self._skin_made = self._skin
        return material

    def _make_skin(self, stage):
        """적어 둔 자리의 머티리얼 하나. 같은 경로면 있던 것을 그대로 쓴다"""
        url = self._skin
        where = f"{SKIN_ROOT}/{SKIN_NAME}"
        if self._skin_made == url:
            made = stage.GetPrimAtPath(where)
            if made is not None and made.IsValid():
                return UsdShade.Material(made)
        try:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                UsdGeom.Scope.Define(stage, SKIN_ROOT)
                material = UsdShade.Material.Define(stage, where)
                shader = UsdShade.Shader.Define(stage, f"{where}/mdl")
                shader.SetSourceAsset(Sdf.AssetPath(url), "mdl")
                shader.SetSourceAssetSubIdentifier(self._skin_name(url), "mdl")
                material.CreateSurfaceOutput("mdl").ConnectToSource(
                    shader.ConnectableAPI(), "out")
        except Exception as e:
            self._loud(f"skin: could not load {url}: "
                       f"{type(e).__name__}: {e}")
            return None
        self._skin_made = url
        self._note(f"material ready: {url}")
        return material

    def wear_skin(self, prim=None) -> bool:
        """미리 챙겨 둔 머티리얼을 대상 장비에 건다. align 이 부른다

        한 자도 안 쓰고 어디에 걸지 정하고(_skin_plan), 풀 것이 있으면 한
        덩이로 풀고(_open_instances), 더 풀 것이 없을 때까지 되풀이한 뒤에
        한 덩이로 건다. 세션 레이어에만 쓴다. 원본 USD 는 안 건드린다
        되풀이  인스턴스 안쪽의 인스턴스는 바깥을 풀기 전에는 프록시라서
                 안 보인다. 한 번만 훑으면 그것들이 프록시로 남고, 프록시에
                 걸려다 터져서 그 장비의 첫 SIM 은 아무것도 안 칠해졌다
        _skin_worn  같은 장비에 같은 것을 이미 걸어 뒀으면 손대지 않는다
        """
        if not self._skin_use:
            if self._skin:
                self._loud("skin: the skin box is off, nothing bound")
            self.strip_skin()
            return False
        if not self._skin:
            self.strip_skin()
            return False
        if prim is None:
            prim = (self._target or {}).get("equipment")
        stage = self._get_stage()
        if stage is None or prim is None or not prim.IsValid():
            self._loud("skin: no equipment to bind on yet")
            self.strip_skin()
            return False
        worn = (str(prim.GetPath()), self._skin)
        if worn == self._skin_worn:
            self._loud(f"skin: already on {worn[0]}, left alone")
            return True
        self.strip_skin()
        material = self._skin_material(stage)
        if material is None:
            self._loud(f"skin: could not resolve {self._skin}")
            return False
        try:
            with self._phase("skin"):
                meshes = []
                for _ in range(SKIN_DEEP):
                    with self._stage_timer("skin: plan"):
                        roots, meshes = self._skin_plan(prim)
                    if not roots:
                        break
                    with self._stage_timer("skin: open"):
                        self._open_instances(stage, roots)
                with self._stage_timer("skin: bind"):
                    self._bind_all(stage, material, meshes)
        except Exception as e:
            self._loud(f"skin: could not bind {self._skin}: "
                       f"{type(e).__name__}: {e}")
            return False
        if not self._skin_wrote:
            self._loud(f"skin: no mesh under {worn[0]} to bind")
            return False
        self._skin_worn = worn
        self._loud(f"skin: bound {self._skin} on {len(self._skin_wrote)} "
                   f"place(s) under {worn[0]}")
        return True

    def drop_skin(self) -> None:
        """세워 둔 머티리얼까지 치우고 열어 둔 인스턴스를 도로 묶는다"""
        self.strip_skin()
        self.close_skin()
        self._skin_made = ""
        stage = self._get_stage()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(SKIN_ROOT).IsValid():
                stage.RemovePrim(SKIN_ROOT)

    @staticmethod
    def _bind_skin(prim, material) -> None:
        """그 메시에 직접 건다. 제 프림에 건 것이 제일 세다

        조상에 strongerThanDescendants 로 걸면 그 아래 전부를 다시 풀게
        만들어 킷이 오래 멈춘다. 메시마다 직접 걸면 그 메시들만 바뀐다
        """
        UsdShade.MaterialBindingAPI(prim).Bind(material)

    def _skin_plan(self, root) -> tuple:
        """어디에 걸지만 먼저 정한다. 한 자도 안 쓴다

        프록시 안까지 읽기만 하며 내려간다. 인스턴스를 만나면 그 뿌리를
        적고도 계속 내려가 안쪽 메시까지 모은다. 뿌린 뒤에 한 덩이로 푼다
        """
        roots, meshes, stack = [], [], [root]
        while stack:
            one = stack.pop()
            where = str(one.GetPath())
            if where in OURS or where.startswith(OURS_UNDER):
                continue
            try:
                inside = bool(one.IsInstance())
            except Exception:
                inside = False
            if inside:
                roots.append(where)
            if one.GetTypeName() in GEOMETRY_TYPES:
                meshes.append(where)
                continue
            stack.extend(_children(one))
        return roots, meshes

    def _open_instances(self, stage, roots) -> int:
        """적어 둔 인스턴스를 한 덩이로 푼다. 재구성이 한 번만 돈다

        프록시 경로에는 USD API 로 못 쓴다. 레이어에 스펙을 직접 깔면
        블록이 닫힐 때 위에서 아래로 한 번에 합성되어 중첩까지 같이 풀린다
        """
        layer = stage.GetSessionLayer()
        with Sdf.ChangeBlock():
            for path in roots:
                spec = Sdf.CreatePrimInLayer(layer, path)
                if spec is not None:
                    spec.SetInfo("instanceable", False)
        already = set(self._skin_opened)
        self._skin_opened.extend(p for p in roots if p not in already)
        self._loud(f"skin: opened {len(roots)} instance(s) in one block")
        return len(roots)

    def _bind_all(self, stage, material, meshes) -> int:
        """정해 둔 메시에 한 덩이로 건다. 통지가 한 번만 간다

        프림은 블록 밖에서 미리 집는다. 블록 안에서는 스테이지를 안 읽는다
        하나가 안 걸려도 나머지는 건다. 프록시가 하나 섞여 있다고 그 장비를
        통째로 안 칠하면 안 된다
        """
        ready = []
        for path in meshes:
            one = stage.GetPrimAtPath(path)
            if one is not None and one.IsValid():
                ready.append((path, one))
        missed = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            with Sdf.ChangeBlock():
                for path, one in ready:
                    try:
                        self._bind_skin(one, material)
                    except Exception:
                        missed += 1
                        continue
                    self._skin_wrote.append(path)
        if missed:
            self._loud(f"skin: {missed} of {len(ready)} place(s) refused the "
                       f"binding")
        return len(ready) - missed

    @staticmethod
    def _skin_colour(text: str):
        """색으로 읽히면 (r, g, b). 머티리얼 경로면 None

        #rrggbb, 0.8,0.1,0.1, 204,26,26 셋 다 받는다. 1 을 넘으면 255 로 나눈다
        """
        one = (text or "").strip()
        if one.startswith("#") and len(one) == 7:
            try:
                return tuple(int(one[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            except ValueError:
                return None
        parts = [p for p in one.replace(" ", "").split(",") if p]
        if len(parts) != 3:
            return None
        try:
            got = [float(p) for p in parts]
        except ValueError:
            return None
        if any(v < 0.0 or v > 255.0 for v in got):
            return None
        return (tuple(v / 255.0 for v in got) if max(got) > 1.0
                else tuple(got))

    @staticmethod
    def _skin_name(url: str) -> str:
        """.mdl 안에서 찾을 머티리얼 이름. 파일 이름을 그대로 쓴다"""
        stem = url.replace("\\", "/").rsplit("/", 1)[-1].split("?", 1)[0]
        return stem[:-4] if stem.lower().endswith(".mdl") else stem

    def set_min_gaps(self, side: float, ceiling: float) -> None:
        """면마다 지켜야 하는 최소 여유(m)

        _face_marks  미달 판정과 색. 기본값은 MIN_GAP_SIDE / MIN_GAP_CEILING
        """
        self._min_gap = {FACE_CEILING: max(0.0, float(ceiling)),
                         FACE_LEFT: max(0.0, float(side)),
                         FACE_RIGHT: max(0.0, float(side))}

    def set_precision(self, mode: str) -> None:
        """충돌 판정 정밀도. 모르는 값이면 그대로 둔다

        check_collision     bbox<->triangle 전환 지점 (PRECISION_TRI 비교)
        _nearest_in_prism   빈 면 거리 쪽의 같은 전환
        """
        if mode in (PRECISION_BBOX, PRECISION_MESH, PRECISION_TRI):
            self._precision = mode
        else:
            self._note(f"unknown precision '{mode}', keeping {self._precision}")

    def set_offset_scale(self, mode: str) -> None:
        """포트 offset 을 거리로 바꾸는 방식

        _coords_by_offset / _coords_by_puls / _snap_shift  세 방식의 본체
        SCALE_MODES  모드를 늘리려면 여기 + dummy_ui 콤보
        """
        mode = (mode or "").strip().lower()
        self._offset_scale = mode if mode in SCALE_MODES else SCALE_FIXED

    def set_show_lasers(self, on: bool) -> None:
        """align 이 확인용 레이저를 그릴지

        show_port_lasers  그리는 곳. 굵기·색은 LASER_RADIUS, LASER_COLOR
        """
        self._lasers = bool(on)

    def set_rail_root(self, path: str) -> None:
        """레일 프림의 부모 경로. 바뀌면 레일 색인을 버린다

        _rails_from  레일 인덱스. 첫 align 때 만든다 (RAIL_PREFIX)
        """
        self._rail_index = None
        self._rail_root = (path or "").strip()

    def set_search_root(self, path: str) -> None:
        """EQP_ 장비를 찾을 서브트리. 바뀌면 색인과 Init 을 버린다

        _walk  순회 범위와 가지치기. init 이 느리면 여기부터 (PRUNE_TYPES)
        """
        path = (path or "").strip()
        if path != self._search_root:
            self._eqp_index = {}
            self._ready = False
        self._search_root = path

    def get_payload(self) -> dict:
        """마지막 단계가 남긴 결과. 장비별 판정 기록은 get_result 쪽"""
        return dict(self._result)

    def get_timings(self) -> list:
        """마지막 단계의 구간별 시간"""
        return [list(t) for t in self._timings]

    def teardown(self) -> None:
        """그린 것, 카메라, 색인, 캐시를 전부 놓는다

        teardown  카메라 프림을 실제로 지우는 유일한 곳
        """
        self.strip_skin()
        self.drop_skin()
        self._camera.remove(self._get_stage())
        self.clear_markers()
        self.clear_port_lasers()
        self.clear_sweep()
        self.hide_ebs()
        self._eqp_index = {}
        self._port_map = {}
        self._triangles = {}
        self._local = {}
        self._faces = {}
        self._parts = {}
        self._visible = {}
        self._timings = []
        self._ready = False
        self._target = None
        self._aligned = False
        self._result = {}


    def _begin(self, step: str = "") -> None:
        """한 단계를 시작한다. 시간과 로그를 비운다. 훑어 둔 잎도 버린다"""
        self._leaves = {}
        self._step = step
        self._progress = 0.0
        self._spent = {}
        self._boxed = {}
        self._timings = []
        self._notes = []
        self._blocked = ""
        self._phases = {}
        self._legs = 1
        self._leg = 0
        self._started = time.perf_counter()

    def _done(self, payload: dict) -> dict:
        """단계 하나에 한 줄. 무엇을 했고 얼마나 걸렸나"""
        self._note(f"{self._step or 'step'}: {payload.get('reason', '')}")
        return payload

    def _fail(self, key: str, reason: str, short: str = ""):
        """실패 사유를 적고 None. 부른 쪽이 payload 로 감싼다"""
        self._why = short or reason
        self._note(f"{key}: {reason}")
        return None

    @contextmanager
    def _phase(self, name: str):
        """그 단계에 걸린 시간을 이름별로 모은다. 한 줄 보고과 진행도에 쓴다"""
        started = time.perf_counter()
        before, self._doing = self._doing, name
        try:
            yield
        finally:
            self._doing = before
            self._phases[name] = (self._phases.get(name, 0.0)
                                  + time.perf_counter() - started)

    def add_phase(self, name: str, spent: float) -> None:
        """바깥에서 잰 시간을 같은 줄에 얹는다. UI 가 오버레이 시간을 준다"""
        self._phases[name] = self._phases.get(name, 0.0) + float(spent)

    def phase_line(self) -> str:
        """단계 이름과 걸린 시간. 콘솔에 남기는 것은 이 한 줄뿐이다"""
        spent = time.perf_counter() - self._started
        parts = " | ".join(f"{label} : {self._phases[key]:.2f}s"
                           for key, label in PHASES
                           if self._phases.get(key, 0.0) >= 0.005)
        return (f"[ebs] {self._step or 'step'} : {spent:.2f}s"
                + (f" | {parts}" if parts else ""))

    def say_phases(self) -> str:
        """단계 한 줄을 찍고 그대로 돌려준다"""
        line = self.phase_line()
        print(line)
        return line

    def _note(self, text: str) -> None:
        """notes 에 남긴다. 콘솔은 단계마다 _done 한 줄뿐"""
        self._notes.append(text)

    def _loud(self, text: str) -> None:
        """notes 에 남긴다. 한 줄 보고만 콘솔에 나간다"""
        self._notes.append(text)

    def get_notes(self) -> list:
        """이번 단계에 남긴 자세한 기록

        _note   콘솔에 안 찍는다. 단계마다 _done 이 한 줄만 찍는다
        _done   무엇을 했고 얼마나 걸렸나. 자세한 것은 여기로
        """
        return list(self._notes)

    def _hush(self, loud: bool):
        """loud 가 아니면 그 안의 print 를 삼킨다"""
        return nullcontext() if loud else redirect_stdout(io.StringIO())

    @contextmanager
    def _stage_timer(self, label: str):
        """그 구간이 얼마나 걸렸는지 재서 timings 에 담는다"""
        started = time.perf_counter()
        try:
            yield
        finally:
            self._timings.append([label, (time.perf_counter() - started) * 1000.0])


    def init(self) -> dict:
        """USD 를 열고 장비 색인·상자 목록·포트 표를 만든다. 지오메트리는 안 읽는다

        _stage_boxes   스테이지 상자 목록. Init 값의 대부분이다. EBS 는 안 담는다
        _bounds_cache  공유 바운드 캐시. 움직이는 EBS 는 _moving_cache 로 따로
        """
        self._begin("init")
        self._eqp_boxes = None
        self._bounds = None
        self._stage_index = None
        self._leaves = {}
        self._ebs_box = None
        self._ready = False
        self._target = None
        self._aligned = False
        self._verdict = {}
        self._results = {}
        self._triangles = {}
        self._local = {}
        self._faces = {}
        self._parts = {}
        if not self.open_stage():
            return self._payload(False, f"Could not open {self._usd_path}")
        if self._get_stage() is None:
            return self._payload(False, "No stage open - give a USD path")
        if self._camera.make(self._get_stage()):
            self._note(f"camera {CAMERA_PATH} created (the viewport switches "
                       f"to it when the camera step runs)")

        self.warm_skin()
        self.hide_ebs()
        equipment = self.build_index()
        Collide._stage_boxes(self)
        ports = self.load_ports()
        self._ready = equipment > 0 and ports > 0
        self._note(f"indexed {equipment} equipment, {ports} port entries")
        if not equipment:
            return self._payload(False, "No EQP_ prims found - check the search root")
        if not ports:
            return self._payload(False, "No ports read - check the XML path")
        return self._payload(True, f"Ready: {equipment} equipment, {ports} port entries")

    def prepare(self, equipment: str = "") -> dict:
        """장비를 확정하고 포트 수·EBS·피봇을 잡는다

        _resolve_by_name / _resolve_by_selection  찾는 두 길
        resolve_anchor  피봇을 어디로 볼지. 깊이는 ANCHOR_DEPTH
        """
        self._begin("prepare")
        if not self._ready:
            return self._done(self._payload(False, "Run Init first"))
        return self._done(self._do_prepare(equipment))

    def align(self, equipment: str = "") -> dict:
        """prepare 를 품고, 포트 위치를 계산해 EBS 를 놓는다

        compute_port_points / compute_target  포트 좌표와 놓을 목표점 (snap 보정 포함)
        find_rail  레일 고르기. 직선/코너 판정은 _rail_axis. 유격은 CAD_SLACK
        _place_ebs  이동. 회전·스케일은 _align_prims 와 _write_transform
        """
        self._begin("align")
        if not self._ready:
            return self._done(self._payload(False, "Run Init first"))
        made = self._do_prepare(equipment)
        if not made["ok"]:
            return self._done(made)
        return self._done(self._do_align())

    async def align_async(self, equipment: str = "") -> dict:
        """align 인데 화면이 잦아들 때까지 기다려 그 시간까지 담는다"""
        told = self.align(equipment)
        await self.settle_skin()
        return told

    def focus(self, equipment: str = "") -> dict:
        """prepare 를 품고, EBS 를 놓을 자리에 카메라를 세운다

        _do_stage  아직 안 놓았으면 감춘 채로 먼저 놓는다. 담는 것은 EBS 상자다
        EbsSimulateCamera.place  놓는 곳. 거리는 CAMERA_BACK
        _grab / _turn / _zoom / _double  좌드래그 공전, 휠 줌, 더블클릭 중심 옮기기
        """
        self._begin("focus")
        if not self._ready:
            return self._done(self._payload(False, "Run Init first"))
        made = self._do_prepare(equipment)
        if not made["ok"]:
            return self._done(made)
        placed = self._do_stage()
        if not placed["ok"]:
            return self._done(placed)
        return self._done(self._do_focus())

    def collide(self) -> dict:
        """3면 충돌과 여유 거리를 재고 마커를 그린다

        _do_collide  이 단계의 순서가 전부 여기 있다
        check_collision / measure_faces / check_equipment  3면, 빈 면 거리,
                     내부 간섭. 막힌 면은 파고든 깊이를 음수로 준다
        _flat_gap / _mesh_parts  같은 덩어리의 같은 높이인 면들을 합쳐 그 중앙
        _mesh_local / _cube_local  삼각형을 어디서 얻나. 못 얻으면 상자 (_boxed)
        _face_marks / _lead_path  선은 앞 모서리 중점에서, 안내선은 뒤로 갔다가
                     한 번 꺾어 잰 자리로 (LEAD_FACES, LEAD_FRONT)
        _lead_patch / _sliced / _stop_at  안내선이 무엇에 닿으면 멈추나
                     (LEAD_ROOM, LEAD_TOL, LEAD_PATCH)
        EbsSimulateMarks  씬에 그리는 것은 전부 ebs_simulate_overlay 에
        show_markers / build_verdict  씬에 그리기와 오버레이가 읽을 판정
        """
        self._begin("collide")
        return self._done(self._do_collide())

    def sweep_ports(self) -> dict:
        """장비 전체의 피봇과 포트 1 을 재서 표로 뽑는다 (진단용)

        sweep_ports  판정 기준은 PIVOT_TOLERANCE, PIVOT_ACROSS
        show_sweep   그리기. 색은 SWEEP_COLOR_*
        """
        self._begin("sweep")
        if not self._ready:
            return self._payload(False, "Run Init first")
        stage = self._get_stage()
        if stage is None:
            return self._payload(False, "No stage open")

        names = sorted(self._eqp_index)
        spots, rows, failed = {}, [], []
        parents = {}
        tc = Usd.TimeCode.Default()
        with self._stage_timer(f"port 1 of {len(names)} equipment"):
            for position, name in enumerate(names):
                eqp_id = name[len(EQP_PREFIX):] if name.startswith(EQP_PREFIX) else name
                row = {"equipment": eqp_id, "prim": self._eqp_index[name]}
                rows.append(row)
                try:
                    prim = stage.GetPrimAtPath(self._eqp_index[name])
                    anchor, reached = self.resolve_anchor(prim)
                    row["pivot_ok"] = "TRUE" if reached else "FALSE"
                    if not reached:
                        row["why"] = (f"nothing {ANCHOR_DEPTH} levels down to "
                                      f"measure against")
                        failed.append((eqp_id, row["why"]))
                        continue
                    here = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(
                        tc).ExtractTranslation()

                    self._rail_frame = None
                    with self._hush(position == 0):
                        found = self.compute_port_points(stage, eqp_id)
                    if found is None:
                        listed = self._port_map.get(eqp_id.upper())
                        if listed is None:
                            row["pivot_ok"] = "no-xml"
                        elif len(listed) < MIN_PORTS:
                            row["pivot_ok"] = f"port{len(listed)}"
                        else:
                            row["pivot_ok"] = "xml-invalid"
                        row["why"] = self._why or "배치 계산 실패"
                        failed.append((eqp_id, row["why"]))
                        continue
                    points, axis, rail = found
                    if 1 not in points or self._rail_frame is None:
                        row["pivot_ok"] = "xml-invalid"
                        row["why"] = "포트 1 위치 없음"
                        failed.append((eqp_id, row["why"]))
                        continue

                    parent = rail.GetParent()
                    key = str(parent.GetPath()) if parent else ""
                    if key not in parents:
                        parents[key] = self._parent_world(rail)
                    to_world = parents[key]
                    port = to_world.Transform(points[1])
                    row.update(self._measure(to_world, axis, rail, port, here,
                                             self._port_addr.get(eqp_id.upper())))
                    row["_port"], row["_here"] = port, here

                    row["pivot_ok"] = self._pivot_state(row, here, eqp_id)
                except Exception as e:
                    row["pivot_ok"] = "error"
                    row["why"] = f"{type(e).__name__}: {e}"
                    failed.append((eqp_id, row["why"]))

        self._mark_shared(rows)

        for row in rows:
            if "_draw" not in row:
                continue
            doubted = str(row.get("pivot_ok", "")).startswith("invalid")
            spots[row["equipment"]] = (row["_port"] if doubted else row["_draw"],
                                       row["_here"])

        self._blocked = ""
        with self._stage_timer("draw sweep"):
            try:
                drawn = self.show_sweep(spots)
            except Exception as e:
                return self._payload(False, f"Could not draw the sweep: {e}")
        self._note(f"port 1 and equipment drawn for {drawn} of "
                   f"{len(names)} equipment")
        self._report_spread(rows)

        grouped = {}
        for name, why in failed:
            grouped.setdefault(why, []).append(name)
        for why, skipped in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            self._note(f"{len(skipped)} skipped, {why}: " + ", ".join(skipped[:6])
                       + (" ..." if len(skipped) > 6 else ""))
        return self._payload(drawn > 0, f"{drawn} equipment swept"
                             if drawn else "Nothing drawn",
                             rows=[{k: v for k, v in row.items()
                                    if not k.startswith("_")} for row in rows])

    def _measure(self, to_world, axis: int, rail, port, here, addr: int) -> dict:
        """레일 방향을 기준으로 피봇과 포트의 어긋남을 잰다"""
        origin_local, onward_local, _ = self._rail_frame
        origin = to_world.Transform(origin_local)
        onward = to_world.Transform(onward_local)
        run = (onward[0] - origin[0], onward[1] - origin[1])
        length = math.sqrt(run[0] ** 2 + run[1] ** 2) or 1.0
        along = (run[0] / length, run[1] / length)
        across = (-along[1], along[0])

        def project(point, unit):
            """그 점을 단위벡터에 투영한 길이"""
            return ((point[0] - origin[0]) * unit[0]
                    + (point[1] - origin[1]) * unit[1])

        pivot_run = project(here, along)
        port_run = project(port, along)
        here_across = project(here, across)

        drawn = Gf.Vec3d(origin[0] + along[0] * port_run + across[0] * here_across,
                         origin[1] + along[1] * port_run + across[1] * here_across,
                         port[2])

        step = self._addr_step(addr, axis)
        per_unit = (step[1] / step[0]) if step and step[0] else None
        row = {
            "axis": "XY"[axis],
            "rail": rail.GetName(),
            "pivot_coord": here[axis],
            "pivot_offset": pivot_run * OFFSET_PER_UNIT,
            "port_coord": port[axis],
            "port_offset": port_run * OFFSET_PER_UNIT,
            "coord_diff": port_run - pivot_run,
            "off_axis_diff": project(port, across) - here_across,
            "offset_diff": (port_run - pivot_run) * OFFSET_PER_UNIT,
            "_draw": drawn,
            "_along": along,
        }
        if per_unit:
            row["puls_per_unit"] = per_unit
            row["pivot_offset_puls"] = pivot_run * per_unit
            row["port_offset_puls"] = port_run * per_unit
        return row

    def _pivot_state(self, row: dict, here, eqp_id: str) -> str:
        """그 피봇을 믿을 수 있나. 원점·축·포트 수를 본다"""
        off = []
        if abs(row["coord_diff"]) > PIVOT_TOLERANCE:
            off.append("axis")
        if abs(row["off_axis_diff"]) > PIVOT_TOLERANCE * PIVOT_ACROSS:
            off.append("across")
        count = len(self._port_map.get(eqp_id.upper(), ()))
        if abs(here[0]) < 1e-6 and abs(here[1]) < 1e-6:
            return "invalid:origin"
        if off:
            return "invalid:" + "+".join(off)
        if count > MAX_PORTS:
            return f"port{count}"
        return "TRUE"

    def _mark_shared(self, rows: list) -> None:
        """같은 자리를 여러 장비가 피봇으로 쓰면 표시한다"""
        seen = {}
        for row in rows:
            if "pivot_coord" not in row:
                continue
            spot = (round(row["pivot_coord"], 4), round(row.get("off_axis_diff", 0.0), 4))
            seen.setdefault(spot, []).append(row)
        for spot, sharing in seen.items():
            if len(sharing) < 2:
                continue
            for row in sharing:
                state = str(row.get("pivot_ok", "TRUE"))
                if state.startswith("invalid"):
                    row["pivot_ok"] = state + "+shared"

    def _report_spread(self, rows: list) -> None:
        """offset_diff 의 최소·중앙·최대"""
        gaps = [r["offset_diff"] for r in rows
                if "offset_diff" in r and r.get("pivot_ok") == "TRUE"]
        doubted = sum(1 for r in rows
                      if str(r.get("pivot_ok", "")).startswith("invalid"))
        if not gaps:
            return
        middle = sorted(gaps)[len(gaps) // 2]
        self._note(f"offset_diff over {len(gaps)}: min {min(gaps):.0f}, "
                   f"median {middle:.0f}, max {max(gaps):.0f}, "
                   f"mean {sum(gaps) / len(gaps):.0f}")
        if doubted:
            self._note(f"{doubted} left out: a pivot that cannot be one, or more "
                       f"ports than the EBS spans")

    def simulate(self, equipment: str = "") -> dict:
        """카메라를 먼저 잡고, EBS 를 보이고, 잰다

        simulate  순서를 바꾸려면 여기. 오버레이는 collide 뒤에 뜬다
        한 걸음이 한 줄이다. prepare 로 고르고, _do_stage 로 놓고, _do_focus 로
        담고, wear_skin 으로 입히고, show_ebs 로 보이고, _do_collide 로 잰다
        """
        self._begin("simulate")
        if not self._ready:
            return self._payload(False, "Run Init first")
        result = self._do_prepare(equipment)
        if not result["ok"]:
            return result
        placed = self._do_stage()
        if not placed["ok"]:
            return placed
        told = self._do_focus()
        if not told["ok"]:
            return told
        self.wear_skin()
        self.show_ebs(self._target["ebs"])
        result = self._do_collide()
        if not result["ok"]:
            return result
        result["timings"] = list(self._timings)
        result["notes"] = list(self._notes)
        result["total_ms"] = (time.perf_counter() - self._started) * 1000.0
        return self._done(result)

    async def simulate_async(self, equipment: str = "") -> dict:
        """simulate 인데 collide 만 프레임에 나눠 돈다. 나머지는 짧다"""
        import omni.kit.app
        self._begin("simulate")
        self.set_legs(3)
        if not self._ready:
            return self._done(self._payload(False, "Run Init first"))
        result = self._do_prepare(equipment)
        if not result["ok"]:
            return self._done(result)
        placed = self._do_stage()
        if not placed["ok"]:
            return self._done(placed)
        told = self._do_focus()
        if not told["ok"]:
            return self._done(told)
        if self.wear_skin():
            await self.settle_skin()
        self.next_leg()
        self.show_ebs(self._target["ebs"])
        for _ in self._collide_steps():
            await omni.kit.app.get_app().next_update_async()
        self.next_leg()
        result = self._result
        if not result["ok"]:
            return self._done(result)
        result["timings"] = list(self._timings)
        result["notes"] = list(self._notes)
        result["total_ms"] = (time.perf_counter() - self._started) * 1000.0
        return self._done(result)


    def _do_prepare(self, equipment: str) -> dict:
        """장비를 찾아 포트 수로 EBS 를 고르고 피봇을 잡는다"""
        self._target = None
        self._aligned = False

        stage = self._get_stage()
        if stage is None:
            return self._payload(False, "No stage open")

        with self._stage_timer("resolve equipment"):
            eqp_prim = (self._resolve_by_name(stage, equipment) if equipment.strip()
                        else self._resolve_by_selection(stage))
        if eqp_prim is None:
            return self._payload(False, "Equipment prim not found: "
                                 f"{equipment.strip() or '(no selection)'}")

        eqp_id = self._equipment_id(eqp_prim)

        with self._stage_timer("port lookup"):
            port_count = self.get_port_count(eqp_id)
        if port_count is None:
            return self._payload(False, f"No port info for '{eqp_id}' in XML",
                                 equipment=eqp_prim, eqp_id=eqp_id)
        if port_count not in (2, 3):
            return self._payload(False, f"{port_count}-port equipment: no matching EBS",
                                 equipment=eqp_prim, eqp_id=eqp_id, port_count=port_count)

        ebs_path = self._ebs_path_2port if port_count == 2 else self._ebs_path_3port
        ebs_prim = stage.GetPrimAtPath(ebs_path) if ebs_path else None
        if ebs_prim is None or not ebs_prim.IsValid():
            return self._payload(False, f"Invalid {port_count}-port EBS prim path: {ebs_path}",
                                 equipment=eqp_prim, eqp_id=eqp_id, port_count=port_count)

        with self._stage_timer("resolve anchor"):
            anchor, reached = self.resolve_anchor(eqp_prim)
        if not reached:
            self._note(f"{eqp_id}: nothing {ANCHOR_DEPTH} transform levels down, "
                  f"working off the equipment prim")
        self._target = {
            "equipment": eqp_prim,
            "eqp_id": eqp_id,
            "port_count": port_count,
            "ebs": ebs_prim,
            "anchor": anchor,
        }
        return self._payload(True, f"Prepared: {eqp_id} ({port_count} port)")

    def _do_stage(self) -> dict:
        """카메라를 세우기 전에 해 둘 것. 그린 것을 걷고, 아직이면 놓는다

        놓을 때는 보임 상태를 그대로 두고 머티리얼도 안 입힌다. 보이는 것도
        입히는 것도 부른 쪽이 제 차례에 한다
        clear_markers  놓기 전에 걷는다. 놓으면서 그린 레이저가 살아남는다
        """
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        self.clear_markers()
        if self._aligned:
            return self._payload(True, "EBS already placed")
        ebs = self._target["ebs"]
        return self._do_align(reveal=self._is_visible(ebs, str(ebs.GetPath())),
                              wear=False)

    def _do_focus(self) -> dict:
        """EBS 상자를 담도록 카메라를 세운다. 놓는 것은 _do_stage 몫이다"""
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        stage = self._get_stage()
        ebs = self._target["ebs"]
        anchor = self._target["anchor"]
        facing = anchor if (anchor is not None and anchor.IsValid()) else ebs
        with self._phase("camera"), self._stage_timer("camera focus"):
            told = self._camera.place(stage, self._framed_box(), facing)
        if told:
            self._note(told)
        return self._payload(bool(told), "Camera on the EBS" if told
                             else "Camera focus failed")

    def _framed_box(self):
        """카메라가 담을 상자. 아직 감춰 둔 EBS 는 잠깐 켜서 잰다"""
        ebs = self._target["ebs"]
        box = self._world_range(ebs)
        if box is not None and not box.IsEmpty():
            return box
        self.show_ebs(ebs)
        box = self._world_range(ebs)
        self._show_ebs([ebs], False)
        return box

    def _do_align(self, reveal: bool = True, wear: bool = True) -> dict:
        """포트 좌표로 목표점을 구해 EBS 를 놓는다. 못 구하면 피봇에 맞춘다

        reveal  False 면 자리만 잡고 감춰 둔다. 카메라를 먼저 잡는 SIM 이 쓴다
        wear  False 면 머티리얼을 안 건다. 카메라가 속으로 부를 때 그렇다
        clear_markers  collide 이전 단계라, 지난 판정과 그린 것을 먼저 지운다
        """
        if self._target is None:
            return self._payload(False, "Run Prepare first")
        self.clear_markers()
        stage = self._get_stage()
        anchor = self._target["anchor"]

        with self._phase("place"), self._stage_timer("align EBS"):
            self._base = self.compute_target(stage, self._target["eqp_id"], anchor)
            target = self._pushed(anchor, self._base)
            if target is not None:
                self._aligned = self._place_ebs(self._target["ebs"], target, anchor)
                note = ("EBS placed at port 0, world "
                        f"({target[0]:.3f}, {target[1]:.3f}, {target[2]:.3f})")
            elif self._blocked:
                self._port_world = {}
                self.clear_port_lasers()
                return self._payload(False, self._blocked)
            else:
                self._note("port geometry unavailable, falling back to the anchor prim")
                self._port_world = {}
                self._aligned = self._align_prims(self._target["ebs"], anchor)
                note = "EBS aligned to the anchor prim"

        Collide._forget_triangles(self, self._target["ebs"])
        self._ebs_box = None
        if wear:
            self.wear_skin()

        if reveal:
            self.show_ebs(self._target["ebs"])

        if self._lasers:
            with self._stage_timer("draw port lasers"):
                drawn = self.show_port_lasers()
            if drawn:
                self._note(f"{drawn} port laser(s) drawn under {LASER_ROOT}")
        else:
            self.clear_port_lasers()
        return self._payload(self._aligned, note if self._aligned else "EBS alignment failed")

    def _do_collide(self) -> dict:
        """3면 충돌, 빈 면 거리, 내부 간섭을 재고 판정과 마커까지"""
        with self._phase("collide"):
            for _ in self._collide_steps():
                pass
        return self._result

    async def collide_async(self) -> dict:
        """collide 를 프레임마다 한 단계씩. 도는 동안 화면이 안 멈춘다"""
        import omni.kit.app
        self._begin("collide")
        self.set_legs(2)
        for _ in self._collide_steps():
            await omni.kit.app.get_app().next_update_async()
        self.next_leg()
        return self._done(self._result)

    def get_progress(self) -> float:
        """지금 단계가 얼마나 왔나. 0.0 에서 100.0

        _collide_steps  단계 사이에서 멈춘다. 그 틈에 collide_async 가 프레임을
                     넘기고, 그동안 이걸 물어보면 된다
        _warm_steps  가까운 프림의 바운드를 미리 잰다. collide 시간의 대부분이
                     여기다. WARM_CHUNK 개씩 끊으니 이 안에서도 올라간다
        COLLIDE_STEPS  단계마다 몫이 얼마인가. 첫 번만 쓰는 추정치다
        _learn_shares  한 번 돌고 나면 실제로 걸린 시간의 비율로 몫을 다시 잡는다
        settle_skin  머티리얼은 정착만 길다. 지난번 걸린 시간으로 어림잡아
                     올리고 99 에서 기다리다 끝나면 100 을 찍는다
        _leg  한 동작이 여러 구간이면 구간마다 제 몫 안에서만 돈다. 두 구간
                     이면 앞이 0~50, 뒤가 50~100 이다. 막대는 하나로 이어진다
        """
        inner = max(0.0, min(self._progress, 100.0))
        return (self._leg + inner / 100.0) / self._legs * 100.0

    def set_legs(self, legs: int) -> None:
        """이 동작이 몇 구간인지. 막대 하나를 그만큼 나눠 쓴다"""
        self._legs = max(1, int(legs))
        self._leg = 0
        self._progress = 0.0

    def next_leg(self) -> None:
        """다음 구간으로 넘어간다. 그 구간의 진행도는 0 부터 다시 센다"""
        self._leg = min(self._leg + 1, self._legs - 1)
        self._progress = 0.0

    def _at(self, step: str, done: float = 1.0) -> None:
        """그 단계가 done(0~1) 만큼 왔다고 적는다"""
        weights = self._shares or dict(COLLIDE_STEPS)
        before = 0.0
        for name, _ in COLLIDE_STEPS:
            if name == step:
                break
            before += weights.get(name, 0.0)
        share = weights.get(step, 0.0) * max(0.0, min(done, 1.0))
        self._progress = min(before + share, 100.0)

    def _reached(self, step: str) -> None:
        """그 단계가 끝났다고 적는다"""
        self._at(step, 1.0)

    @contextmanager
    def _spending(self, step: str):
        """그 단계에 쓴 시간을 더한다. 프레임을 기다린 시간은 안 센다"""
        started = time.perf_counter()
        try:
            yield
        finally:
            self._spent[step] = (self._spent.get(step, 0.0)
                                 + time.perf_counter() - started)

    def _spent_line(self) -> str:
        """외부와 내부에 각각 얼마나 썼나, 단계마다 얼마였나. 디버깅용 한 줄"""
        def spent(names, on):
            """켠 쪽은 합과 단계별 내역, 끈 쪽은 skipped"""
            if not on:
                return "skipped"
            whole = sum(self._spent.get(one, 0.0) for one in names)
            each = ", ".join(f"{one} {self._spent.get(one, 0.0):.2f}"
                             for one in names)
            return f"{whole:.2f}s ({each})"

        rest = ", ".join(f"{name} {self._spent.get(name, 0.0):.2f}"
                         for name, _ in COLLIDE_STEPS
                         if name not in OUTER_STEPS and name not in INNER_STEPS)
        return (f"collide: outer {spent(OUTER_STEPS, self._outer)}, "
                f"inner {spent(INNER_STEPS, self._inner)}, {rest}")

    def _learn_shares(self) -> None:
        """이번에 걸린 시간으로 다음 번 몫을 잡는다. 첫 번은 COLLIDE_STEPS"""
        total = sum(self._spent.values())
        if total <= 0.0:
            return
        self._shares = {name: self._spent.get(name, 0.0) / total * 100.0
                        for name, _ in COLLIDE_STEPS}

    def _warm_steps(self, ebs_prim, skip: list):
        """가까운 프림의 바운드를 미리 잰다. collide 시간의 대부분이 여기다"""
        stage = self._get_stage()
        if stage is None:
            return
        cache = Collide._bounds_cache(self)
        search = Collide._reach_box(self, ebs_prim)
        if search is None:
            return
        with self._spending("warm"):
            inside, _ = Collide._index_inside(self, cache, search, skip)
        self._note(f"warming {len(inside)} prims around the EBS")
        for at, prim in enumerate(inside, 1):
            with self._spending("warm"):
                Collide._gather_nearby(self, stage, cache, search, skip, [prim])
            if at % WARM_CHUNK and at != len(inside):
                continue
            self._at("warm", at / len(inside))
            yield

    def _collide_steps(self):
        """collide 를 단계로 쪼갠 것. 단계마다 진행률을 올리고 한 번 멈춘다"""
        if self._target is None:
            self._payload(False, "Run Prepare first")
            return
        if not self._aligned:
            self._payload(False, "Run Align first")
            return

        apart = [self._target["ebs"], self._target["equipment"]]
        skip = [str(p.GetPath()) for p in apart if p and p.IsValid()]
        bounds = Collide._bounds_cache(self)
        cells = {face: [] for face in FACES}
        distances = {}
        hit_count = 0
        if not self._outer:
            self._note("outer collide off: the three faces were not measured")
            for step in OUTER_STEPS:
                self._reached(step)
            yield
        else:
            for _ in self._warm_steps(self._target["ebs"], skip):
                yield
            self._reached("warm")
            yield

            with self._spending("sides"):
                roots = Collide._side_roots(self)
            self._reached("sides")
            yield

            with self._spending("faces"):
                cells = Collide.check_collision(self, self._target["ebs"], exclude=apart,
                                             cache=bounds, roots=roots)
                hit_count = sum(sum(1 for c in v if c) for v in cells.values())
            self._reached("faces")
            yield

            with self._spending("clearance"):
                distances = Collide.measure_faces(self, self._target["ebs"], cells,
                                               exclude=apart, cache=bounds,
                                               roots=roots)
                for face, found in distances.items():
                    if found.get("distance") is None:
                        self._note(f"{face}: clear, nothing within "
                                   f"{found.get('reach', 0):.3f}")
                    else:
                        self._note(f"{face}: clear, nearest "
                                   f"{found['distance']:.4f} away "
                                   f"({found['prim'].rsplit('/', 1)[-1]})")
            self._reached("clearance")
            yield

        meeting = {"hit": False, "pairs": [], "boxes": [], "tests": 0}
        if not self._inner:
            self._note("inner collide off: the equipment itself was not tested")
        else:
            with self._spending("equipment"):
                try:
                    meeting = Collide.check_equipment(self, self._target["ebs"],
                                                   self._target["equipment"],
                                                   cache=bounds)
                except Exception as e:
                    self._note(f"interference check failed: "
                               f"{type(e).__name__}: {e}")
                if meeting["hit"]:
                    names = [b.rsplit("/", 1)[-1] for _, b in meeting["pairs"]]
                    self._note(f"the EBS runs through the equipment at "
                               f"{len(names)} place(s): " + ", ".join(names[:6])
                               + (" ..." if len(names) > 6 else "")
                               + f" ({meeting['tests']} pairs tested)")
                else:
                    self._note(f"clear of the equipment itself "
                               f"({meeting['tests']} triangle pairs tested)")
                for why, paths in sorted(self._boxed.items()):
                    self._note(f"{len(paths)} judged by box, {why}: "
                               + ", ".join(sorted(p.rsplit("/", 1)[-1]
                                                  for p in paths)[:4])
                               + (" ..." if len(paths) > 4 else ""))
        self._reached("equipment")
        yield

        with self._spending("verdict"), self._stage_timer("verdict: build"):
            try:
                verdict = self.build_verdict(self._target["ebs"], cells,
                                             distances, meeting["hit"],
                                             meeting.get("boxes"))
            except Exception as e:
                verdict = {}
                self._note(f"no overlay verdict: {type(e).__name__}: {e}")
        self._reached("verdict")
        yield

        with self._phase("overlay"), self._spending("markers"), \
                self._stage_timer("markers: draw"):
            self.show_markers(self._target["ebs"], cells,
                              verdict.get("marks"), verdict.get("boxes"))
        self._verdict = verdict

        name = self._keep_result(verdict, meeting)
        if verdict:
            told = self.get_result(name)["reason"]
        else:
            told = ("No collision" if hit_count == 0
                    else f"{hit_count} cell(s) blocked")
            if meeting["hit"]:
                told += ", and through the equipment"
        self._payload(
            True, told,
            cells=cells, hit_count=hit_count, distances=distances,
            equipment_hit=meeting,
        )
        self._reached("markers")
        self._note(self._spent_line())
        self._learn_shares()

    def _keep_result(self, verdict: dict, meeting: dict) -> str:
        """이번 판정을 장비 이름으로 적어 두고 그 이름을 준다"""
        target = self._target or {}
        prim = target.get("equipment")
        name = prim.GetName() if prim is not None else ""
        if not name:
            return ""
        pairs = meeting.get("pairs") or ()
        self._results[name.upper()] = {
            "equipment": name,
            "port_count": target.get("port_count") or 0,
            "inside_hit": bool(meeting.get("hit")),
            "inside": [b.rsplit("/", 1)[-1] for _, b in pairs],
            "offset": round(self._nudge, 4),
            "faces": {mark["face"]: {"hit": mark["state"] == STATE_CLASH,
                                     "gap": mark["distance"],
                                     "name": mark["name"]}
                      for mark in verdict.get("marks") or ()},
        }
        return name

    def get_result(self, equipment: str = "") -> dict:
        """그 장비의 마지막 판정. 화면은 안 건드린다

        equipment, port_count, reason, faces, inside, offset, placeable 일곱.
                     기록이 없어도 키는 다 있다
        offset       좌우로 민 거리(m). 0 이 아니면 제자리 판정이 아니다
        reason       면마다 clear / tight / clash. 닿았으면 clash, 안 닿아도
                     최소 여유 미달이면 tight. 내부는 clear / clash
        faces        면마다 잰 간격(m)과 상대 이름 (RESULT_ORDER 순서)
        placeable    세 면이 다 clear 이고 내부도 안 걸려야 참
        _keep_result  collide 가 적어 두는 곳. 지우는 곳은 Init 하나
        get_results / list_results  통째로, 또는 이름만
        """
        found = self._results.get(self._result_key(equipment)) or {}
        faces, words, snug = {}, [], False
        for face in RESULT_ORDER:
            one = (found.get("faces") or {}).get(face) or {}
            gap = one.get("gap")
            faces[face] = {"gap": gap, "name": one.get("name") or ""}
            if not found:
                continue
            state = (STATE_CLASH if one.get("hit") else
                     STATE_TIGHT
                     if gap is not None and gap < self._min_gap.get(face, 0.0)
                     else STATE_CLEAR)
            snug = snug or state != STATE_CLEAR
            words.append(f"{face} {state}")
        if found:
            words.append(f"{RESULT_INSIDE} "
                         f"{STATE_CLASH if found['inside_hit'] else STATE_CLEAR}")
        return {
            "equipment": found.get("equipment") or "",
            "port_count": found.get("port_count") or 0,
            "reason": " / ".join(words),
            "faces": faces,
            "inside": list(found.get("inside") or ()),
            "offset": found.get("offset") or 0.0,
            "placeable": bool(found) and not found["inside_hit"] and not snug,
        }

    def get_results(self) -> dict:
        """적어 둔 판정 전부. 장비 이름 -> get_result"""
        return {name: self.get_result(name) for name in self.list_results()}

    def list_results(self) -> list:
        """판정을 적어 둔 장비 이름 전부"""
        return sorted(one["equipment"] for one in self._results.values())

    def _result_key(self, equipment: str) -> str:
        """기록에서 찾을 이름. 경로를 줘도, EQP_ 접두가 없어도 찾는다"""
        name = str(equipment or "").strip().rstrip("/").rsplit("/", 1)[-1].upper()
        if not name or name in self._results:
            return name
        return EQP_PREFIX + name

    def owner_name(self, path: str) -> str:
        """메시 경로에서 사람이 아는 이름(장비/그룹)을 뽑는다"""
        parts = [part for part in str(path or "").split("/") if part]
        if not parts:
            return ""
        root = [part for part in (self._search_root or "").split("/") if part]
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] in GROUP_NAMES:
                return parts[i]
            if root and parts[:i] == root:
                return parts[i]
        return parts[-1]

    def build_verdict(self, ebs_prim, cells: dict, distances: dict,
                      inside: bool, boxes: list = None) -> dict:
        """오버레이가 읽을 판정 한 벌. 세울 수 있나, 왜 못 세우나"""
        bbox = Collide._ebs_bound(self, ebs_prim)
        local_box, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local_box.IsEmpty():
            return {}
        lo, hi = local_box.GetMin(), local_box.GetMax()
        spot = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        up_axis = (self._face_planes.get(FACE_CEILING) or (2,))[0]
        tall = hi[up_axis] - lo[up_axis]
        spot[up_axis] = lo[up_axis] + tall * VERDICT_HEIGHT
        middle = to_world.Transform(Gf.Vec3d(*spot))
        spot[up_axis] = lo[up_axis] + tall * CLASH_HEIGHT
        lower = to_world.Transform(Gf.Vec3d(*spot))
        marks = Collide._face_marks(self, local_box, to_world, cells, distances)
        blocked = [{"face": mark["face"], "name": mark["name"],
                    "state": mark["state"]}
                   for mark in marks if mark["state"] != STATE_CLEAR]
        return {
            "marks": marks,
            "right": self._right_way(0),
            "grip": self._grip_spot(local_box, to_world),
            "offset": self._nudge,
            "centre": (middle[0], middle[1], middle[2]),
            "inside_at": (lower[0], lower[1], lower[2]),
            "inside": bool(inside),
            "boxes": list(boxes or ()),
            "faces": blocked,
            "blocked": sum(sum(1 for c in cells.get(face, []) if c)
                           for face in FACES),
            "placeable": not inside and not blocked,
        }

    def _grip_spot(self, local_box, to_world) -> dict:
        """손잡이를 EBS 에 붙이는 법. 자리도 크기도 EBS 안에서 잰다

        손잡이 뿌리에 matrix 를 그대로 걸고 나머지는 EBS 안 좌표로 그리면
        EBS 가 어디로 가든, 크기가 얼마든 손잡이가 같이 간다
        at    3면 판의 앞모서리와 같은 깊이, EBS 높이의 GRIP_HEIGHT
        under  같은 깊이 OFFSET_HEIGHT 높이. 이격 표를 다는 자리
        side  손잡이가 눕는 축. 몸통 중점은 EBS 상자 앞면에 그대로 앉는다
        wide  EBS 폭의 GRIP_WIDE. high  EBS 높이의 GRIP_TALL
        unit  스테이지 한 단위가 몇 미터인가. 끈 픽셀을 미터로 바꿀 때 쓴다
        """
        up_axis = (self._face_planes.get(FACE_CEILING) or (2,))[0]
        front_axis = 3 - up_axis
        side_axis = 3 - up_axis - front_axis
        lo, hi = local_box.GetMin(), local_box.GetMax()
        spot = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
        spot[front_axis] = (lo if LEAD_FRONT < 0 else hi)[front_axis]
        tall = hi[up_axis] - lo[up_axis]
        under = list(spot)
        under[up_axis] = lo[up_axis] + tall * OFFSET_HEIGHT
        spot[up_axis] = lo[up_axis] + tall * GRIP_HEIGHT
        return {"at": tuple(spot), "under": tuple(under),
                "matrix": Gf.Matrix4d(to_world),
                "side": side_axis,
                "unit": self._per_unit(),
                "wide": (hi[side_axis] - lo[side_axis]) * GRIP_WIDE,
                "high": (hi[up_axis] - lo[up_axis]) * GRIP_TALL}

    def _right_way(self, which: int = 0):
        """EBS 가 보는 방향으로 만든 축 하나. 0 은 오른쪽, 2 는 정면(앞)"""
        target = self._target or {}
        anchor = target.get("anchor") or target.get("ebs")
        if anchor is None:
            return None
        try:
            axes = self._camera.axes(self._get_stage(), anchor)
        except Exception:
            return None
        way = axes[which]
        return (way[0], way[1], way[2])

    def get_verdict(self) -> dict:
        """마지막 collide 가 만든 판정

        build_verdict   내용을 바꾸려면 여기
        _verdict_panel  못 세울 때만 한 줄 (VERDICT_HEIGHT), 내부 간섭은 그
                     아래 한 줄 (CLASH_HEIGHT). 글은 오버레이 맨 위에
        """
        return dict(self._verdict)


    def build_index(self) -> int:
        """search root 아래 EQP_ 장비 이름 -> 경로 색인"""
        stage = self._get_stage()
        self._eqp_index = {}
        self._rail_index = None
        self._triangles = {}
        self._local = {}
        self._faces = {}
        self._parts = {}
        if stage is None:
            return 0
        visited = 0
        started = time.perf_counter()
        for prim, name in self._walk(stage):
            visited += 1
            if name.startswith(EQP_PREFIX):
                self._eqp_index[name] = str(prim.GetPath())
        self._timings.append([f"build index (visited {visited})",
                              (time.perf_counter() - started) * 1000.0])
        return len(self._eqp_index)

    def _walk(self, stage: Usd.Stage):
        """프림을 훑는다. PRUNE_TYPES 아래로는 안 내려간다"""
        root = None
        if self._search_root:
            root = stage.GetPrimAtPath(self._search_root)
            if not root.IsValid():
                self._note(f"search root not found, scanning the whole stage: "
                      f"{self._search_root}")
                root = None
        stack = list(_children(root or stage.GetPseudoRoot()))
        while stack:
            prim = stack.pop()
            name = prim.GetName().upper()
            yield prim, name
            if name.startswith(EQP_PREFIX):
                continue
            type_name = prim.GetTypeName()
            if type_name in PRUNE_TYPES or type_name.endswith("Light"):
                continue
            stack.extend(_children(prim))

    def equipment_boxes(self, stage) -> dict:
        """장비 이름 -> 월드 상자. 상자 목록에서 꺼내 쓴다"""
        if self._eqp_boxes is not None:
            return self._eqp_boxes
        with self._stage_timer(f"measure {len(self._eqp_index)} equipment"):
            by_path = {path: box
                       for path, _, _, box, _, _ in Collide._stage_boxes(self)}
            boxes = {name: by_path[path]
                     for name, path in self._eqp_index.items() if path in by_path}
        self._eqp_boxes = boxes
        return boxes

    @staticmethod
    def _cast(box, way) -> tuple:
        """그 타입으로 속성 값을 만든다"""
        lo, hi = box.GetMin(), box.GetMax()
        centre = (lo[0] + hi[0]) * 0.5 * way[0] + (lo[1] + hi[1]) * 0.5 * way[1]
        half = (abs(way[0]) * (hi[0] - lo[0]) + abs(way[1]) * (hi[1] - lo[1])) * 0.5
        return (centre - half, centre + half)

    def side_band(self, stage, ebs_prim, eqp_prim) -> dict:
        """EBS 를 기준으로 좌우 띠 안에 드는 장비를 고른다"""
        boxes = self.equipment_boxes(stage)
        mine = self._equipment_id(eqp_prim)
        key = next((name for name in self._eqp_index
                    if name[len(EQP_PREFIX):] == mine or name == mine), None)
        here = boxes.get(key) or self._world_range(eqp_prim)
        if here is None or here.IsEmpty():
            return {}

        sideways = self._sideways(ebs_prim)
        inward = (-sideways[1], sideways[0])
        my_side = self._cast(here, sideways)
        my_deep = self._cast(here, inward)
        reach = (my_side[1] - my_side[0]) * NEIGHBOUR_REACH
        if reach <= 0:
            return {}

        left = right = None
        for name, box in boxes.items():
            if name == key:
                continue
            deep = self._cast(box, inward)
            if deep[0] >= my_deep[1] or deep[1] <= my_deep[0]:
                continue
            side = self._cast(box, sideways)
            if side[0] >= my_side[1]:
                gap, hand = side[0] - my_side[1], "right"
            elif side[1] <= my_side[0]:
                gap, hand = my_side[0] - side[1], "left"
            else:
                continue
            if gap > reach:
                continue
            if hand == "right":
                if right is None or gap < right[0]:
                    right = (gap, name)
            elif left is None or gap < left[0]:
                left = (gap, name)

        return {
            "beside": [self._eqp_index[one[1]] for one in (left, right) if one],
            "sideways": sideways, "inward": inward,
            "side": (my_side[0] - reach, my_side[1] + reach),
            "deep": my_deep,
        }

    def _sideways(self, ebs_prim) -> tuple:
        """그 상자가 EBS 의 좌우 어느 쪽에 얼마나 걸치나"""
        try:
            row = Collide._ebs_bound(self, ebs_prim).GetMatrix().GetRow(0)
            length = math.sqrt(row[0] ** 2 + row[1] ** 2)
            if length > 1e-9:
                return (row[0] / length, row[1] / length)
        except Exception:
            pass
        return (1.0, 0.0)

    def get_selected_equipment(self) -> str:
        """뷰포트 선택에서 장비 경로 하나

        _resolve_by_selection
        """
        stage = self._get_stage()
        prim = self._resolve_by_selection(stage) if stage else None
        return str(prim.GetPath()) if prim else ""

    def _resolve_by_selection(self, stage: Usd.Stage) -> "Usd.Prim | None":
        """선택된 프림에서 위로 올라가며 EQP_ 장비를 찾는다"""
        paths = omni.usd.get_context().get_selection().get_selected_prim_paths()
        for path in paths:
            prim = stage.GetPrimAtPath(path)
            while prim and prim.IsValid() and prim != stage.GetPseudoRoot():
                if prim.GetName().upper().startswith(EQP_PREFIX):
                    return prim
                prim = prim.GetParent()
        return None

    def _resolve_by_name(self, stage: Usd.Stage, text: str) -> "Usd.Prim | None":
        """이름으로 장비를 찾는다. EQP_ 접두는 없어도 붙인다"""
        text = text.strip()
        if text.startswith("/"):
            prim = stage.GetPrimAtPath(text)
            return prim if prim.IsValid() else None
        key = text.upper()
        if not key.startswith(EQP_PREFIX):
            key = EQP_PREFIX + key

        path = self._eqp_index.get(key)
        if path:
            prim = stage.GetPrimAtPath(path)
            return prim if prim.IsValid() else None

        started = time.perf_counter()
        direct = f"{self._search_root.rstrip('/')}/{key}" if self._search_root else f"/{key}"
        prim = stage.GetPrimAtPath(direct)
        if prim.IsValid():
            self._eqp_index[key] = direct
            self._timings.append(["find equipment (direct path)",
                                  (time.perf_counter() - started) * 1000.0])
            return prim

        visited = 0
        started = time.perf_counter()
        found = None
        for prim, name in self._walk(stage):
            visited += 1
            if name == key:
                self._eqp_index[key] = str(prim.GetPath())
                found = prim
                break
        self._timings.append([f"find equipment (visited {visited})",
                              (time.perf_counter() - started) * 1000.0])
        return found

    @staticmethod
    def _equipment_id(prim: Usd.Prim) -> str:
        """프림 이름에서 EQP_ 를 뗀 장비 번호"""
        name = prim.GetName()
        return name[len(EQP_PREFIX):] if name.upper().startswith(EQP_PREFIX) else name

    @staticmethod
    def resolve_anchor(prim: Usd.Prim, depth: int = ANCHOR_DEPTH):
        """피봇으로 쓸 프림. ANCHOR_DEPTH 만큼 내려가 본다"""
        current, level = prim, 0
        while level < depth:
            children = _children(current) if current and current.IsValid() else []
            if not children:
                return current, False
            first = children[0]
            if first.GetTypeName() in PASS_TYPES:
                current = first
                continue
            current, level = first, level + 1
        return current, True


    def load_ports(self) -> int:
        """포트 XML 을 읽는다. 캐시가 맞으면 캐시로"""
        self._port_map = {}
        self._port_offsets = {}
        self._port_addr = {}
        self._port_addr_of = {}
        self._addr_cad = {}
        self._addr_next = {}
        if not self._xml_path:
            return 0
        if self._load_cache():
            return len(self._port_map)

        found = self._scan_xml()
        if found is None:
            return 0
        self._collect_ports(found)
        self._save_cache()
        return len(self._port_map)

    def _scan_xml(self) -> "dict | None":
        """XML 을 파싱해 포트·주소·CAD 좌표를 거둔다"""
        scan = _PortScan()
        try:
            with self._stage_timer("XML: parse"):
                reads = self._feed_parser(scan)
        except Exception as e:
            self._note(f"xml parse failed: {e}")
            return None
        self._note(f"xml parsed, {reads} reads of {READ_BLOCK >> 20} MB")
        self._addr_cad = scan.addr_cad
        self._addr_next = scan.addr_next
        return scan.found

    def _feed_parser(self, scan: "_PortScan") -> int:
        """파일을 조각으로 읽어 파서에 흘린다"""
        parser = expat.ParserCreate()
        parser.buffer_text = True
        parser.StartElementHandler = scan.start
        parser.EndElementHandler = scan.end
        parser.CharacterDataHandler = scan.data

        reads = 0
        if _remote(self._xml_path):
            whole = _read_bytes(self._xml_path)
            for at in range(0, len(whole) or 1, READ_BLOCK):
                parser.Parse(whole[at:at + READ_BLOCK], False)
                reads += 1
        else:
            with open(self._xml_path, "rb") as handle:
                while True:
                    block = handle.read(READ_BLOCK)
                    reads += 1
                    if not block:
                        break
                    parser.Parse(block, False)
        parser.Parse(b"", True)
        return reads

    def _collect_ports(self, found: dict) -> None:
        """거둔 것을 장비별 포트 표로 정리한다"""
        spanning, gapped = [], []
        for key, by_index in found.items():
            indices = sorted(by_index)
            self._port_map[key] = indices
            self._port_offsets[key] = {i: by_index[i][0] for i in indices}
            by_port = {i: by_index[i][1] for i in indices
                       if by_index[i][1] is not None}
            self._port_addr_of[key] = by_port
            self._port_addr[key] = by_port[max(by_port)] if by_port else None
            if len(set(by_port.values())) > 1:
                spanning.append(key)
            if indices != list(range(1, len(indices) + 1)):
                gapped.append(key)
        for what, names in (("span several addr blocks", spanning),
                            ("have port indices that are not 1..N", gapped)):
            if names:
                self._note(f"{len(names)} equipment {what}: "
                           + ", ".join(sorted(names)[:6])
                           + (" ..." if len(names) > 6 else ""))


    def _cache_path(self) -> str:
        """그 XML 옆에 둘 캐시 파일 경로"""
        return self._xml_path + CACHE_SUFFIX

    def _source_stamp(self) -> list:
        """지금 XML 의 표식. 캐시와 견줄 값"""
        return _stamp_of(self._xml_path)

    def _load_cache(self) -> bool:
        """캐시를 읽는다. 판본이나 표식이 다르면 버린다"""
        path = self._cache_path()
        try:
            want = self._source_stamp()
        except OSError as e:
            self._note(f"xml not readable: {e}")
            return False
        with self._stage_timer("XML: cache read"):
            try:
                blob = json.loads(_read_bytes(path).decode("utf-8"))
                if blob.get("stamp") != want:
                    self._note(f"xml cache stale, parsing again "
                               f"(cache {blob.get('stamp')}, source {want})")
                    return False
                port_map = {k: [int(i) for i in v]
                            for k, v in blob["port_map"].items()}
                offsets = {k: {int(i): v for i, v in d.items()}
                           for k, d in blob["port_offsets"].items()}
                addr_of = {k: {int(i): v for i, v in d.items()}
                           for k, d in blob["port_addr_of"].items()}
                addr = dict(blob["port_addr"])
                cad = {int(k): tuple(v) for k, v in blob["addr_cad"].items()}
                nxt = {int(k): [(int(t), float(pu)) for t, pu in v]
                       for k, v in blob["addr_next"].items()}
            except Exception as e:
                self._note(f"xml cache unusable, parsing again: {e}")
                return False
        self._port_map, self._port_offsets = port_map, offsets
        self._port_addr, self._port_addr_of = addr, addr_of
        self._addr_cad, self._addr_next = cad, nxt
        self._note(f"xml cache hit: {path}")
        return True

    def _save_cache(self) -> None:
        """포트 표를 캐시로 남긴다"""
        path = self._cache_path()
        with self._stage_timer("XML: cache write"):
            try:
                blob = {"stamp": self._source_stamp(),
                        "port_map": self._port_map,
                        "port_offsets": self._port_offsets,
                        "port_addr": self._port_addr,
                        "port_addr_of": self._port_addr_of,
                        "addr_cad": self._addr_cad,
                        "addr_next": self._addr_next}
                text = json.dumps(blob)
                _write_text(path, text)
            except Exception as e:
                self._note(f"xml cache not written: {e}")
                return
        self._note(f"xml cache written: {path} "
                   f"({len(text.encode('utf-8')) / 1048576:.2f} MB)")

    def get_port_count(self, eqp_id: str) -> "int | None":
        """그 장비의 포트 수"""
        indices = self.get_port_indices(eqp_id)
        return len(indices) if indices else None

    def get_port_indices(self, eqp_id: str) -> list:
        """그 장비의 포트 번호들"""
        return list(self._port_map.get(eqp_id.upper(), []))


    def find_rail(self, stage: Usd.Stage, addr_number: int, prefer=()):
        """그 장비가 붙는 레일. 직선인지 코너인지도 함께"""
        prefix = f"{RAIL_PREFIX}{addr_number}_"
        found = self._rails_from(stage, addr_number)
        if not found:
            return None, None, None

        straight = []
        for prim, neighbour in found:
            axis = self._rail_axis(addr_number, neighbour)
            if axis is None:
                self._note(f"  skipping {prim.GetName()}: not a straight rail "
                      f"along one cad axis")
                continue
            straight.append((prim, neighbour, axis))
        if not straight:
            self._note(f"{prefix}*: no straight rail among "
                  f"{[p.GetName() for p, _ in found]}")
            return None, None, None

        if len(straight) > 1 and prefer:
            for prim, neighbour, axis in straight:
                if neighbour in prefer:
                    self._note(f"  {prim.GetName()} chosen: it ends at addr "
                          f"{neighbour}, where a port sits")
                    return prim, neighbour, axis
            base_cad = self._addr_cad.get(addr_number)
            for prim, neighbour, axis in straight:
                if any(abs(self._addr_cad[a][axis] - base_cad[axis]) > 1e-6
                       for a in prefer if a in self._addr_cad):
                    self._note(f"  {prim.GetName()} chosen: it runs along the axis "
                          f"the spilled ports differ on")
                    return prim, neighbour, axis

        if len(straight) > 1:
            self._note(f"{prefix}*: several straight rails "
                  f"{[(p.GetName(), n) for p, n, _ in straight]}, taking the first")
        return straight[0]

    def _rails_from(self, stage: Usd.Stage, addr_number: int) -> list:
        """rail_<a>_<b> 프림을 훑어 구간 색인을 만든다"""
        if self._rail_index is None:
            self._rail_index = {}
            root = stage.GetPrimAtPath(self._rail_root) if self._rail_root else None
            if root is not None and root.IsValid():
                source = _children(root)
            else:
                if self._rail_root:
                    self._note(f"no rails under {self._rail_root}, scanning the stage")
                source = (p for p, _ in self._walk(stage))
            for prim in source:
                parts = prim.GetName().lower().split("_")
                if len(parts) < 3 or parts[0] != RAIL_PREFIX[:-1]:
                    continue
                if not (parts[1].isdigit() and parts[2].isdigit()):
                    continue
                self._rail_index.setdefault(int(parts[1]), []).append(
                    (prim, int(parts[2])))
            self._note(f"indexed rails leaving {len(self._rail_index)} addrs")
        return self._rail_index.get(addr_number, [])

    def _rail_axis(self, addr_a: int, addr_b: int) -> "int | None":
        """그 레일이 X 로 뻗나 Y 로 뻗나"""
        cad_a, cad_b = self._addr_cad.get(addr_a), self._addr_cad.get(addr_b)
        if cad_a is None or cad_b is None:
            return None
        span = (cad_b[0] - cad_a[0], cad_b[1] - cad_a[1])
        moves = [i for i in (0, 1) if abs(span[i]) > CAD_SLACK]
        if len(moves) != 1:
            return None
        held = 1 - moves[0]
        if abs(span[held]) > 1e-6:
            self._note(f"  addr {addr_a} -> {addr_b} wanders {span[held]:+.4f} cad "
                  f"on {'XY'[held]}, within {CAD_SLACK:g}: held at addr {addr_a}")
        return moves[0]

    def _addr_step(self, addr: int, axis: int):
        """그 주소 구간의 길이와 puls. offset 환산에 쓴다"""
        cad = self._addr_cad.get(addr)
        if cad is None:
            return None
        for target, puls in self._addr_next.get(addr, []):
            if not puls or puls <= 0 or self._rail_axis(addr, target) != axis:
                continue
            length = (self._addr_cad[target][axis] - cad[axis]) / CAD_PER_UNIT
            if abs(length) > 1e-9:
                return abs(length), puls
        return None

    def compute_port_points(self, stage: Usd.Stage, eqp_id: str):
        """포트 번호마다 월드 좌표를 만든다"""
        key = eqp_id.upper()
        self._why = ""
        addr_a = self._port_addr.get(key)
        if addr_a is None:
            return self._fail(key, "no addr block found for its ports",
                              "XML에 포트 없음")

        spilled = {a for i, a in self._port_addr_of.get(key, {}).items()
                   if a != addr_a}
        rail, addr_b, axis = self.find_rail(stage, addr_a, prefer=spilled)
        if rail is None:
            return self._fail(key, f"no straight {RAIL_PREFIX}<addr>_* rail "
                              f"leaving addr {addr_a}",
                              f"직선 레일 없음 (addr {addr_a})")

        cad_a, cad_b = self._addr_cad[addr_a], self._addr_cad[addr_b]
        span = (cad_b[0] - cad_a[0], cad_b[1] - cad_a[1])

        length = span[axis] / CAD_PER_UNIT
        direction = 1.0 if length >= 0 else -1.0

        rail_local = self._local_translation(rail)
        start = rail_local[axis] - length / 2.0
        origin = [rail_local[0], rail_local[1], rail_local[2]]
        origin[axis] = start
        onward = list(origin)
        onward[axis] = start + length
        self._rail_frame = (Gf.Vec3d(*origin), Gf.Vec3d(*onward), axis)

        name = "XY"[axis]
        self._note(f"{key}: addr {addr_a} -> rail {rail.GetName()} (neighbour {addr_b})")
        self._note(f"  cad {cad_a} -> {cad_b}, span ({span[0]:+.3f}, {span[1]:+.3f})"
              f" -> runs along {name}, direction {direction:+.0f}")
        self._note(f"  length {span[axis]:+.3f} / {CAD_PER_UNIT:.4f} = {length:+.4f} units")
        self._note(f"  rail.{name.lower()} {rail_local[axis]:.4f} - {length:+.4f}/2 "
              f"= start {start:.4f}")
        self._note(f"  offset scale: {self._offset_scale}")

        if self._offset_scale in (SCALE_PULS, SCALE_SNAP):
            along = self._coords_by_puls(key, axis, direction, start, addr_a)
        else:
            along = self._coords_by_offset(key, axis, direction, start, addr_a)
        if along is None:
            return None

        self._note(f"  a constant shift would match: half a rail "
              f"{abs(length) / 2:.4f}, a whole rail {abs(length):.4f}"
              f"  (port 1 is {abs(along[1] - start):.4f} from the base addr)")

        points = {}
        for index, coord in along.items():
            coords = [rail_local[0], rail_local[1], rail_local[2]]
            coords[axis] = coord
            points[index] = Gf.Vec3d(*coords)
        self._note(f"  {name.lower()} = " +
              ", ".join(f"{i}:{along[i]:.4f}" for i in sorted(along)) +
              " (rail's other axes kept)")
        return points, axis, rail

    def _coords_by_offset(self, key: str, axis: int, direction: float,
                          start: float, addr_a: int) -> "dict | None":
        """offset 을 OFFSET_PER_UNIT 로 나눠 거리로"""
        offsets = self._rebase_offsets(key, addr_a, axis, direction)
        spacing = self._port_spacing(key, offsets)
        if spacing is None:
            return None
        offset_zero = offsets[1] + spacing

        gaps = [f"{offsets[i] - offsets[i + 1]:.1f}"
                for i in sorted(offsets) if i + 1 in offsets]
        self._note(f"  offsets " +
              ", ".join(f"{i}:{offsets[i]:.1f}" for i in sorted(offsets)) +
              f" | gaps [{', '.join(gaps)}] -> spacing {spacing:.1f}")
        self._note(f"  offset0 = {offsets[1]:.1f} + {spacing:.1f} = {offset_zero:.1f}"
              f" / {OFFSET_PER_UNIT:.0f} = {offset_zero / OFFSET_PER_UNIT:.4f} units")

        all_offsets = dict(offsets)
        all_offsets[0] = offset_zero
        return {index: start + direction * offset / OFFSET_PER_UNIT
                for index, offset in all_offsets.items()}

    def _coords_by_puls(self, key: str, axis: int, direction: float,
                        start: float, addr_a: int) -> "dict | None":
        """구간 길이와 distance-puls 로 거리로"""
        offsets = self._port_offsets.get(key, {})
        addr_of = self._port_addr_of.get(key, {})
        base_cad = self._addr_cad.get(addr_a)
        if 1 not in offsets or base_cad is None:
            return self._fail(key, "no offset for port 1",
                              f"포트 1 offset 없음 (있는 포트 {sorted(offsets)})")

        along = {}
        for index in sorted(offsets):
            offset = offsets[index]
            addr = addr_of.get(index, addr_a)
            cad = self._addr_cad.get(addr)
            if offset is None or cad is None:
                return self._fail(key, "a port has no offset, or its addr no cad",
                                  f"포트 {index} offset 또는 addr {addr} cad 없음")
            step = self._addr_step(addr, axis)
            if step is None:
                self._blocked = (f"{key}: addr {addr} has no straight {PULS_KEY} "
                                 f"run for port {index}")
                self._note(f"{self._blocked}")
                self._why = f"직선 {PULS_KEY} 구간 없음 (addr {addr})"
                return None
            seg_length, puls = step
            addr_start = start + (cad[axis] - base_cad[axis]) / CAD_PER_UNIT
            walk = offset * seg_length / puls
            along[index] = addr_start + direction * walk
            self._note(f"  port {index} @ addr {addr}: {offset:.1f} x "
                  f"{seg_length:.4f}/{puls:.0f} = {walk:+.4f} units "
                  f"from {addr_start:.4f}")

        steps = [along[i] - along[i + 1] for i in sorted(along) if i + 1 in along]
        if not steps:
            return self._fail(key, "fewer than two ports, nothing to step by",
                              f"포트 {len(along)}개, 최소 2개 필요")
        pitch = sum(steps) / len(steps)
        along[0] = along[1] + pitch
        self._note(f"  pitch " + ", ".join(f"{s:.4f}" for s in steps) +
              f" -> {pitch:.4f} units, port 0 at {along[0]:.4f}")

        return along

    def _rebase_offsets(self, key: str, base_addr: int, axis: int,
                        direction: float) -> dict:
        """포트 offset 을 그 장비 기준으로 다시 잡는다"""
        offsets = dict(self._port_offsets.get(key, {}))
        addr_of = self._port_addr_of.get(key, {})
        base_cad = self._addr_cad.get(base_addr)
        if base_cad is None:
            return offsets

        for index, offset in list(offsets.items()):
            addr = addr_of.get(index)
            if addr is None or addr == base_addr:
                continue
            cad = self._addr_cad.get(addr)
            if cad is None:
                self._note(f"{key}: port {index} sits in addr {addr}, which has no cad")
                continue
            gap = (cad[axis] - base_cad[axis]) / CAD_PER_UNIT
            shift = direction * gap * OFFSET_PER_UNIT
            offsets[index] = offset + shift
            self._note(f"  port {index} is in addr {addr}, not {base_addr}: "
                  f"{offset:.1f} {shift:+.1f} = {offsets[index]:.1f} "
                  f"(addr gap {gap:+.4f} units)")
        return offsets

    def _port_spacing(self, key: str, offsets: dict) -> "float | None":
        """포트 사이 간격. 간격이 안 맞으면 알린다"""
        gaps = [offsets[i] - offsets[i + 1]
                for i in sorted(offsets) if i + 1 in offsets]
        if 1 not in offsets or not gaps:
            self._note(f"{key}: no offsets for ports 1 and 2, got {offsets}")
            self._why = f"포트 1·2 offset 없음 (있는 포트 {sorted(offsets)})"
            return None

        spacing = sum(gaps) / len(gaps)
        if spacing <= 0:
            self._note(f"{key}: ports should get closer to the addr as the number "
                  f"rises, got {offsets}")
        if len(gaps) > 1 and max(gaps) - min(gaps) > 1e-6:
            self._note(f"{key}: port spacing is uneven {gaps}, using {spacing}")
        return spacing

    def compute_target(self, stage: Usd.Stage, eqp_id: str, anchor: Usd.Prim):
        """EBS 를 놓을 목표점. snap 보정까지"""
        self._port_world = {}
        found = self.compute_port_points(stage, eqp_id)
        if found is None:
            return None
        points, axis, rail = found

        to_world = self._parent_world(rail)
        anchor_world = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()).ExtractTranslation()
        spots = {index: to_world.Transform(spot) for index, spot in points.items()}
        slide = self._snap_shift(to_world, axis, rail, spots, anchor_world, eqp_id)

        for index, spot in spots.items():
            self._port_rail_z = spot[2]
            self._port_world[index] = Gf.Vec3d(spot[0] + slide[0], spot[1] + slide[1],
                                               anchor_world[2])

        in_rail_space = points[0]
        world = spots[0]
        target = self._port_world[0]
        self._note(f"  rail space ({in_rail_space[0]:.4f}, {in_rail_space[1]:.4f}, "
              f"{in_rail_space[2]:.4f}) -> world ({world[0]:.4f}, {world[1]:.4f}, "
              f"{world[2]:.4f})")
        for index in sorted(self._port_world):
            spot = self._port_world[index]
            self._note(f"port {index} world ({spot[0]:.4f}, {spot[1]:.4f}, "
                       f"{spot[2]:.4f})")
        self._note(f"  target = ({target[0]:.4f}, {target[1]:.4f}, {target[2]:.4f})"
              f"  [rail xy, anchor z from {anchor.GetName()}]")
        return target

    def _snap_shift(self, to_world, axis: int, rail, spots: dict, here,
                    eqp_id: str) -> tuple:
        """포트 1 을 피봇에 얹도록 밀어 주는 몫"""
        if self._offset_scale != SCALE_SNAP:
            return (0.0, 0.0)
        if 1 not in spots or self._rail_frame is None:
            self._note("no snap: port 1 has no place to be")
            return (0.0, 0.0)

        row = self._measure(to_world, axis, rail, spots[1], here,
                            self._port_addr.get(eqp_id.upper()))
        state = self._pivot_state(row, here, eqp_id)
        if state != "TRUE":
            self._note(f"no snap: pivot reads {state}")
            return (0.0, 0.0)

        along, gap = row["_along"], row["coord_diff"]
        self._note(f"snapped every port {-gap:+.4f} along the rail, "
                   f"port 1 onto the pivot")
        return (-along[0] * gap, -along[1] * gap)

    @staticmethod
    def _local_translation(prim: Usd.Prim) -> Gf.Vec3d:
        """그 프림이 부모 안에서 놓인 자리"""
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return Gf.Vec3d(0.0, 0.0, 0.0)
        return xformable.GetLocalTransformation(
            Usd.TimeCode.Default()).ExtractTranslation()

    @staticmethod
    def _parent_world(prim: Usd.Prim) -> Gf.Matrix4d:
        """부모까지의 월드 행렬. 없으면 단위행렬"""
        parent = prim.GetParent() if prim else None
        if parent and parent.IsValid() and UsdGeom.Xformable(parent):
            return UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default())
        return Gf.Matrix4d(1.0)


    def _place_ebs(self, ebs_prim: Usd.Prim, world_position: Gf.Vec3d,
                   anchor: Usd.Prim) -> bool:
        """EBS 를 그 월드 좌표에 놓는다. 회전은 피봇에서 가져온다"""
        stage = self._get_stage()
        xformable = UsdGeom.Xformable(ebs_prim)
        if stage is None or not xformable:
            return False

        tc = Usd.TimeCode.Default()
        to_ebs_space = self._parent_world(ebs_prim).GetInverse()
        anchor_world = UsdGeom.Xformable(anchor).ComputeLocalToWorldTransform(tc)
        rotation = self._normalized_rows(anchor_world * to_ebs_space)
        scale = self._extract_scale(xformable.GetLocalTransformation(tc))
        local = to_ebs_space.Transform(world_position)
        self._note(f"  EBS local translate ({local[0]:.4f}, {local[1]:.4f}, "
              f"{local[2]:.4f}), rotation from {anchor.GetName()}")
        return self._write_transform(stage, xformable, rotation, scale, local)

    def _align_prims(self, ebs_prim: Usd.Prim, anchor_prim: Usd.Prim) -> bool:
        """포트를 못 쓸 때. EBS 를 피봇 프림에 통째로 맞춘다"""
        stage = self._get_stage()
        if stage is None or not anchor_prim.IsValid():
            return False
        xformable = UsdGeom.Xformable(ebs_prim)
        if not xformable:
            return False

        tc = Usd.TimeCode.Default()
        anchor_world = UsdGeom.Xformable(anchor_prim).ComputeLocalToWorldTransform(tc)

        target_local = anchor_world * self._parent_world(ebs_prim).GetInverse()

        rotation = self._normalized_rows(target_local)
        scale = self._extract_scale(xformable.GetLocalTransformation(tc))
        return self._write_transform(stage, xformable, rotation, scale,
                                     self._pushed_local(ebs_prim, anchor_prim,
                                                        target_local))

    def _pushed_local(self, ebs_prim, anchor_prim, target_local):
        """앵커에 맞출 때의 자리. 민 거리는 EBS 부모 공간으로 바꿔서 더한다"""
        spot = target_local.ExtractTranslation()
        if not self._nudge:
            return spot
        right, _, _ = self._camera.axes(self._get_stage(), anchor_prim)
        paces = self._nudge / (self._per_unit() or 1.0)
        shift = self._parent_world(ebs_prim).GetInverse().TransformDir(
            Gf.Vec3d(*[right[i] * paces for i in range(3)]))
        self._note(f"nudged {self._nudge:+.3f} along the EBS right axis")
        return Gf.Vec3d(*[spot[i] + shift[i] for i in range(3)])

    def _write_transform(self, stage, xformable, rotation, scale, translation) -> bool:
        """쓸 수 있는 xformOp 를 찾아 회전·크기·이동을 쓴다"""
        ops = {op.GetOpName(): op for op in xformable.GetOrderedXformOps()}

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if "xformOp:transform" in ops:
                ops["xformOp:transform"].Set(
                    self._compose(rotation, scale, translation))
                return True

            if "xformOp:translate" in ops and self._set_rotation(ops, rotation):
                ops["xformOp:translate"].Set(Gf.Vec3d(translation))
                return True

            self._note("no usable xform ops, authoring a single transform op")
            try:
                xformable.ClearXformOpOrder()
                xformable.AddTransformOp().Set(
                    self._compose(rotation, scale, translation))
                return True
            except Exception as e:
                self._note(f"transform op failed, translate only: {e}")
                api = UsdGeom.XformCommonAPI(xformable.GetPrim())
                return bool(api and api.SetTranslate(Gf.Vec3d(translation)))

    def _set_rotation(self, ops: dict, rotation) -> bool:
        """orient 나 rotateXYZ 중 있는 것에 회전을 쓴다"""
        matrix = self._compose(rotation, Gf.Vec3d(1.0, 1.0, 1.0),
                               Gf.Vec3d(0.0, 0.0, 0.0))
        orient = ops.get("xformOp:orient")
        if orient is not None:
            quat = matrix.ExtractRotationQuat()
            precision = orient.GetPrecision()
            if precision == UsdGeom.XformOp.PrecisionFloat:
                quat = Gf.Quatf(quat)
            elif precision == UsdGeom.XformOp.PrecisionHalf:
                quat = Gf.Quath(Gf.Quatf(quat))
            orient.Set(quat)
            return True

        for order in ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"):
            op = ops.get(f"xformOp:rotate{order}")
            if op is not None:
                angles = self._euler(rotation, order)
                op.Set(Gf.Vec3f(*angles) if op.GetPrecision() ==
                       UsdGeom.XformOp.PrecisionFloat else Gf.Vec3d(*angles))
                return True
        return False

    @staticmethod
    def _compose(rotation, scale, translation) -> Gf.Matrix4d:
        """회전·크기·이동을 4x4 한 장으로"""
        return Gf.Matrix4d(
            rotation[0][0] * scale[0], rotation[0][1] * scale[0], rotation[0][2] * scale[0], 0.0,
            rotation[1][0] * scale[1], rotation[1][1] * scale[1], rotation[1][2] * scale[1], 0.0,
            rotation[2][0] * scale[2], rotation[2][1] * scale[2], rotation[2][2] * scale[2], 0.0,
            translation[0], translation[1], translation[2], 1.0,
        )

    @staticmethod
    def _euler(rotation, order: str = "XYZ") -> tuple:
        """회전 행렬을 그 순서의 오일러 각으로"""
        axes = {"X": 0, "Y": 1, "Z": 2}
        a, b, c = (axes[ch] for ch in order)
        m = [[rotation[i][j] for j in range(3)] for i in range(3)]

        p = [a, b, c]
        sign = 1.0 if order in ("XYZ", "YZX", "ZXY") else -1.0
        r = [[m[p[i]][p[j]] for j in range(3)] for i in range(3)]

        beta = math.asin(max(-1.0, min(1.0, -sign * r[0][2])))
        if abs(math.cos(beta)) < 1e-9:
            alpha = 0.0
            gamma = math.atan2(-sign * r[1][0], r[1][1])
        else:
            alpha = math.atan2(sign * r[1][2], r[2][2])
            gamma = math.atan2(sign * r[0][1], r[0][0])

        values = [0.0, 0.0, 0.0]
        values[a], values[b], values[c] = (math.degrees(alpha), math.degrees(beta),
                                           math.degrees(gamma))
        return tuple(values)

    @staticmethod
    def _normalized_rows(matrix: Gf.Matrix4d) -> list:
        """행렬에서 크기를 뺀 회전 세 축"""
        rows = matrix.ExtractRotationMatrix()
        return [Gf.Vec3d(rows[i][0], rows[i][1], rows[i][2]).GetNormalized()
                for i in range(3)]

    @staticmethod
    def _extract_scale(matrix: Gf.Matrix4d) -> Gf.Vec3d:
        """행렬의 축 길이 = 크기. 0 이면 1 로 본다"""
        rows = matrix.ExtractRotationMatrix()
        scale = [Gf.Vec3d(rows[i][0], rows[i][1], rows[i][2]).GetLength() for i in range(3)]
        return Gf.Vec3d(*[v if v > 1e-12 else 1.0 for v in scale])


    def _is_visible(self, prim, path: str) -> bool:
        """그 프림이 화면에 보이나. collide 마다 다시 푼다"""
        known = self._visible.get(path)
        if known is not None:
            return known
        visible = True
        imageable = UsdGeom.Imageable(prim)
        if imageable:
            attribute = imageable.GetVisibilityAttr()
            if attribute:
                visible = attribute.Get(NOW) != UsdGeom.Tokens.invisible
        self._visible[path] = visible
        return visible

    @staticmethod
    def _path_of(prim) -> str:
        """프림이든 문자열이든 경로 문자열로"""
        try:
            return str(prim.GetPath()) if prim.IsValid() else ""
        except AttributeError:
            return ""

    def show_markers(self, ebs_prim: Usd.Prim, cells: dict,
                     marks: list = None, marks_boxes: list = None,
                     fresh: bool = True) -> int:
        """3면 판, 여유 선, 내부 충돌 상자를 오버레이에게 그리게 한다

        fresh  False 면 지우지 않고 있던 프림을 고쳐 그린다. 미는 동안 쓴다
        """
        bbox = Collide._ebs_bound(self, ebs_prim)
        local_box, to_world = bbox.GetRange(), bbox.GetMatrix()
        if local_box.IsEmpty():
            return 0
        return self._marks().draw(
            self._face_sheets(local_box, to_world, cells, marks),
            marks, marks_boxes, fresh)

    def _face_sheets(self, local_box, to_world, cells: dict,
                     marks: list) -> list:
        """면 판마다 (이름, 월드 네 점, 막혔나)"""
        built = Collide._build_cells(self, local_box)
        tight = {mark["face"] for mark in marks or ()
                 if mark.get("state") == STATE_TIGHT}
        sheets = []
        for face, boxes in built.items():
            flags = ([True] * len(boxes) if face in tight
                     else cells.get(face, []))
            for i, (_, quad) in enumerate(boxes):
                points = [tuple(to_world.Transform(Gf.Vec3d(*corner)))
                          for corner in quad]
                sheets.append((f"{face}_{i}", points,
                               bool(i < len(flags) and flags[i])))
        return sheets

    def _marks(self):
        """씬에 그리는 쪽. 순환 임포트를 피하려 여기서 늦게 들인다"""
        if self._marker_draw is None:
            from .ebs_simulate_overlay import EbsSimulateMarks
            self._marker_draw = EbsSimulateMarks(
                lambda: self._get_stage(), MARKER_ROOT)
        return self._marker_draw

    def _thread_radius(self) -> float:
        """선 굵기. 대상 장비 대각선 대비 LASER_RADIUS"""
        box = self._world_range((self._target or {}).get("equipment"))
        if box is None:
            span = 1.0
        else:
            lo, hi = box.GetMin(), box.GetMax()
            span = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
        return max(span * LASER_RADIUS, 1e-5)

    def show_port_lasers(self, points: dict = None) -> int:
        """포트 자리에 확인용 세로 레이저를 세운다"""
        stage = self._get_stage()
        if stage is None:
            return 0
        self.clear_port_lasers()

        points = self._port_world if points is None else points
        if not points:
            return 0

        top = self._port_rail_z
        radius = self._thread_radius()

        drawn = 0
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, LASER_ROOT)
            for index in sorted(points):
                colour = LASER_COLOR_0 if index == 0 else LASER_COLOR
                spot = points[index]
                bottom = spot[2]
                height = max(abs(top - bottom), 1e-3)
                self._laser_cylinder(stage, f"{LASER_ROOT}/port_{index}",
                                     Gf.Vec3d(spot[0], spot[1], (top + bottom) / 2.0),
                                     radius, height, colour)
                drawn += 1
        self._note(f"drew {drawn} port lasers under {LASER_ROOT}, "
              f"radius {radius:.4f}, rail z {top:.4f} down to the EBS z")
        return drawn

    def show_sweep(self, spots: dict) -> int:
        """sweep_ports 가 잰 자리를 씬에 표시한다 (진단용)"""
        stage = self._get_stage()
        if stage is None:
            return 0
        self.clear_sweep()
        if not spots:
            return 0

        radius = 0.0
        for name in spots:
            box = self._world_range(stage.GetPrimAtPath(self._eqp_index.get(
                EQP_PREFIX + name, "")))
            if box is not None:
                lo, hi = box.GetMin(), box.GetMax()
                radius = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
                radius *= LASER_RADIUS
                break
        radius = max(radius, 1e-5)

        drawn, refused = 0, []
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, SWEEP_ROOT)
            for name, (port, here) in spots.items():
                top, bottom = port[2], here[2]
                height = max(abs(top - bottom), 1e-3)
                middle = (top + bottom) / 2.0
                stem = self._prim_name(name)
                try:
                    self._laser_cylinder(stage, f"{SWEEP_ROOT}/{stem}_port",
                                         Gf.Vec3d(port[0], port[1], middle),
                                         radius, height, SWEEP_COLOR_PORT)
                    self._laser_cylinder(stage, f"{SWEEP_ROOT}/{stem}_eqp",
                                         Gf.Vec3d(here[0], here[1], middle),
                                         radius, height, SWEEP_COLOR_EQP)
                except Exception as e:
                    refused.append(f"{name}: {e}")
                    continue
                drawn += 1
        self._note(f"drew {drawn} pairs under {SWEEP_ROOT}, radius {radius:.4f}")
        if refused:
            self._note(f"{len(refused)} could not be drawn: "
                       + ", ".join(refused[:4]))
        return drawn

    @staticmethod
    def _prim_name(text: str) -> str:
        """프림 이름에 못 쓰는 글자를 _ 로 바꾼다"""
        cleaned = "".join(c if c.isalnum() or c == "_" else "_" for c in text)
        return cleaned if cleaned[:1].isalpha() or cleaned[:1] == "_" else "_" + cleaned

    def clear_sweep(self) -> None:
        """sweep 이 그린 것을 지운다"""
        stage = self._get_stage()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(SWEEP_ROOT).IsValid():
                stage.RemovePrim(SWEEP_ROOT)

    def clear_port_lasers(self) -> None:
        """포트 레이저를 지운다"""
        stage = self._get_stage()
        if stage is None:
            return
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(LASER_ROOT).IsValid():
                stage.RemovePrim(LASER_ROOT)

    @staticmethod
    def _laser_cylinder(stage, path: str, centre, radius: float, height: float,
                        colour) -> None:
        """포트 레이저와 스윕이 같이 쓰는 실린더"""
        cylinder = UsdGeom.Cylinder.Define(stage, path)
        cylinder.CreateAxisAttr(UsdGeom.Tokens.z)
        cylinder.CreateHeightAttr(height)
        cylinder.CreateRadiusAttr(radius)
        cylinder.CreateExtentAttr(Vt.Vec3fArray([
            Gf.Vec3f(-radius, -radius, -height / 2.0),
            Gf.Vec3f(radius, radius, height / 2.0)]))
        cylinder.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*colour)]))
        cylinder.AddTranslateOp().Set(Gf.Vec3d(centre[0], centre[1], centre[2]))

    def clear_all(self) -> dict:
        """Clear 버튼이 하는 일 전부. 어디서 얼마가 걸렸는지 한 줄로 찍는다

        strip_skin  제일 먼저 푼다. 장비 전체를 다시 그리게 만드는 일이라,
                 뒤에 두면 앞에서 지운 것까지 같은 파동에 얹혀 늦어진다
        """
        self._begin("clear")
        for phase, work in (("skin", self.strip_skin),
                            ("overlay", self.clear_markers),
                            ("overlay", self.clear_port_lasers),
                            ("overlay", self.clear_sweep),
                            ("camera", self.release_camera),
                            ("place", self.hide_ebs)):
            with self._phase(phase):
                try:
                    work()
                except Exception as e:
                    self._note(f"clear failed at {phase}: "
                               f"{type(e).__name__}: {e}")
        return self._done(self._payload(True, "cleared"))

    async def clear_all_async(self) -> dict:
        """clear_all 인데 화면이 잦아들 때까지 기다려 그 시간까지 담는다"""
        told = self.clear_all()
        await self.settle_skin()
        return told

    def clear_markers(self) -> None:
        """그린 것을 오버레이에게 지우게 하고 판정도 놓는다"""
        self._verdict = {}
        self._marks().clear()



    def release_camera(self) -> None:
        """카메라를 놓고 갈아입힌 머티리얼을 걷는다

        EbsSimulateCamera.release
        strip_skin  갈아입힌 머티리얼도 같이 걷는다
        """
        self.strip_skin()
        self._camera.release(self._get_stage())

    def refresh_camera(self) -> dict:
        """카메라를 처음 잡은 자리로

        EbsSimulateCamera.reset  place 가 적어둔 _home 을 다시 쓴다
        """
        told = self._camera.reset(self._get_stage())
        if told:
            self._note(told)
        return self._payload(bool(told), told or "Run Camera first")

    def _world_range(self, prim, cache=None) -> "Gf.Range3d | None":
        """그 프림의 월드 상자"""
        if prim is None or not prim.IsValid():
            return None
        if cache is None:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                includedPurposes=[UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
                useExtentsHint=True,
            )
        box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        return None if box.IsEmpty() else box

    @staticmethod
    def _get_stage() -> "Usd.Stage | None":
        """지금 열린 스테이지"""
        return omni.usd.get_context().get_stage()

    def _payload(self, ok: bool, reason: str, cells: dict = None, hit_count: int = 0,
                 equipment=None, eqp_id: str = "", port_count=None,
                 distances: dict = None, rows: list = None,
                 equipment_hit: dict = None) -> dict:
        """단계 하나의 결과 한 벌. 성공 여부, 사유, 시간, 로그"""
        target = self._target or {}
        equipment = equipment or target.get("equipment")
        ebs = target.get("ebs")
        anchor = target.get("anchor")
        self._result = {
            "ok": ok,
            "reason": reason,
            "equipment": str(equipment.GetPath()) if equipment else "",
            "equipment_id": eqp_id or target.get("eqp_id", ""),
            "port_count": port_count if port_count is not None else target.get("port_count"),
            "ebs": str(ebs.GetPath()) if ebs else "",
            "anchor": str(anchor.GetPath()) if anchor else "",
            "cells": cells,
            "hit_count": hit_count,
            "grid": dict(self._grid_shape),
            "distances": distances or {},
            "rows": rows or [],
            "equipment_hit": equipment_hit or {"hit": False, "pairs": [],
                                              "boxes": [], "tests": 0},
            "timings": list(self._timings),
            "notes": list(self._notes),
            "total_ms": (time.perf_counter() - self._started) * 1000.0,
        }
        return dict(self._result)
