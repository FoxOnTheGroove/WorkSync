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
import json
import tempfile
import threading
import subprocess

import omni.usd
import omni.kit.app
import omni.kit.commands
import omni.kit.async_engine
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

    # Kit 실행파일 / CLI 스크립트 경로 (None 이면 자동 탐색)
    KIT_EXE = None
    HOOPS_MAIN = None

    # file_format_args 키 -> CLI config JSON 키 (문서상 다름)
    _CONFIG_KEY_MAP = {"upAxis": "iUpAxis"}

    @classmethod
    def _find_kit_exe(cls) -> str:
        if cls.KIT_EXE:
            return cls.KIT_EXE
        exe = "kit.exe" if os.name == "nt" else "kit"
        try:
            import carb.tokens
            kit_dir = carb.tokens.get_tokens_interface().resolve("${kit}")
            cand = os.path.join(kit_dir, exe)
            if os.path.isfile(cand):
                return cand
        except Exception:
            pass
        if os.path.basename(sys.argv[0]).lower().startswith("kit"):
            return sys.argv[0]
        raise FileNotFoundError(
            "kit 실행파일을 찾지 못함 - CadConverter.KIT_EXE 를 직접 지정하세요")

    @classmethod
    def _find_hoops_main(cls) -> str:
        if cls.HOOPS_MAIN:
            return cls.HOOPS_MAIN
        mgr = omni.kit.app.get_app().get_extension_manager()
        info = mgr.get_extension_dict("omni.services.convert.cad")
        if info:
            cand = os.path.join(
                info["path"], "omni", "services", "convert", "cad",
                "services", "process", "hoops_main.py")
            if os.path.isfile(cand):
                return cand
        raise FileNotFoundError(
            "hoops_main.py 를 찾지 못함 (omni.services.convert.cad 활성화 필요) "
            "- CadConverter.HOOPS_MAIN 을 직접 지정하세요")

    @classmethod
    def _to_config(cls, options: dict) -> dict:
        """file_format_args(전부 문자열) -> CLI config JSON(타입 있는 값)."""
        def cast(v):
            s = str(v)
            low = s.lower()
            if low in ("true", "false"):
                return low == "true"
            try:
                return float(s) if "." in s else int(s)
            except ValueError:
                return s
        return {cls._CONFIG_KEY_MAP.get(k, k): cast(v) for k, v in options.items()}

    @classmethod
    async def convert_async(cls, src_path: str, dest_path: str, options: dict):
        """STEP -> USD 변환. 완료 후 결과 반환. (호출 형태는 기존과 동일)

        자식 Kit 프로세스로 변환을 돌리고 그 stdout 을 실시간으로 읽어
        cls.progress 에 흘려보낸다. in-process 호출은 네이티브가 GIL 을 쥔 채
        블로킹해서 UI 가 멈추고 진행 로그도 사후에 몰려 나오므로 사용하지 않는다.
        """
        cls.progress.reset()

        kit_exe = cls._find_kit_exe()
        hoops_main = cls._find_hoops_main()

        # 변환 옵션을 config JSON 으로 기록 (CLI 는 파일 경로로 받음)
        fd, cfg_path = tempfile.mkstemp(prefix="cad_cfg_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(cls._to_config(options), f)

        exec_arg = (
            f'{hoops_main} '
            f'--input-path "{src_path}" '
            f'--output-path "{dest_path}" '
            f'--config-path "{cfg_path}"'
        )
        argv = [
            kit_exe,
            "--allow-root",
            "--enable", "omni.kit.converter.hoops_core",
            "--exec",
            "--/app/fastShutdown=1",
            exec_arg,
            "--info",
        ]

        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            errors="ignore",
            **popen_kwargs,
        )

        # 자식 stdout 을 읽어 progress 로 (자식 프로세스라 우리 GIL 과 무관)
        def _pump():
            for line in proc.stdout:
                cls.progress.feed(line.rstrip())

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()

        # 자식이 끝날 때까지 매 프레임 양보 - Kit UI 가 멈추지 않음
        app = omni.kit.app.get_app()
        try:
            while proc.poll() is None:
                await app.next_update_async()
        finally:
            reader.join(timeout=2.0)
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                os.unlink(cfg_path)
            except OSError:
                pass

        if proc.returncode != 0:
            raise RuntimeError(
                f"[cad_converter] 변환 실패 (exit={proc.returncode}): {src_path}")

        print(f"[cad_converter] convert done: {dest_path}")
        return dest_path

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

        # --- 파싱 (형식이 어긋난 라인은 조용히 무시) ---
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
        except Exception as e:
            print(f"[cad_converter] progress parse skipped: {e}")
            return

        # --- 통지 (콜백 에러는 숨기지 않고 드러냄) ---
        for cb in self._callbacks:
            try:
                cb(self.step_type, self.desc, self.value, self.step_label)
            except Exception as e:
                print(f"[cad_converter] progress callback error: {e!r}")


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
