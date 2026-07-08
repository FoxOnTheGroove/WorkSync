"""
STEP/CAD -> USD 변환 + 스테이지 로드 구현부 전체.

모든 기능은 CadConverter 클래스의 @classmethod 로 구현되며,
UI 없이 단독 실행도 가능 (Script Editor 에서 CadConverter.launch(...) 호출).

사전 조건: Extension Manager 에서 omni.kit.converter.hoops_core 활성화
"""

import os
import sys
import threading

import omni.usd
import omni.kit.commands
import omni.kit.async_engine
import omni.kit.converter.hoops_core as hoops_mod
from omni.kit.converter.common import ProgressLogConsumer
from pxr import UsdGeom


# ---------------- 진행 로그 수집 ----------------

class _StdoutTap:
    """C 레벨 stdout(fd 1)을 파이프로 복제해 라인 콜백으로 전달.

    HOOPS 네이티브 진행 로그는 fd1 에 직접 쓰므로 이 방법으로만 잡힌다.
    읽은 내용은 원본 콘솔로 그대로 통과시킨다.
    """

    def __init__(self, on_line):
        self._on_line = on_line
        self._saved_fd = None
        self._read_fd = None
        self._thread = None
        self._shields = []

    def start(self):
        sys.stdout.flush()
        self._saved_fd = os.dup(1)
        r, w = os.pipe()
        os.dup2(w, 1)
        os.close(w)
        self._read_fd = r
        # tap 중 콘솔 스트림 write 의 OSError(WinError 1) 삼키는 보호막
        self._shields = [self._shield(sys.stdout), self._shield(sys.stderr)]
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        buf = b""
        while True:
            try:
                chunk = os.read(self._read_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            os.write(self._saved_fd, chunk)   # 원본 콘솔로 그대로 통과
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    self._on_line(line.decode("utf-8", "ignore"))
                except Exception:
                    pass

    def stop(self):
        if self._saved_fd is not None:
            os.dup2(self._saved_fd, 1)    # fd1 원복 -> pump 종료
            os.close(self._saved_fd)
            self._saved_fd = None
        if self._read_fd is not None:
            try:
                os.close(self._read_fd)
            except OSError:
                pass
            self._read_fd = None
        for stream, orig in self._shields:
            try:
                stream.write = orig
            except (AttributeError, TypeError):
                pass
        self._shields = []

    @staticmethod
    def _shield(stream):
        orig = getattr(stream, "write", None)
        if orig is None:
            return (stream, None)
        def _safe(s, _orig=orig):
            try:
                return _orig(s)
            except OSError:
                return len(s)
        try:
            stream.write = _safe
        except (AttributeError, TypeError):
            return (stream, None)
        return (stream, orig)


class CadConverterProgress:
    """변환 진행 로그를 파싱해 상태를 관리 (변환 1건당 1개).

    extract_line 반환: [ProgressStepType, desc(str), prog(0.0~1.0, PROGRESS 일 때만)]

    접근 방법 두 가지:
      get_current_state()             : (step_type, desc, value, step_label) 반환 (pull)
      on_progress_changed_fn(callback): 갱신마다 callback(step_type, desc, value, step_label) (push)

    주의: 콜백은 tap 백그라운드 스레드에서 호출됨. UI 직접 조작 대신 값 캐싱/
          메인 스레드 폴링(get_current_state) 용도로 쓸 것.
    """

    PREFIX = "[omni.converter.hoops_progress]"

    def __init__(self):
        self._consumer = ProgressLogConsumer(self.PREFIX)
        self._callbacks = []
        self.step_type = None   # ret[0]
        self.desc = ""          # ret[1] - 현재 과정
        self.value = 0.0        # ret[2] - 진행도 0.0 ~ 1.0
        self.step = ""          # 단계 인덱스 (예: "1:2")

    @property
    def step_label(self) -> str:
        """단계 표시 - "1:2" -> "1/2"."""
        return self.step.replace(":", "/")

    def reset(self):
        self.step_type, self.desc, self.value, self.step = None, "", 0.0, ""

    def get_current_state(self):
        return self.step_type, self.desc, self.value, self.step_label

    def on_progress_changed_fn(self, callback):
        """상태 갱신마다 callback(step_type, desc, value, step_label) 호출 등록."""
        self._callbacks.append(callback)

    def feed(self, text: str):
        if "hoops_progress" not in text:
            return
        try:
            # 파서는 프리픽스로 시작하는 라인을 기대함 - 프리픽스 위치부터 자름.
            line = text[text.find(self.PREFIX):]
            parts = [p.strip() for p in line.split("*")]

            if "end" in parts:
                self.step_type, self.desc, self.value = "end", "convert complete", 1.0
            else:
                ret = self._consumer.extract_line(line)
                if not ret:
                    return
                self.step_type = ret[0]
                # step 인덱스가 바뀌면 새 단계 시작 - 진행도 리셋
                if "step" in parts:
                    new_step = parts[parts.index("step") + 1]
                    if new_step != self.step:
                        self.step, self.value = new_step, 0.0
                if len(ret) > 2:                     # PROGRESS - 0.0~1.0
                    self.value = float(ret[2])
                if len(ret) > 1 and ret[1]:
                    self.desc = ret[1]               # 현재 과정

            for cb in self._callbacks:
                cb(self.step_type, self.desc, self.value, self.step_label)
        except Exception:
            pass


class CadConverterService:
    """단일 진입점 서비스. src/dest + 5옵션 + autoload 를 받아 변환(+로드)을 한 번에 수행."""

    @classmethod
    async def convert(
        cls,
        src_path: str,
        dest_path: str,
        up_axis: str = "1",
        tess_lod: str = "2",
        instancing: bool = False,
        use_materials: bool = False,
        meters_per_unit: str = "1.0",
        autoload: bool = True,
    ) -> str:
        """STEP -> USD 변환. autoload=True 면 현재 스테이지에 로드. 로드된 prim 경로 반환."""
        options = CadConverter.build_options(
            up_axis=up_axis,
            tess_lod=tess_lod,
            instancing=instancing,
            use_materials=use_materials,
            meters_per_unit=meters_per_unit,
        )
        await CadConverter.convert_async(src_path, dest_path, options)
        if autoload:
            return CadConverter.load_into_stage(dest_path)
        return ""


class CadConverter:

    # ====== UI 콤보박스용 옵션 정의 (라벨 -> file_format_args 값) ======
    UP_AXIS_CHOICES = {
        "Y-up":    "1",
        "Z-up":    "2",
        "Default": "0",
    }

    TESS_LOD_CHOICES = {
        "ExtraLow":  "0",
        "Low":       "1",
        "Medium":    "2",
        "High":      "3",
        "ExtraHigh": "4",
    }

    # dMetersPerUnit: 스테이지 단위 스케일. 0.0 = 변환 단위 그대로 유지
    METERS_PER_UNIT_CHOICES = {
        "Meter (1.0)":         "1.0",
        "Centimeter (0.01)":   "0.01",
        "Millimeter (0.001)":  "0.001",
        "Keep Original (0.0)": "0.0",
    }

    # 로드로 생성된 prim 경로 추적 (Clear 대상)
    _loaded_prims: list = []

    # 변환 진행도 (UI 등에서 콜백 등록 / 상태 조회)
    progress = CadConverterProgress()

    # ---------------- options ----------------

    @classmethod
    def build_options(
        cls,
        up_axis: str = "1",
        tess_lod: str = "2",
        instancing: bool = False,
        use_materials: bool = False,
        meters_per_unit: str = "1.0",
    ) -> dict:
        """file_format_args 는 dict[str, str] - 값 전부 문자열."""
        return {
            "upAxis"        : up_axis,
            "tessLOD"       : tess_lod,
            "bInstancing"   : "true" if instancing else "false",
            "useMaterials"  : "true" if use_materials else "false",
            "dMetersPerUnit": meters_per_unit,
        }

    # ---------------- convert ----------------

    @classmethod
    async def convert_async(cls, src_path: str, dest_path: str, options: dict):
        """STEP -> USD 변환. 진행 로그를 cls.progress 로 흘려보냄. 완료 후 결과 반환."""
        converter = hoops_mod.get_instance()

        # 진행 로그(fd1)를 progress 에 물려 변환 동안 자동 갱신
        cls.progress.reset()
        tap = _StdoutTap(cls.progress.feed)
        tap.start()
        try:
            result = await converter.create_converter_task(src_path, dest_path, options)
        finally:
            tap.stop()

        print(f"[cad_converter] convert done: {result}")
        return result

    # ---------------- load / clear ----------------

    @classmethod
    def _sanitize(cls, name: str) -> str:
        """USD prim 이름으로 쓸 수 있게 정리 (영숫자/언더스코어만)."""
        s = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
        return ("_" + s) if s and s[0].isdigit() else (s or "Imported")

    @classmethod
    def load_into_stage(cls, usd_path: str, prim_path: str = None) -> str:
        """변환된 USD 를 현재 스테이지에 reference 로 추가.

        prim_path 지정 시 그 경로에, None 이면 /World 바로 아래에 파일명으로 생성.
        충돌 시 _01, _02 로 유니크 처리. 생성된 prim 경로 반환.
        """
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            print("[cad_converter] ERROR: no stage")
            return ""

        if prim_path:
            target = prim_path
        else:
            if not stage.GetPrimAtPath("/World").IsValid():
                UsdGeom.Xform.Define(stage, "/World")
            base = cls._sanitize(os.path.splitext(os.path.basename(usd_path))[0])
            target = f"/World/{base}"

        final_path = target
        i = 1
        while stage.GetPrimAtPath(final_path).IsValid():
            final_path = f"{target}_{i:02d}"
            i += 1

        omni.kit.commands.execute(
            "CreateReference",
            path_to=final_path,
            asset_path=usd_path,
            usd_context=ctx,
        )
        cls._loaded_prims.append(final_path)
        print(f"[cad_converter] loaded {final_path} <- {usd_path}")
        return final_path

    @classmethod
    def clear(cls) -> int:
        """이 컨버터로 로드된 prim 들을 스테이지에서 제거. 제거 개수 반환."""
        valid = [p for p in cls._loaded_prims if p]
        if not valid:
            return 0
        omni.kit.commands.execute("DeletePrims", paths=valid)
        cls._loaded_prims.clear()
        print(f"[cad_converter] removed {valid}")
        return len(valid)

    # ---------------- standalone ----------------

    @classmethod
    async def run(
        cls,
        src_path: str,
        dest_path: str,
        autoload: bool = True,
        **option_kwargs,
    ) -> str:
        """변환 (+ 옵션에 따라 로드) 한 번에 실행. 로드된 prim 경로 반환."""
        options = cls.build_options(**option_kwargs)
        await cls.convert_async(src_path, dest_path, options)
        if autoload:
            return cls.load_into_stage(dest_path)
        return ""

    @classmethod
    def launch(cls, src_path: str, dest_path: str, autoload: bool = True, **option_kwargs):
        """Script Editor 등에서 단독 실행용 - 코루틴을 Kit 루프에 올림."""
        return omni.kit.async_engine.run_coroutine(
            cls.run(src_path, dest_path, autoload, **option_kwargs)
        )


if __name__ == "__main__":
    # 경로를 실제 환경에 맞게 수정 후 Script Editor 에서 실행
    CadConverter.launch(
        r"C:/data/model.stp",
        r"C:/data/out/model.usd",
        autoload=True,
        up_axis="1",
        tess_lod="2",
        instancing=False,
        use_materials=False,
        meters_per_unit="1.0",
    )
