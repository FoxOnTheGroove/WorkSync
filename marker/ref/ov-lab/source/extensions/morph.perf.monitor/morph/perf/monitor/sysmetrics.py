"""morph.perf.monitor 내부: 시스템 지표 수집기 (프로세스 메모리, GPU 메모리).

GPU 프레임 시간은 수집하지 않는다: Kit 110.1.3의 `HydraEngineStats.get_gpu_profiler_result()`가
GPU 프로파일러를 켜도 노드 0개를 돌려줘 공개 API로 얻을 수 없었다 (2026-11-01 메인 세션 실험).

내부 구현이다. 다른 익스텐션은 이 모듈에 의존하지 않는다 (지표 이름·단위는 CONTRACT.md 표가 계약).
carb/omni를 import하지 않으므로 Kit 없이 순수 Python으로도 확인할 수 있다.

수집기 인터페이스 (테스트에서 가짜로 바꿔 끼움):
- `key: str`             — 경고 1회 키·로그용 이름
- `read() -> dict | None` — `{지표 이름: float}`. 일부 지표만 돌려줘도 된다.
                           `None` 또는 `{}` = 이번엔 값 없음(조용히 건너뜀).
                           예외 = 읽기 실패 (Monitor가 경고 1회 후 그 수집기 지표만 건너뜀).
- `unavailable: str | None` — 이 앱에서 원천적으로 못 읽는 이유 (Monitor가 info 로그 1회).

Monitor는 리포트 주기마다(최소 간격 있음) 메인 스레드에서 `read()`를 부른다. 핫패스가 아니다.
"""

from __future__ import annotations

import importlib
import os
import sys

EXT_ID = "morph.perf.monitor"

# ---- 공개 지표 이름 (CONTRACT "시스템 지표" 표와 같아야 한다). MB = MiB (2^20 바이트).
SYS_MEM_WORKING_SET_MB = f"{EXT_ID}.sys.mem_working_set_mb"
SYS_MEM_PRIVATE_MB = f"{EXT_ID}.sys.mem_private_mb"
SYS_COLLECT_MS = f"{EXT_ID}.sys.collect_ms"
GPU_MEM_USED_MB = f"{EXT_ID}.gpu.mem_used_mb"
GPU_MEM_BUDGET_MB = f"{EXT_ID}.gpu.mem_budget_mb"

SYSTEM_METRIC_NAMES = (
    SYS_MEM_WORKING_SET_MB,
    SYS_MEM_PRIVATE_MB,
    SYS_COLLECT_MS,
    GPU_MEM_USED_MB,
    GPU_MEM_BUDGET_MB,
)

_MIB = 1024.0 * 1024.0


# ---------------------------------------------------------------- 프로세스 메모리


class _PsutilBackend:
    name = "psutil"

    def __init__(self, psutil_mod):
        self._proc = psutil_mod.Process()
        self._linux = sys.platform.startswith("linux")

    def read(self) -> dict:
        mi = self._proc.memory_info()
        out = {SYS_MEM_WORKING_SET_MB: mi.rss / _MIB}
        private = getattr(mi, "private", None)  # Windows: PrivateUsage
        if private is None and self._linux:
            shared = getattr(mi, "shared", None)
            if shared is not None:
                private = max(0, mi.rss - shared)  # /proc/self/statm resident - shared 와 같음
        if private is not None:
            out[SYS_MEM_PRIVATE_MB] = private / _MIB
        return out


class _WindowsBackend:
    name = "ctypes:K32GetProcessMemoryInfo"

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):  # noqa: N801 - Win32 이름 유지
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = k32.K32GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        gcp = k32.GetCurrentProcess
        gcp.argtypes = []
        gcp.restype = wintypes.HANDLE
        self._ctypes = ctypes
        self._fn = fn
        self._handle = gcp()  # 의사 핸들 (닫을 필요 없음)
        # 구조체는 한 번만 만들어 재사용한다.
        self._buf = PROCESS_MEMORY_COUNTERS_EX()
        self.struct_size = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)  # 64비트 80, 32비트 44
        self._buf.cb = self.struct_size
        self._ref = ctypes.byref(self._buf)

    def read(self) -> dict:
        if not self._fn(self._handle, self._ref, self._buf.cb):
            raise OSError(self._ctypes.get_last_error(), "K32GetProcessMemoryInfo failed")
        b = self._buf
        return {SYS_MEM_WORKING_SET_MB: b.WorkingSetSize / _MIB, SYS_MEM_PRIVATE_MB: b.PrivateUsage / _MIB}


class _StatmBackend:
    name = "/proc/self/statm"

    def __init__(self, path: str = "/proc/self/statm"):
        self._path = path
        self._page = os.sysconf("SC_PAGE_SIZE")
        with open(path, "rb"):
            pass  # 없으면 여기서 실패 → 다음 후보

    def read(self) -> dict:
        with open(self._path, "rb") as f:
            parts = f.read().split()
        resident = int(parts[1]) * self._page
        shared = int(parts[2]) * self._page
        return {SYS_MEM_WORKING_SET_MB: resident / _MIB, SYS_MEM_PRIVATE_MB: max(0, resident - shared) / _MIB}


def _make_process_backend():
    """psutil → (Windows) ctypes → (Linux) /proc/self/statm 순으로 첫 번째로 되는 것."""
    try:
        import psutil  # Kit에서는 omni.kit.pip_archive가 켜져 있을 때만 import 된다

        return _PsutilBackend(psutil)
    except Exception:  # noqa: BLE001 - ImportError 외 초기화 실패도 다음 후보로
        pass
    if sys.platform == "win32":
        try:
            return _WindowsBackend()
        except Exception:  # noqa: BLE001
            pass
    try:
        return _StatmBackend()
    except Exception:  # noqa: BLE001
        pass
    return None


class ProcessMemoryCollector:
    """프로세스 작업 집합(RSS)과 전용 바이트(MB)."""

    key = "sys.mem"

    def __init__(self, backend=None):
        self._backend = backend if backend is not None else _make_process_backend()
        self.unavailable = None if self._backend is not None else "psutil/ctypes/statm 모두 사용 불가"

    @property
    def backend_name(self) -> str | None:
        return None if self._backend is None else self._backend.name

    def read(self) -> dict | None:
        if self._backend is None:
            return None
        return self._backend.read()


# ---------------------------------------------------------------- GPU (omni.hydra.engine.stats, 선택)

_HYDRA_STATS_MODULE = "omni.hydra.engine.stats"


def _import_hydra_stats():
    """앱에 omni.hydra.engine.stats가 없으면 None (조용히)."""
    try:
        return importlib.import_module(_HYDRA_STATS_MODULE)
    except Exception:  # noqa: BLE001 - ImportError 및 바인딩 로드 실패
        return None


class GpuMemoryCollector:
    """`omni.hydra.engine.stats.get_device_info()`의 `usage`/`budget`(바이트) 합계(MB). 모든 GPU 합."""

    key = "gpu.mem"

    def __init__(self, importer=_import_hydra_stats):
        self._importer = importer
        self._get_device_info = None
        self._tried = False
        self.unavailable = None

    def _resolve(self):
        if not self._tried:
            self._tried = True
            mod = self._importer()
            fn = getattr(mod, "get_device_info", None) if mod is not None else None
            if fn is None:
                self.unavailable = f"{_HYDRA_STATS_MODULE} 없음 (앱 의존에 추가하면 GPU 메모리 지표가 나옴)"
            self._get_device_info = fn
        return self._get_device_info

    def read(self) -> dict | None:
        fn = self._resolve()
        if fn is None:
            return None
        devices = fn()
        if not devices:
            return None
        used = 0
        budget = 0
        have_budget = False
        for dev in devices:
            used += int(dev["usage"])
            b = dev.get("budget") if hasattr(dev, "get") else None
            if b is not None:
                budget += int(b)
                have_budget = True
        out = {GPU_MEM_USED_MB: used / _MIB}
        if have_budget:
            out[GPU_MEM_BUDGET_MB] = budget / _MIB
        return out


def default_collectors() -> list:
    """Monitor 기본 수집기 목록."""
    return [ProcessMemoryCollector(), GpuMemoryCollector()]
