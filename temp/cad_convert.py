import requests


# omni.services.convert.cad 서비스 포트 (Kit 기본값: 8011)
CAD_SERVICE_URL = "http://localhost:8011"


def convert_cad_to_usd(input_path: str, output_path: str, port: int = 8011):
    """
    omni.services.convert.cad 서비스로 CAD 파일을 USD로 변환.

    Args:
        input_path:  변환할 CAD 파일 절대 경로 (.stp, .step 등)
        output_path: 출력 USD 파일 절대 경로
        port:        서비스 포트 (기본 8011, 앱 설정에 따라 다름)
    """
    url = f"http://localhost:{port}/convert/cad/process"
    payload = {
        "import_path": input_path,
        "output_path": output_path,
        "converter_options": {
            "iUpAxis": 1,         # 1 = Y-up, 2 = Z-up
            "instancing": True,   # 반복 파트 인스턴싱
            "bOptimize": True,
            "convertHidden": False,
            "dMetersPerUnit": 1.0,
        }
    }

    print(f"[변환 시작] {input_path} -> {output_path}")

    resp = requests.post(url, json=payload, timeout=600)

    if resp.status_code == 200:
        print("[완료]", resp.json().get("comment", ""))
    elif resp.status_code == 503:
        print("[실패] 변환 슬롯이 가득 참. 잠시 후 재시도.")
    elif resp.status_code == 422:
        print("[실패] 잘못된 경로 또는 지원하지 않는 포맷.")
    else:
        print(f"[오류] {resp.status_code}: {resp.text}")

    return resp


def get_service_status(port: int = 8011):
    """현재 변환 서비스 상태 확인."""
    resp = requests.get(f"http://localhost:{port}/convert/cad/status")
    print(resp.json())
    return resp.json()


if __name__ == "__main__":
    # 서비스 상태 먼저 확인
    get_service_status()

    # 변환 실행 (경로를 실제 경로로 수정)
    convert_cad_to_usd(
        input_path="C:/path/to/model.stp",
        output_path="C:/path/to/output/model.usd",
    )
