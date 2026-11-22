# -- 이름 -------------------------------------------------------------------------

TAG = "[annotation]"                    # 콘솔 로그 머리말
NAME = "marker.annotation"              # 제스처, 쿼리 이름의 앞머리

# -- 마커 종류와 데이터 ---------------------------------------------------------------

KINDS = ("label", "inlet", "outlet")    # 만들 수 있는 마커 종류
ARROW_KINDS = ("inlet", "outlet")       # 화살표가 붙는 종류
DEFAULT_COLORS = {                      # 종류별 기본색 (r, g, b 0..1)
    "label": [0.95, 0.95, 0.95],
    "inlet": [0.2, 0.55, 1.0],
    "outlet": [1.0, 0.45, 0.1],
    "plane": [1.0, 0.85, 0.2],
}
MARKER_FILE_SUFFIX = "_marker.json"     # 마커 파일 이름 = 형상 USD 이름(확장자 뺌) + 이것, 형상 USD 와 같은 자리
MARKER_FILE_VERSION = 1                 # 마커 파일 판. 이보다 새 판은 읽지 않는다
MAX_LABEL = 128                         # label 최대 글자 수
MAX_DESCRIPTION = 1000                  # description 최대 글자 수

# -- 패널 글자 (화면 px) ------------------------------------------------------------

TEXT_SCALE = 1.0                        # 패널 글자 배율. 글자 크기 조절이 붙을 자리
FONT_PX = 35.0                          # 첫 줄(label) 기본 글자 크기. 마커마다 font_px 로 바꾼다
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
PLANE_TITLE_COLOR = DIM_COLOR           # 평면 패널 글자색. inlet / outlet 패널 (이름 없으면 이 색) 과 같게, 골라도 안 바뀐다
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
ARROW_LENGTH_M = 0.12                   # 화살표 기본 길이 (m), 배수 1 일 때 (전의 0.3 x 0.4). 모든 inlet, outlet 이 같고, 장면 단위로 환산한다
MIN_ARROW_SCALE = 0.1                   # 화살표 크기 배수 최소
MAX_ARROW_SCALE = 10.0                  # 화살표 크기 배수 최대
ARROW_HEAD = 0.25                       # 원뿔 머리 길이 = 화살표 길이 x 이 값
ARROW_RADIUS = 0.08                     # 원뿔 머리 반지름 = 화살표 길이 x 이 값
ARROW_SHAFT = 0.025                     # 몸통 반지름 = 화살표 길이 x 이 값
ARROW_HIT_SCALE = 1.5                   # 화살표 잡히는 굵기 = 원뿔 머리 반지름 x 이 값 (광선 판정)
ARROW_RINGS = 32                        # 몸통, 원뿔 둘레 조각 수
ARROW_SHADE = 0.55                      # 화살표 머티리얼 색 = 마커 색 x 이 값 (진하게). 패널 색은 그대로
ARROW_ROUGHNESS = 0.4                   # 화살표 표면 거칠기 (0 매끈 .. 1 무광)
ARROW_EMISSION = 0.0                    # 평소 발광 세기 (0 = 조명만 받음)
ARROW_PICKED_EMISSION = 600.0           # 고른 화살표 발광 세기 (OmniPBR emissive_intensity)
ARROW_PREVIEW_GLOW = 0.5                # 고른 화살표 preview 셰이더 발광 = 색 x 이 값

# -- 평면 (세션 레이어 USD 메시, 형상과 겹치면 형상 안에 묻힌다) ---------------------------------

PLANE_AXES = {"xy": (2, 0, 1), "yz": (0, 1, 2), "zx": (1, 2, 0)}  # 평면별 (법선 축, 가로 축, 세로 축). 0=x 1=y 2=z
PLANE_COLORS = {                        # 평면 색 = 법선 축의 기즈모 색 (x 빨강, y 초록, z 파랑)
    "xy": [0.0, 0.2, 0.9],
    "yz": [0.85, 0.0, 0.0],
    "zx": [0.0, 0.6, 0.05],
}
PLANE_BOUND_SCALE = 1.4                 # 새 평면 크기 = 타겟 프림 바운드의 그 평면 두 축 길이 x 이 값
PLANE_SIZE = (100.0, 100.0)             # 바운드를 못 구할 때 새 평면 크기 [가로, 세로] (스테이지 단위)
PLANE_OPACITY = 0.8                     # 평면 불투명도 (0 투명 .. 1 불투명)
PLANE_PICKED_OPACITY = 0.88             # 고른 평면 불투명도
PLANE_HOVER_OPACITY = 0.85              # 마우스 아래 평면 불투명도
PLANE_PRESS_OPACITY = 0.95              # 누르고 있는 / 끄는 평면 불투명도
PLANE_LOOKS = {                         # 평면 상태별 (불투명도, 흰색 섞기). 겹치면 press > hover > selected > idle
    "idle": (PLANE_OPACITY, 0.0),
    "selected": (PLANE_PICKED_OPACITY, 0.15),
    "hover": (PLANE_HOVER_OPACITY, 0.08),
    "press": (PLANE_PRESS_OPACITY, 0.3),
}
PLANE_PRIORITY = 500                    # 평면 누름, 끌기 제스처 우선순위 (패널보다 아래, Kit 선택보다 위)
PLANE_PRESS_AT_DOWN = True              # 평면 끌기를 누르는 순간 시작 (누름 색이 바로). Kit 이 못 하면 움직인 뒤
PLANE_DEPTH_EPS = 1e-3                  # 깊이 비교 여유 = 평면까지 거리 x 이 값. 모델 표면이 이만큼 더 가까워야 가린 것
CLICK_SLOP_NDC = 0.01                   # 누른 자리에서 이만큼(NDC) 넘게 움직이고 떼면 클릭이 아니다 (카메라 끌기). 빈 곳 클릭으로 선택 풀기에 쓴다
GIZMO_MODES = ("move", "rotate", "off")  # 기즈모 모드 (set_gizmo). 평면은 move 만
GIZMO_SIZE = 1.0                        # Kit 변환 기즈모 크기 (TransformManipulator size)
GIZMO_ZONE_PX = 110.0                   # 기즈모 피봇에서 이 px 안이면 그 뷰포트 orbit 을 끈다 (기즈모 끌기가 카메라로 안 가게)
PLANE_BODY_DRAG = False                 # 평면 본체를 끌어 평면 안에서 옮기기. 끔: 평면은 기즈모로 옮긴다
PLANE_GAP_RATIO = 1e-4                  # 평면 뒷면을 앞면에서 띄우는 거리 = 평면 큰 변 x 이 값 (겹치면 X 자 무늬)
PLANE_EMISSION = 2000.0                 # 평면 발광 세기. 평면은 조명을 안 받고 이 빛으로만 보인다. 너무 세면 톤매핑이 색을 흰 쪽으로 날린다 (파스텔). 전 값 10000

# -- 찍기와 손짓 ---------------------------------------------------------------------

PICK_TIMEOUT_S = 5.0                    # 뷰포트 쿼리 답을 기다리는 시간 (초)
NEIGHBOR_PX = 2                         # 법선을 구할 이웃 픽셀 거리
GESTURE_PRIORITY = 1000                 # 패널 몸통 제스처 우선순위 (Kit 선택보다 위)
ARM_PRIORITY = 2000                     # 찍기 대기 클릭 우선순위 (패널보다 위)
ORBIT_RELEASE_S = 0.05                  # 마커에서 빠져나온 뒤 orbit 을 돌려주기까지 (초). 누르는 순간의 빈틈만 메운다
SELECT_RELEASE_S = 0.25                 # 패널, 손잡이를 다 놓은 뒤 Kit 클릭 선택을 되살리기까지 (초)
DRAG_SCALE = 2.0                        # 끌기 배율 (마우스 ui px -> 패널 px). 원인 확인 전까지 2.0 고정
ORBIT_YIELD = True                      # 마커가 마우스를 쓰는 동안 그 뷰포트 orbit(카메라 조작) 을 끄는가
ORBIT_MODULE = "sk.hyview_eqp_orbit_extension"  # orbit 익스텐션 모듈
ORBIT_CLASS = "OrbitExtension"          # 그 안의 익스텐션 클래스
ORBIT_SCENES = "_orbit_scene_dict"      # 클래스 속성: 뷰포트 id -> orbit scene
ORBIT_MANIPULATOR = "_OrbitOnlyScene__manipulator"  # orbit scene 의 manipulator 속성
ORBIT_ENABLED = "enabled"               # 그 manipulator 의 켜기 / 끄기 속성 (bool)
ORBIT_DRAG_BEGAN = "hyview_eqp_orbit_extension:gesture:drag:on_began_first"  # orbit 이 카메라 끌기를 시작함 (메시지 버스)
ORBIT_DRAG_ENDED = "hyview_eqp_orbit_extension:gesture:drag:on_ended"        # orbit 이 카메라 끌기를 끝냄 (메시지 버스)
