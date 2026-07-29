"""
STEP/CAD -> USD 변환 + 스테이지 로드 + 진행도 추적 구현부 전체.

모든 변환 기능은 CadConverter 의 @classmethod 로 구현되며,
UI 없이 단독 실행도 가능 (Script Editor 에서 CadConverter.launch(...) 호출).

진행도는 CadConverter.progress (CadConverterProgress 인스턴스) 가 보유한다.
UI/외부는 CadConverterService 를 통해 접근.

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


class CadConverterService:
    """단일 진입점 서비스. 변환 + 진행도 접근을 외부에 노출."""

    # ---------------- convert ----------------

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

    # ---------------- progress ----------------

    @classmethod
    def on_progress_changed_fn(cls, callback):
        """진행 상태 갱신마다 callback(step_type, desc, value, step_label) 호출 등록."""
        CadConverter.progress.on_progress_changed_fn(callback)

    @classmethod
    def get_progress(cls):
        """현재 진행 상태 (step_type, desc, value, step_label) 조회."""
        return CadConverter.progress.get_current_state()


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

    # 변환 진행도 - 파일 하단에서 CadConverterProgress 인스턴스로 채워짐
    progress = None

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
            "upAxis"            : up_axis,
            "tessLOD"           : tess_lod,
            "bInstancing"       : "true" if instancing else "false",
            "useMaterials"      : "true" if use_materials else "false",
            "dMetersPerUnit"    : meters_per_unit,
            # 진행 로그(*step*/*prog*) 는 이 옵션에 게이트됨 - 진행도 추적하려면 필수
            "reportProgress"    : "true",
            "reportProgressFreq": "10.0",   # 초당 리포팅 상한 [1~10]
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


# ---------------- 진행 로그 수집 ----------------

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


class _StdoutTap:
    """C 레벨 stdout(fd 1)을 파이프로 복제해 라인 콜백으로 전달.

    HOOPS 네이티브 진행 로그는 fd1 에 직접 쓰므로 이 방법으로만 잡힌다.
    읽은 내용은 원본 콘솔로 그대로 통과시킨다.
    """

    DRAIN_TIMEOUT = 5.0   # stop() 에서 pump 가 남은 데이터를 처리할 때까지 대기(초)
    DEBUG = False         # True 면 파이프로 들어온 라인 통계를 stderr 로 보고

    def __init__(self, on_line):
        self._on_line = on_line
        self._saved_fd = None
        self._read_fd = None
        self._thread = None
        self._shields = []
        self._n_lines = 0        # 파이프로 들어온 전체 라인 수
        self._n_progress = 0     # 그중 hoops_progress 라인 수

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
                break                       # 파이프 write 끝이 닫힘 = 정상 종료
            if self._saved_fd is not None:
                try:
                    os.write(self._saved_fd, chunk)   # 원본 출력으로 그대로 통과
                except OSError:
                    pass
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "ignore")
                self._n_lines += 1
                if "hoops_progress" in text:
                    self._n_progress += 1
                try:
                    self._on_line(text)
                except Exception:
                    pass

    def stop(self):
        # 1) C stdio 버퍼에 갇힌 네이티브 출력을 먼저 파이프로 밀어냄.
        #    (fd1 이 아직 파이프인 동안 해야 우리 쪽으로 들어온다)
        self._flush_c_stdio()
        try:
            sys.stdout.flush()
        except Exception:
            pass

        # 2) fd1 원복 -> 파이프의 write 끝 참조가 사라져 pump 가 EOF 로 끝남
        if self._saved_fd is not None:
            os.dup2(self._saved_fd, 1)

        # 3) pump 가 남은 데이터를 다 처리할 때까지 대기 (여기서 콜백이 실행된다)
        if self._thread is not None:
            self._thread.join(timeout=self.DRAIN_TIMEOUT)
            self._thread = None

        # 4) 배수 완료 후에야 fd 정리 (passthrough 중 close 하면 pump 가 죽는다)
        if self._saved_fd is not None:
            os.close(self._saved_fd)
            self._saved_fd = None
        if self._read_fd is not None:
            try:
                os.close(self._read_fd)
            except OSError:
                pass
            self._read_fd = None

        for stream, orig in self._shields:
            if orig is not None:
                try:
                    stream.write = orig
                except (AttributeError, TypeError):
                    pass
        self._shields = []

        if self.DEBUG:
            print(f"[cad_converter] tap: lines={self._n_lines} "
                  f"hoops_progress={self._n_progress}")

    @staticmethod
    def _flush_c_stdio():
        """네이티브(C) stdout 버퍼를 강제로 비운다.

        헤드리스/서비스 환경에선 fd1 이 파일·파이프라 C 런타임이 full-buffered 로
        동작해, 진행 로그가 버퍼에 갇힌 채 변환이 끝난다. fd1 을 원복하기 전에
        flush 해야 그 내용이 우리 파이프로 들어온다.
        """
        import ctypes
        libs = []
        if os.name == "nt":
            for name in ("ucrtbase", "msvcrt"):
                try:
                    libs.append(ctypes.CDLL(name))
                except OSError:
                    pass
        else:
            try:
                libs.append(ctypes.CDLL(None))
            except OSError:
                pass
        for lib in libs:
            try:
                lib.fflush(None)      # fflush(NULL) = 열려 있는 모든 스트림 flush
            except Exception:
                pass

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


# CadConverter 아래에 정의된 CadConverterProgress 로 progress 채움
CadConverter.progress = CadConverterProgress()


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
