"""Twin ROM 뷰어 구현부.

.twin 런타임을 평가해 TBROM 필드 결과를 USD 포인트 클라우드로 기록한다.
외부에서는 이 클래스를 직접 쓰지 말고 twin_viewer_service 를 경유할 것.
"""

import asyncio
import importlib
import importlib.util
import math
import os
import sys

import numpy as np
import omni.kit.app
import omni.usd
from pxr import Gf, Sdf, UsdGeom, Vt

DEFAULT_PRIM_PATH = "/World/TwinROM"
DEFAULT_POINT_WIDTH = 0.0        # 0 이하 = 바운딩박스에서 자동 산출

# 컬러맵 상한 자동 결정용.
# CFD 필드는 분포가 극단적으로 치우쳐 있는 경우가 많다 — 이 HX 모델은 최댓값이
# p99 의 2.2배라, min~max 로 정규화하면 포인트의 80%가 컬러맵 하위 10%에 깔린다.
# 그래서 꼬리가 두꺼울 때만 상한을 p99 로 자른다. 꼬리가 없으면 최댓값을 그대로 쓴다.
_CLIP_PERCENTILE = 99.0
_TAIL_RATIO = 1.5                # max/p99 가 이보다 크면 '꼬리가 두껍다'고 본다

# ROM 지오메트리의 길이 단위(미터). Ansys 모델은 SI라 1.0 이 기본이다.
# 스테이지 metersPerUnit 과 비교해 좌표를 환산한다 — cm 스테이지(0.01)면 100배가 된다.
DEFAULT_SOURCE_METERS_PER_UNIT = 1.0

# 평가 전 지오메트리만 보여줄 때 쓰는 색
GEOMETRY_COLOR = (0.5, 0.5, 0.5)

# 필드값 → RGB 매핑용 제어점 (0.0 → 1.0).
# Turbo — Ansys Discovery 스타일 가이드의 기본 컬러맵. 진한 파랑에서 시작해
# 청록·초록·노랑을 거쳐 진한 빨강으로 끝난다. viridis 는 위쪽이 노랑이라
# 빨강이 아예 나오지 않아 해석 결과 화면으로는 어색했다.
# matplotlib turbo 를 33 제어점으로 뽑은 값 — 256단계 원본 대비 최대 오차 3.2/255.
_COLORMAP_STOPS = np.array([
    (0.1900, 0.0718, 0.2322), (0.2250, 0.1635, 0.4510), (0.2511, 0.2524, 0.6337),
    (0.2682, 0.3382, 0.7805), (0.2763, 0.4212, 0.8912), (0.2754, 0.5011, 0.9659),
    (0.2586, 0.5796, 0.9988), (0.2138, 0.6589, 0.9796), (0.1584, 0.7355, 0.9231),
    (0.1117, 0.8057, 0.8452), (0.0927, 0.8655, 0.7623), (0.1201, 0.9119, 0.6866),
    (0.1966, 0.9490, 0.5947), (0.3051, 0.9770, 0.4899), (0.4278, 0.9942, 0.3857),
    (0.5466, 0.9991, 0.2958), (0.6436, 0.9900, 0.2336), (0.7260, 0.9647, 0.2064),
    (0.8047, 0.9245, 0.2046), (0.8753, 0.8727, 0.2155), (0.9330, 0.8124, 0.2267),
    (0.9732, 0.7468, 0.2254), (0.9931, 0.6741, 0.2035), (0.9959, 0.5870, 0.1690),
    (0.9836, 0.4929, 0.1285), (0.9580, 0.3996, 0.0883), (0.9211, 0.3149, 0.0548),
    (0.8742, 0.2453, 0.0330), (0.8161, 0.1846, 0.0181), (0.7462, 0.1310, 0.0085),
    (0.6645, 0.0844, 0.0042), (0.5710, 0.0447, 0.0053), (0.4796, 0.0158, 0.0106),
], dtype=np.float32)


def _bootstrap_pywin32() -> None:
    """pywin32 서브패키지 경로와 DLL 디렉터리를 직접 등록한다.

    pywin32는 'pywin32.pth' 로 win32 / win32/lib / pythonwin 을 sys.path 에 올리고
    pywin32_system32 의 DLL을 찾게 만든다. 그런데 Kit의 pipapi 설치 경로는
    site.addsitedir() 없이 sys.path 에 붙기만 해서 .pth 가 처리되지 않는다.
    그 결과 pytwin이 의존하는 win32api 를 import 하지 못한다.
    """
    if importlib.util.find_spec("win32api") is not None:
        return

    for entry in list(sys.path):
        if not entry:
            continue
        dll_dir = os.path.join(entry, "pywin32_system32")
        if not os.path.isdir(dll_dir):
            continue

        try:
            os.add_dll_directory(dll_dir)
        except OSError as e:
            print(f"[twin] pywin32 DLL 디렉터리 등록 실패: {e}")

        for sub in ("win32", os.path.join("win32", "lib"), "pythonwin"):
            path = os.path.join(entry, sub)
            if os.path.isdir(path) and path not in sys.path:
                sys.path.append(path)

        importlib.invalidate_caches()
        print(f"[twin] pywin32 부트스트랩: {entry}")
        return


class TwinViewer:

    _model              = None                      # pytwin.TwinModel
    _loaded:      bool  = False
    _twin_file:   str   = ""
    _rom_names:   list  = []
    _rom_name:    str   = ""
    _field_name:  str   = ""
    _inputs:      dict  = {}                        # 입력 이름 → 현재값
    _outputs:     dict  = {}                        # 출력 이름 → 마지막 평가값
    _output_srcs: dict  = {}                        # 출력 이름 → [(입력 이름, 기여도)]
    _named_selections: list = []

    _prim_path:   str   = DEFAULT_PRIM_PATH
    _point_width: float = DEFAULT_POINT_WIDTH
    _source_mpu:  float = DEFAULT_SOURCE_METERS_PER_UNIT

    _points_cache: dict = {}                        # (rom, named_selection) → (N, 3)
    _point_count: int   = 0
    _value_range: "tuple[float, float] | None" = None

    _play_task          = None                      # asyncio.Task
    _sim_time:    float = 0.0                       # 트윈 내부 시각(초)
    _play_range: "tuple[float, float] | None" = None   # 재생 중 고정할 색 범위

    # 외부 훅 — service 가 등록한다
    _on_loaded    = None                            # fn(twin_file: str) -> None
    _on_evaluated = None                            # fn(point_count: int, value_range) -> None

    # ------------------------------------------------------------------ 수명주기

    @classmethod
    def load(cls, twin_file: str) -> bool:
        """.twin 을 열고 첫 TBROM을 선택한 뒤, 지오메트리를 회색으로 띄운다."""
        if not twin_file:
            print("[twin] .twin 경로가 비어 있습니다.")
            return False

        _bootstrap_pywin32()
        try:
            from pytwin import TwinModel
        except ImportError as e:
            print(f"[twin] pytwin을 import할 수 없습니다: {e}\n"
                  "       extension.toml의 [python.pipapi] 로 설치되거나, "
                  "Kit 파이썬에 'pip install pytwin' 이 되어 있어야 합니다.")
            return False

        cls.unload()

        try:
            model = TwinModel(twin_file)
            # 기본 입출력값을 채우고 라이선스 체크아웃을 여기서 미리 확인한다
            model.initialize_evaluation()
        except Exception as e:
            print(f"[twin] '{twin_file}' 로드 실패: {e}\n"
                  "       graphics 관련 메시지면 pytwin[graphics] 설치가 필요하고,\n"
                  "       라이선스 관련 메시지면 .twin 이 unlicensed export가 아닌 것이다.")
            return False

        rom_names = list(model.tbrom_names or [])
        if not rom_names:
            print(f"[twin] '{twin_file}'에 TBROM이 없습니다. 필드 결과를 그릴 수 없습니다.")
            return False

        cls._model     = model
        cls._twin_file = twin_file
        cls._rom_names = rom_names
        cls._loaded    = True
        cls._refresh_io()
        cls._apply_rom(rom_names[0])

        print(f"[twin] 로드 완료: rom='{cls._rom_name}' ({len(rom_names)}개), "
              f"field='{cls._field_name}', inputs={list(cls._inputs)}")
        if not cls.is_output_field_connected():
            print(f"[twin] 경고: TBROM 출력 필드가 트윈 출력에 연결돼 있지 않습니다.\n"
                  f"       'outField_mode_1' 계열 출력이 없습니다 (현재: {list(cls._outputs)}).\n"
                  "       Twin Builder에서 모드 계수를 출력에 연결해 다시 export해야 합니다.")

        cls.show_geometry()
        if cls._on_loaded:
            cls._on_loaded(twin_file)
        return True

    @classmethod
    def unload(cls) -> None:
        cls.pause()
        model, cls._model = cls._model, None
        if model is not None:
            try:
                model.close()
            except Exception:
                pass                                # close()가 없는 버전도 있다
        cls._reset_model_state()
        cls._twin_file = ""
        cls._loaded    = False

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    # ------------------------------------------------------------------ 모델 정보

    @classmethod
    def get_twin_file(cls) -> str:
        return cls._twin_file

    @classmethod
    def get_rom_names(cls) -> list:
        return list(cls._rom_names)

    @classmethod
    def get_rom_name(cls) -> str:
        return cls._rom_name

    @classmethod
    def select_rom(cls, rom_name: str) -> bool:
        if rom_name not in cls._rom_names:
            print(f"[twin] 알 수 없는 ROM: '{rom_name}'")
            return False
        cls._apply_rom(rom_name)
        return True

    @classmethod
    def get_field_name(cls) -> str:
        return cls._field_name

    @classmethod
    def get_named_selections(cls) -> list:
        return list(cls._named_selections)

    @classmethod
    def get_inputs(cls) -> dict:
        return dict(cls._inputs)

    @classmethod
    def get_outputs(cls) -> dict:
        return dict(cls._outputs)

    @classmethod
    def get_output_sources(cls) -> dict:
        """출력 이름 → [(입력 이름, 기여도)], 기여도 큰 순.

        기여도는 그 출력에서 가장 크게 작용한 입력을 1.0 으로 놓은 상대값이다.
        """
        return {k: list(v) for k, v in cls._output_srcs.items()}

    @classmethod
    def compute_output_sources(cls, rel_delta: float = 0.05) -> dict:
        """입력을 하나씩 섭동해 출력별 입력 기여도를 잰다.

        pytwin 은 입력↔출력 연결 정보를 노출하지 않는다(Description 컬럼이 전부
        비어 있다). 그래서 실제로 흔들어보는 수밖에 없다.

        이 모델처럼 결합된 시스템은 모든 출력이 모든 입력에 반응하므로 '연결됐다/
        아니다'는 정보가 없다. 대신 모든 입력을 같은 비율로 흔든 뒤 출력 변화량을
        비교해 어느 입력이 지배적인지를 상대값으로 낸다.

        평가 횟수는 (입력 수 + 2)회. 끝나면 원래 입력값으로 되돌린다.
        """
        if not cls._loaded:
            return {}

        base = dict(cls._inputs)

        def outputs_now() -> dict:
            return {str(k): float(v) for k, v in (cls._model.outputs or {}).items()}

        try:
            cls._model.initialize_evaluation(inputs=base)
            base_out = outputs_now()
        except Exception as e:
            print(f"[twin] 출력 민감도 분석 실패: {e}")
            return {}

        deltas = {name: {} for name in base_out}    # 출력 → {입력: |Δ출력|}
        for name, value in base.items():
            v = float(value)
            # 값이 0이면 상대 섭동이 0이 되므로 절대값으로 흔든다
            step = abs(v) * rel_delta if abs(v) > 1e-12 else 1.0
            try:
                cls._model.initialize_evaluation(inputs={**base, name: v + step})
                trial_out = outputs_now()
            except Exception:
                continue
            for out_name, base_value in base_out.items():
                deltas[out_name][name] = abs(trial_out.get(out_name, base_value) - base_value)

        # 입력마다 같은 비율로 흔들었으므로 |Δ출력| 끼리 바로 비교할 수 있다
        sources = {}
        for out_name, per_input in deltas.items():
            top = max(per_input.values(), default=0.0)
            if top <= 0.0:
                sources[out_name] = []              # 어떤 입력에도 반응하지 않음
                continue
            sources[out_name] = sorted(
                ((name, d / top) for name, d in per_input.items()),
                key=lambda item: item[1], reverse=True,
            )

        try:
            cls._model.initialize_evaluation(inputs=base)   # 원래 상태로 복구
            cls._refresh_io()
        except Exception as e:
            print(f"[twin] 입력 복구 실패: {e}")

        cls._output_srcs = sources
        return cls.get_output_sources()

    @classmethod
    def is_output_field_connected(cls) -> bool:
        """TBROM 모드 계수가 트윈 출력에 연결돼 있는지 확인한다.

        연결은 Twin Builder에서 트윈을 만들 때 하는 것이고 런타임에 걸 수 없다.
        연결돼 있으면 'outField_mode_{i}' (TBROM이 여럿이면 '..._{rom_name}')
        형태의 출력이 존재한다. 없으면 필드 계산이 성립하지 않는다.
        """
        suffix = f"_{cls._rom_name}" if len(cls._rom_names) > 1 else ""
        return f"outField_mode_1{suffix}" in {str(k) for k in cls._outputs}

    # ------------------------------------------------------------------ 표시

    @classmethod
    def show_geometry(cls, named_selection: "str | None" = None) -> bool:
        """평가 없이 지오메트리만 회색 포인트로 띄운다."""
        if not cls._loaded:
            print("[twin] 먼저 .twin 파일을 로드하세요.")
            return False

        points = cls._get_points(named_selection)
        if points is None:
            return False

        cls._value_range = None
        return cls._write(points, None)

    @classmethod
    def evaluate(cls, inputs: dict, named_selection: "str | None" = None) -> bool:
        """주어진 입력으로 ROM을 평가하고 포인트에 색을 입힌다."""
        if not cls._loaded:
            print("[twin] 먼저 .twin 파일을 로드하세요.")
            return False

        cls._inputs.update(inputs or {})

        try:
            cls._model.initialize_evaluation(inputs=cls._inputs)
        except Exception as e:
            print(f"[twin] 평가 실패: {e}")
            return False

        cls._refresh_io()
        if not cls._output_srcs:
            cls.compute_output_sources()

        points = cls._get_points(named_selection)
        if points is None:
            return False

        field = cls._get_field(named_selection, len(points))
        if field is None:
            return False

        cls._value_range = cls._compute_range(field)
        if not cls._write(points, field):
            return False

        print(f"[twin] 평가 완료: {cls._point_count} points, "
              f"{cls._field_name or 'field'} range="
              f"{cls._value_range[0]:.4g} ~ {cls._value_range[1]:.4g}")

        if cls._on_evaluated:
            cls._on_evaluated(cls._point_count, cls._value_range)
        return True

    # ------------------------------------------------------------------ 재생

    # 트윈의 시간축은 USD 스테이지 타임라인과 별개다. 타임라인에 굽지 않고 직접
    # 스텝을 돌려서 매 프레임 색만 갱신한다. 그래야 프레임 하나하나가 실제 트윈
    # 상태이고, 타임라인을 씬의 다른 애니메이션과 다투지 않는다.

    @classmethod
    def play(cls, step_size: float = 0.0, named_selection: "str | None" = None) -> bool:
        """현재 시각부터 스텝을 진행하며 색을 갱신한다. pause 후 부르면 이어서 재개."""
        if not cls._loaded:
            print("[twin] 먼저 .twin 파일을 로드하세요.")
            return False
        if cls.is_playing():
            return True

        if step_size <= 0.0:
            step_size = cls.get_default_step_size()
        if step_size <= 0.0:
            print("[twin] step size 를 결정할 수 없습니다. 값을 지정하세요.")
            return False

        points = cls._get_points(named_selection)
        if points is None:
            return False

        # 프레임마다 색 범위를 다시 잡으면 같은 색이 프레임마다 다른 값을 뜻하게 된다.
        # 재생 시작 시점의 범위로 고정해야 변화를 색으로 읽을 수 있다.
        if cls._play_range is None:
            field = cls._get_field(named_selection, len(points))
            if field is None:
                return False
            cls._play_range = cls._compute_range(field)

        cls._play_task = asyncio.ensure_future(
            cls._play_loop(step_size, named_selection, len(points))
        )
        print(f"[twin] play: step={step_size:g}s, t={cls._sim_time:g}s 부터")
        return True

    @classmethod
    def pause(cls) -> None:
        """재생만 멈춘다. 트윈 상태는 유지되므로 play 로 이어서 재개할 수 있다."""
        task, cls._play_task = cls._play_task, None
        if task is not None and not task.done():
            task.cancel()
            print(f"[twin] pause: t={cls._sim_time:g}s")

    @classmethod
    def stop(cls, named_selection: "str | None" = None) -> None:
        """재생을 멈추고 t=0 으로 되돌린다."""
        cls.pause()
        cls._sim_time = 0.0
        cls._play_range = None
        if cls._loaded:
            # initialize_evaluation 이 시각을 0 으로 되감는다
            cls.evaluate({}, named_selection)
        print("[twin] stop: t=0 으로 리셋")

    @classmethod
    def is_playing(cls) -> bool:
        return cls._play_task is not None and not cls._play_task.done()

    @classmethod
    def get_sim_time(cls) -> float:
        """트윈 내부 시각(초)."""
        return cls._sim_time

    @classmethod
    def get_default_step_size(cls) -> float:
        """트윈에 박혀 있는 기본 step size(초). 모르면 0.0."""
        return cls._default_sim_setting(1)

    @classmethod
    def get_default_end_time(cls) -> float:
        """트윈에 박혀 있는 기본 종료 시각(초). 모르면 0.0."""
        return cls._default_sim_setting(0)

    @classmethod
    async def _play_loop(cls, step_size: float, named_selection: "str | None",
                         expected: int) -> None:
        end_time = cls.get_default_end_time()
        first_field = None
        warned_static = False

        try:
            while True:
                try:
                    cls._model.evaluate_step_by_step(step_size=step_size,
                                                     inputs=cls._inputs)
                except Exception as e:
                    print(f"[twin] 스텝 평가 실패: {e}")
                    return

                cls._sim_time = float(getattr(cls._model, "evaluation_time",
                                              cls._sim_time + step_size))

                field = cls._get_field(named_selection, expected)
                if field is None:
                    return

                # 정적 트윈이면 여기서 영원히 같은 그림이 나온다. 코드가 안 도는 것과
                # 구분이 안 되므로 한 번은 알려준다.
                if first_field is None:
                    first_field = field.copy()
                elif not warned_static and np.array_equal(first_field, field):
                    print("[twin] 경고: 스텝을 진행해도 필드가 변하지 않습니다. "
                          "정적 트윈이면 재생해도 그림이 바뀌지 않습니다.")
                    warned_static = True

                cls._refresh_io()
                cls._value_range = cls._play_range
                if not cls._write_colors(field):
                    return

                if cls._on_evaluated:
                    cls._on_evaluated(cls._point_count, cls._value_range)

                if end_time > 0.0 and cls._sim_time >= end_time:
                    print(f"[twin] play 종료: t={cls._sim_time:g}s (기본 종료 시각 도달)")
                    return

                await omni.kit.app.get_app().next_update_async()
        except asyncio.CancelledError:
            return
        finally:
            cls._play_task = None

    @classmethod
    def get_point_count(cls) -> int:
        return cls._point_count

    @classmethod
    def get_value_range(cls) -> "tuple[float, float] | None":
        """직전 평가에서 컬러맵에 쓴 (lo, hi)."""
        return cls._value_range

    # ------------------------------------------------------------------ 표시 설정

    @classmethod
    def set_prim_path(cls, prim_path: str) -> None:
        cls._prim_path = prim_path or DEFAULT_PRIM_PATH

    @classmethod
    def get_prim_path(cls) -> str:
        return cls._prim_path

    @classmethod
    def set_point_width(cls, width: float) -> None:
        """0 이하면 바운딩박스에서 자동 산출한다."""
        cls._point_width = float(width)

    @classmethod
    def get_point_width(cls) -> float:
        return cls._point_width

    @classmethod
    def set_source_meters_per_unit(cls, mpu: float) -> None:
        """ROM 좌표의 길이 단위(미터 기준). 미터면 1.0, 밀리미터면 0.001."""
        if mpu > 0.0:
            cls._source_mpu = float(mpu)

    @classmethod
    def clear(cls) -> None:
        """기록한 포인트 클라우드 prim을 스테이지에서 제거한다."""
        cls.pause()                     # 지운 prim 에 색을 쓰려는 루프를 먼저 멈춘다
        stage = omni.usd.get_context().get_stage()
        if stage and stage.GetPrimAtPath(cls._prim_path).IsValid():
            stage.RemovePrim(cls._prim_path)
            print(f"[twin] '{cls._prim_path}' 제거")
        cls._point_count = 0
        cls._value_range = None

    # ------------------------------------------------------------------ 내부

    @classmethod
    def _reset_model_state(cls) -> None:
        cls._rom_names        = []
        cls._rom_name         = ""
        cls._field_name       = ""
        cls._inputs           = {}
        cls._outputs          = {}
        cls._output_srcs      = {}
        cls._named_selections = []
        cls._points_cache     = {}
        cls._point_count      = 0
        cls._value_range      = None
        cls._sim_time         = 0.0
        cls._play_range       = None

    @classmethod
    def _refresh_io(cls) -> None:
        """현재 평가 시점의 입출력값을 읽어 캐시한다."""
        try:
            cls._inputs = {str(k): v for k, v in (cls._model.inputs or {}).items()}
        except Exception as e:
            print(f"[twin] 입력 목록을 읽지 못했습니다: {e}")
            cls._inputs = {}
        try:
            cls._outputs = {str(k): v for k, v in (cls._model.outputs or {}).items()}
        except Exception as e:
            print(f"[twin] 출력 목록을 읽지 못했습니다: {e}")
            cls._outputs = {}

    @classmethod
    def _default_sim_setting(cls, index: int) -> float:
        """런타임의 기본 시뮬레이션 설정 (end_time, step_size, tolerance)."""
        runtime = getattr(cls._model, "_twin_runtime", None)
        getter = getattr(runtime, "twin_get_default_simulation_settings", None)
        if getter is None:
            return 0.0
        try:
            return float(getter()[index])
        except Exception:
            return 0.0

    @classmethod
    def _apply_rom(cls, rom_name: str) -> None:
        """선택된 ROM 기준으로 필드명과 named selection 목록을 갱신한다."""
        cls._rom_name = rom_name
        try:
            cls._field_name = cls._model.get_field_output_name(rom_name) or ""
        except Exception:
            cls._field_name = ""
        try:
            cls._named_selections = list(cls._model.get_named_selections(rom_name) or [])
        except Exception as e:
            print(f"[twin] named selection 목록을 읽지 못했습니다: {e}")
            cls._named_selections = []

    @classmethod
    def _get_points(cls, named_selection: "str | None") -> "np.ndarray | None":
        # 지오메트리는 입력값에 따라 변하지 않으므로 (rom, ns) 조합마다 1회만 받는다
        key = (cls._rom_name, named_selection)
        cached = cls._points_cache.get(key)
        if cached is not None:
            return cached

        try:
            raw = cls._model.generate_points(cls._rom_name, False, named_selection)
        except Exception as e:
            print(f"[twin] 포인트 생성 실패: {e}")
            return None

        arr = np.asarray(raw, dtype=np.float32).reshape(-1)
        if arr.size == 0 or arr.size % 3 != 0:
            print(f"[twin] 포인트 배열 크기가 3의 배수가 아닙니다: {arr.size}")
            return None

        points = arr.reshape(-1, 3)
        cls._points_cache[key] = points
        lo, hi = points.min(axis=0), points.max(axis=0)
        print(f"[twin] 포인트 {len(points)}개, bbox={tuple(lo.round(4))} ~ {tuple(hi.round(4))}")
        return points

    @classmethod
    def _get_field(cls, named_selection: "str | None", expected: int) -> "np.ndarray | None":
        """스칼라 필드는 그대로, 벡터 필드는 크기(magnitude)로 환산해 반환."""
        try:
            raw = cls._model.generate_snapshot(cls._rom_name, False, named_selection)
        except Exception as e:
            print(f"[twin] 스냅샷 생성 실패: {e}")
            return None

        arr = np.asarray(raw, dtype=np.float32).reshape(-1)
        if arr.size == expected:
            return arr
        if arr.size == expected * 3:
            # 속도 같은 벡터 필드 — 포인트당 색 하나로 매핑하려면 크기로 환산
            return np.linalg.norm(arr.reshape(-1, 3), axis=1)

        print(f"[twin] 스냅샷 크기({arr.size})가 포인트 수({expected})와 맞지 않습니다.")
        return None

    @staticmethod
    def _compute_range(field: "np.ndarray") -> tuple:
        """컬러맵에 쓸 (lo, hi) 를 필드 분포에서 자동으로 정한다.

        꼬리가 두꺼우면(최댓값이 p99 보다 훨씬 큼) 상한을 p99 로 잘라야 나머지
        99% 가 컬러맵 전체를 쓴다. 자르지 않으면 소수의 극단값이 범위를 독차지해
        대부분의 포인트가 맨 아래 색에 뭉친다. 꼬리가 없으면 그대로 최댓값을 쓴다.
        """
        lo, real_hi = float(field.min()), float(field.max())
        p99 = float(np.percentile(field, _CLIP_PERCENTILE))

        if p99 <= lo or real_hi <= p99 * _TAIL_RATIO:
            print(f"[twin] 컬러맵 범위 {lo:.6g} ~ {real_hi:.6g} (꼬리 얇음, 자르지 않음)")
            return lo, real_hi

        print(f"[twin] 컬러맵 범위 {lo:.6g} ~ {p99:.6g} "
              f"(p{_CLIP_PERCENTILE:g} 로 자름. 실제 최대 {real_hi:.6g}, "
              f"max/p99={real_hi / p99:.2f} — 상위 1%는 최상단 색으로 포화)")
        return lo, p99

    @classmethod
    def _colorize(cls, field: "np.ndarray", value_range: tuple) -> "np.ndarray":
        lo, hi = value_range
        span = hi - lo
        if not math.isfinite(span) or span < 1e-12:
            t = np.zeros(len(field), dtype=np.float32)
        else:
            t = np.clip((field - lo) / span, 0.0, 1.0)

        seg  = len(_COLORMAP_STOPS) - 1
        pos  = t * seg
        idx  = np.clip(pos.astype(np.int32), 0, seg - 1)
        frac = (pos - idx).reshape(-1, 1)
        return (_COLORMAP_STOPS[idx] * (1.0 - frac) + _COLORMAP_STOPS[idx + 1] * frac)

    @classmethod
    def _resolve_width(cls, points: "np.ndarray") -> float:
        if cls._point_width > 0.0:
            return cls._point_width
        # 자동: 바운딩박스 대각선을 포인트 수의 세제곱근으로 나눠 대략적인 간격을 잡는다
        diag = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
        if diag <= 0.0:
            return 1.0
        width = diag / max(1.0, len(points) ** (1.0 / 3.0))
        print(f"[twin] point width 자동 산출: {width:.6g} (bbox diag={diag:.6g})")
        return width

    @classmethod
    def _write(cls, points: "np.ndarray", field: "np.ndarray | None") -> bool:
        """포인트를 UsdGeom.Points 로 기록한다. field 가 None 이면 회색."""
        stage = omni.usd.get_context().get_stage()
        if not stage:
            print("[twin] 스테이지를 가져올 수 없습니다. 먼저 스테이지를 여세요.")
            return False

        # ROM은 미터, 스테이지는 보통 cm — 환산하지 않으면 100배 작게 나온다
        stage_mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
        scale = cls._source_mpu / stage_mpu
        if abs(scale - 1.0) > 1e-9:
            points = points * np.float32(scale)
            print(f"[twin] 단위 환산 x{scale:g} "
                  f"(source {cls._source_mpu:g} m/unit → stage {stage_mpu:g} m/unit)")

        count = len(points)
        width = cls._resolve_width(points)

        gprim = UsdGeom.Points.Define(stage, Sdf.Path(cls._prim_path))
        gprim.CreatePointsAttr().Set(cls._to_vec3f(points))
        gprim.CreateWidthsAttr().Set(Vt.FloatArray([width] * count))
        gprim.SetWidthsInterpolation(UsdGeom.Tokens.vertex)

        # 회색은 상수 하나로 충분하다 — 포인트마다 같은 값을 87만 개 쓸 이유가 없다.
        # 반대로 필드 색은 포인트마다 다르므로 interpolation 도 함께 바꿔야 한다.
        color_pv = UsdGeom.PrimvarsAPI(gprim).CreatePrimvar(
            "displayColor", Sdf.ValueTypeNames.Color3fArray
        )
        if field is None:
            color_pv.SetInterpolation(UsdGeom.Tokens.constant)
            color_pv.Set(Vt.Vec3fArray([Gf.Vec3f(*GEOMETRY_COLOR)]))
        else:
            color_pv.SetInterpolation(UsdGeom.Tokens.vertex)
            color_pv.Set(cls._to_vec3f(cls._colorize(field, cls._value_range)))

        lo, hi = points.min(axis=0), points.max(axis=0)
        gprim.CreateExtentAttr().Set(Vt.Vec3fArray([
            Gf.Vec3f(*lo.tolist()), Gf.Vec3f(*hi.tolist()),
        ]))

        cls._point_count = count
        return True

    @classmethod
    def _write_colors(cls, field: "np.ndarray") -> bool:
        """이미 기록된 포인트의 색만 갱신한다. 재생 중에는 좌표가 변하지 않는다."""
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return False
        prim = stage.GetPrimAtPath(cls._prim_path)
        if not prim.IsValid():
            print(f"[twin] '{cls._prim_path}' 가 없습니다. 먼저 Evaluate 하세요.")
            return False

        color_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
        if not color_pv:
            return False
        color_pv.SetInterpolation(UsdGeom.Tokens.vertex)
        color_pv.Set(cls._to_vec3f(cls._colorize(field, cls._value_range)))
        return True

    @staticmethod
    def _to_vec3f(arr: "np.ndarray") -> "Vt.Vec3fArray":
        return Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(arr, dtype=np.float32))
