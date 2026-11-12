# -- 이름 -------------------------------------------------------------------------

TAG = "[annotation]"                    # 콘솔 로그 머리말
NAME = "marker.annotation"              # 제스처, 쿼리, 레지스트리 항목 이름의 앞머리
SCENE_ID = f"{NAME}.scene"              # 뷰포트 레지스트리에 거는 장면 id
SCENE_NAME = "Markers"                  # 뷰포트 장면 항목 이름 (뷰포트 표시 토글에 보임)
SCENE_CATEGORY = "annotation"           # 뷰포트 장면 항목 분류

# -- 마커 종류와 데이터 ---------------------------------------------------------------

KINDS = ("label", "inlet", "outlet")    # 만들 수 있는 마커 종류
ARROW_KINDS = ("inlet", "outlet")       # 화살표가 붙는 종류
DEFAULT_COLORS = {                      # 종류별 기본색 (r, g, b 0..1)
    "label": [0.95, 0.95, 0.95],
    "inlet": [0.2, 0.55, 1.0],
    "outlet": [1.0, 0.45, 0.1],
    "plane": [1.0, 0.85, 0.2],
}
MAX_LABEL = 128                         # label 최대 글자 수
MAX_DESCRIPTION = 1000                  # description 최대 글자 수

# -- 패널 글자 (화면 px) ------------------------------------------------------------

TEXT_SCALE = 1.0                        # 패널 글자 배율. 글자 크기 조절이 붙을 자리
FONT_PX = 14.0                          # 첫 줄(label) 기본 글자 크기. 마커마다 font_px 로 바꾼다
MIN_FONT_PX = 6.0                       # 마커 글자 크기 최소
MAX_FONT_PX = 72.0                      # 마커 글자 크기 최대
DESC_RATIO = 0.8                        # description 글자 크기 = FONT_PX x 이 값
LINE_HEIGHT = 1.6                       # 줄 간격 = 글자 크기 x 이 값 (1.5 에서 줄이 맞닿는다 -> 0.1 만큼 틈)
GLYPH_EM = 1.25                         # 실제 글자 크기 = 지정 크기 x 이 값 (줄 상자 1.5 = 1.2em 에서). 글자 폭 어림에 곱한다
PAD_PX = 5.0                            # 상자 안쪽 여백
MAX_PANEL_LINES = 12                    # 상자 하나에 보이는 description 최대 줄 수 (글 칸을 미리 이만큼 짓는다). 줄은 입력한 줄바꿈 그대로
MIN_WIDE_PX = 48.0                      # 상자 최소 폭 (최소 높이는 첫 줄 하나)
WIDE_EM = 1.0                           # 전각 글자 폭 어림 (실제 em 기준)
NARROW_EM = 0.66                        # 그 밖의 글자 폭 어림 (실제 em 기준). 넉넉하게 잡는다
ELLIPSIS = "…"                     # 잘린 줄 끝에 붙이는 표시

# -- 패널 모양과 자리 (화면 px, y 위로) --------------------------------------------------

DEFAULT_OFFSET = (32.0, 40.0)           # 앵커에서 본 새 상자의 왼쪽 위
BOX_BG = (0.08, 0.08, 0.08, 0.72)       # 상자 바탕색 (rgba)
DESC_COLOR = (0.85, 0.85, 0.85, 1.0)    # description 글자색
DIM_COLOR = (0.6, 0.6, 0.6, 1.0)        # 이름 없는 마커의 id 글자색
ZONES = (None, (-1, 0), (1, 0), (0, 1), (0, -1))  # 잡는 사각형: 몸통(None) + 변 띠 (왼, 오, 위, 아래). 안 겹친다
EDGE_IN_PX = 0.0                        # 변에서 안쪽으로 크기 조절이 들어오는 폭. 0 = 상자 안은 전부 이동
EDGE_OUT_PX = 12.0                      # 크기 조절 띠 폭. 테두리 바로 바깥에만 있다 (커서 <-> / 위아래)
HIT_COLOR = (1.0, 1.0, 1.0, 0.01)       # 잡는 사각형들 색. 거의 투명 (완전 투명은 안 잡힐 수 있어서)
BORDER_PX = 1.0                         # 상자 테두리 두께
BORDER_PICKED_PX = 3.0                  # 고른 상자 테두리 두께
LEADER_PX = 1.5                         # 지시선 두께
DOT_PX = 8.0                            # label 점 크기
BOX_Z = -1.0                            # 상자 바탕의 앞뒤 자리 (글 뒤)

# -- 고른 마커 강조 ------------------------------------------------------------------

HIGHLIGHT_MIX = 0.45                    # 고른 색 = 원래 색을 흰색 쪽으로 이만큼
PICKED_LINE_SCALE = 2.0                 # 고른 마커의 지시선 두께 배율
PICKED_DOT_SCALE = 1.5                  # 고른 label 점 크기 배율

# -- 화살표 (세션 레이어 USD 메시, 월드 크기) ---------------------------------------------

MARKER_ROOT = "/AnnotationMarkers"      # 마커 3D 몸체를 담는 뿌리 (세션 레이어, 클릭, 선택에서 뺌)
ARROW_SCOPE = f"{MARKER_ROOT}/Arrows"   # 화살표 메시 자리
PLANE_SCOPE = f"{MARKER_ROOT}/Planes"   # 평면 메시 자리
LOOKS_SCOPE = f"{MARKER_ROOT}/Looks"    # 화살표, 평면 머티리얼 자리
ARROW_LENGTH_M = 0.3                    # 화살표 기본 길이 (m), 배수 1 일 때. 모든 inlet, outlet 이 같고, 장면 단위로 환산한다
MIN_ARROW_SCALE = 0.1                   # 화살표 크기 배수 최소
MAX_ARROW_SCALE = 10.0                  # 화살표 크기 배수 최대
ARROW_HEAD = 0.25                       # 원뿔 머리 길이 = 화살표 길이 x 이 값
ARROW_RADIUS = 0.08                     # 원뿔 머리 반지름 = 화살표 길이 x 이 값
ARROW_SHAFT = 0.025                     # 몸통 반지름 = 화살표 길이 x 이 값
ARROW_RINGS = 32                        # 몸통, 원뿔 둘레 조각 수
ARROW_ROUGHNESS = 0.4                   # 화살표 표면 거칠기 (0 매끈 .. 1 무광)
ARROW_EMISSION = 0.0                    # 평소 발광 세기 (0 = 조명만 받음)
ARROW_PICKED_EMISSION = 600.0           # 고른 화살표 발광 세기 (OmniPBR emissive_intensity)
ARROW_PREVIEW_GLOW = 0.5                # 고른 화살표 preview 셰이더 발광 = 색 x 이 값

# -- 평면 (세션 레이어 USD 메시, 형상과 겹치면 형상 안에 묻힌다) ---------------------------------

PLANE_AXES = {"xy": (2, 0, 1), "yz": (0, 1, 2), "zx": (1, 2, 0)}  # 평면별 (법선 축, 가로 축, 세로 축). 0=x 1=y 2=z
PLANE_SIZE = (100.0, 100.0)             # 새 평면 크기 [가로, 세로] (스테이지 단위)
PLANE_OPACITY = 0.12                    # 평면 불투명도 (0 투명 .. 1 불투명)
PLANE_PICKED_OPACITY = 0.22             # 고른 평면 불투명도
PLANE_GAP_RATIO = 1e-4                  # 평면 뒷면을 앞면에서 띄우는 거리 = 평면 큰 변 x 이 값 (겹치면 X 자 무늬)
PLANE_EMISSION = 10000.0                # 평면 발광 세기. 평면은 조명을 안 받고 이 빛으로만 보인다 (그림자 안 탐, ebs 판과 같은 값)

# -- 찍기와 손짓 ---------------------------------------------------------------------

PICK_TIMEOUT_S = 5.0                    # 뷰포트 쿼리 답을 기다리는 시간 (초)
NEIGHBOR_PX = 2                         # 법선을 구할 이웃 픽셀 거리
GESTURE_PRIORITY = 1000                 # 패널 몸통 제스처 우선순위 (Kit 선택보다 위)
ARM_PRIORITY = 2000                     # 찍기 대기 클릭 우선순위 (패널보다 위)
SELECT_RELEASE_S = 0.25                 # 패널, 손잡이를 다 놓은 뒤 Kit 클릭 선택을 되살리기까지 (초)
DRAG_SCALE = 2.0                        # 끌기 배율 (마우스 ui px -> 패널 px). 원인 확인 전까지 2.0 고정
REGISTRY_TRIES = 600                    # 뷰포트 레지스트리를 기다리는 프레임 수
