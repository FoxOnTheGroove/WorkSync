"""
STEP/CAD -> USD 변환을 omni.services.convert.cad 의 CLI 배치 경로로 실행.

UI 익스텐션의 "Convert Options"를 코드로 재현:
  - Up Axis = Y  ->  config JSON 의 {"iUpAxis": 1}
  - Dest Path    ->  --output-path

STEP/IGES 등 HOOPS 지원 포맷은 hoops_main.py + omni.kit.converter.hoops_core 를 사용.
"""

import os
import json
import glob
import tempfile
import subprocess


def _find_hoops_main(kit_dir: str) -> str:
    """extscache 안에서 hoops_main.py 경로를 자동 탐색."""
    # kit 실행파일 디렉터리 기준 형제 폴더 extscache
    release_dir = os.path.dirname(kit_dir.rstrip("/\\"))
    patterns = [
        os.path.join(release_dir, "extscache",
                     "omni.services.convert.cad-*",
                     "omni", "services", "convert", "cad",
                     "services", "process", "hoops_main.py"),
    ]
    for pat in patterns:
        hits = glob.glob(pat)
        if hits:
            return sorted(hits)[-1]  # 최신 버전
    raise FileNotFoundError(
        f"hoops_main.py 를 찾지 못함. extscache 경로 확인 필요: {patterns}")


def convert_step_to_usd(
    kit_exe: str,
    input_path: str,
    output_path: str,
    up_axis_y: bool = True,
    extra_options: dict | None = None,
    hoops_main: str | None = None,
):
    """
    Args:
        kit_exe:     Kit 실행파일 절대경로 (Windows: kit.exe / Linux: kit)
        input_path:  변환할 STEP 파일 절대경로
        output_path: 출력 USD 절대경로 (.usd/.usda/.usdc)
        up_axis_y:   True면 Y-up(iUpAxis=1), False면 Z-up(iUpAxis=2)
        extra_options: config JSON 에 합칠 추가 converter 옵션
        hoops_main:  hoops_main.py 직접 지정 (없으면 자동 탐색)
    """
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if hoops_main is None:
        hoops_main = _find_hoops_main(os.path.dirname(kit_exe))

    # --- Convert Options 를 config JSON 으로 작성 ---
    options = {"iUpAxis": 1 if up_axis_y else 2}
    if extra_options:
        options.update(extra_options)

    cfg_fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix="cc_cfg_")
    with os.fdopen(cfg_fd, "w") as f:
        json.dump(options, f)

    try:
        exec_arg = (
            f'{hoops_main} '
            f'--input-path "{input_path}" '
            f'--output-path "{output_path}" '
            f'--config-path "{cfg_path}"'
        )
        cmd = [
            kit_exe,
            "--allow-root",
            "--enable", "omni.kit.converter.hoops_core",
            "--exec",
            "--/app/fastShutdown=1",
            exec_arg,
            "--info",
        ]

        print("[실행]", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            print("[stderr]", result.stderr)
            print(f"[실패] returncode={result.returncode}")
        else:
            print(f"[완료] {output_path}")
        return result
    finally:
        os.unlink(cfg_path)


if __name__ == "__main__":
    # 경로를 실제 환경에 맞게 수정
    convert_step_to_usd(
        kit_exe=r"C:/Users/me/Documents/kit-app-template/_build/windows-x86_64/release/kit/kit.exe",
        input_path=r"C:/data/model.stp",
        output_path=r"C:/data/out/model.usd",
        up_axis_y=True,
    )
