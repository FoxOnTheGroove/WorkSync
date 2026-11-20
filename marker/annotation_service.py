from .annotation import instance

__all__ = ["AnnotationService"]


class AnnotationService:
    """밖에서 부르는 API 표면"""

    # -- 만들기 -------------------------------------------------------------------

    @classmethod
    def create(cls, kind):
        """label / inlet / outlet 을 만들 준비. 다음 클릭한 모델 표면에 생긴다"""
        return instance().create(kind)

    @classmethod
    def create_plane(cls, axis):
        """축 평면 (xy / yz / zx) 을 바로 만든다. 가운데는 타겟 프림 바운드 중점 (못 구하면 원점)"""
        return instance().create_plane(axis)

    @classmethod
    def relocate(cls, marker_id):
        """그 label / inlet / outlet 을 다음 클릭한 자리로 옮길 준비. 빈 곳이면 취소"""
        return instance().relocate(marker_id)

    @classmethod
    def cancel_create(cls):
        """찍기 대기 (만들기, 위치 재설정) 를 거둔다"""
        return instance().cancel_create()

    # -- 고르기와 고치기 ------------------------------------------------------------

    @classmethod
    def select(cls, marker_id):
        """그 마커를 고른다. None 이면 풀기"""
        return instance().select(marker_id)

    @classmethod
    def set_visible(cls, marker_id, on):
        """마커 하나 보이기 / 숨기기"""
        return instance().set_visible(marker_id, on)

    @classmethod
    def set_all_visible(cls, on):
        """전체 보이기 / 숨기기 (마커마다 값은 그대로)"""
        return instance().set_all_visible(on)

    @classmethod
    def set_text(cls, marker_id, label, description):
        """패널 글"""
        return instance().set_text(marker_id, label, description)

    @classmethod
    def set_font_size(cls, marker_id, px):
        """패널 글자 크기 (첫 줄 px)"""
        return instance().set_font_size(marker_id, px)

    @classmethod
    def set_arrow_scale(cls, scale):
        """화살표 크기 배수 (모든 inlet, outlet 공통)"""
        return instance().set_arrow_scale(scale)

    @classmethod
    def set_plane_at(cls, marker_id, at):
        """평면 위치 (기준점 = 바운드 중점에서 법선 축 방향 거리)"""
        return instance().set_plane_at(marker_id, at)

    @classmethod
    def set_plane_size(cls, marker_id, w, h):
        """평면 크기 [가로, 세로]"""
        return instance().set_plane_size(marker_id, w, h)

    @classmethod
    def set_gizmo(cls, mode):
        """고른 마커 (화살표, 평면) 에 Kit 기즈모: move / rotate / off. 평면은 가운데, 화살표는 표면 끝에서 돈다. 선택이 바뀌면 꺼진다"""
        return instance().set_gizmo(mode)

    @classmethod
    def gizmo(cls):
        """지금 기즈모 모드"""
        return instance().gizmo()

    @classmethod
    def reset_panel(cls, marker_id):
        """패널을 처음 자리, 글에 맞춘 크기로"""
        return instance().reset_panel(marker_id)

    @classmethod
    def remove(cls, marker_id):
        """마커 하나를 지운다"""
        return instance().remove(marker_id)

    @classmethod
    def clear(cls):
        """모두 지운다"""
        return instance().clear()

    # -- 형상 USD 와 마커 파일 ---------------------------------------------------------

    @classmethod
    def set_target_usd(cls, path):
        """마커가 붙는 형상 USD 경로 (열지는 않는다)"""
        return instance().set_target_usd(path)

    @classmethod
    def target_usd(cls):
        """마커가 붙는 형상 USD 경로"""
        return instance().target_usd()

    @classmethod
    def marker_file(cls):
        """마커 파일 경로 (형상 USD 옆 <이름>_marker.json)"""
        return instance().marker_file()

    @classmethod
    def bind_viewport(cls, vph, prim_path, usd_path):
        """그 뷰포트(vph: viewport_api, ui_zstack) 가 이 프림(원본 USD) 을 비춘다. 그 뷰포트에 마커 층을 깔고 그 형상 마커를 그린다"""
        return instance().bind_viewport(vph, prim_path, usd_path)

    @classmethod
    def unbind_viewport(cls, viewport_id):
        """뷰포트 연결을 풀고 마커 층을 걷는다"""
        return instance().unbind_viewport(viewport_id)

    @classmethod
    def diagnose(cls):
        """지금 상태: 마커 층이 붙은 뷰포트, 연결, 타겟, 마커, 어긋나는 점 (hints)"""
        return instance().diagnose()

    @classmethod
    def bindings(cls):
        """뷰포트 id -> {"prim", "usd"}"""
        return instance().bindings()

    @classmethod
    def save(cls, target_usd=None):
        """마커 파일에 저장. target_usd 를 주면 그 형상으로 정하면서 저장 (타겟 없이 찍어 둔 마커의 첫 저장)"""
        return instance().save(target_usd)

    @classmethod
    def load(cls):
        """마커 파일에서 불러오기"""
        return instance().load()

    # -- 질의 -------------------------------------------------------------------

    @classmethod
    def markers(cls):
        """모든 마커 (사본)"""
        return instance().markers()

    @classmethod
    def get(cls, marker_id):
        """마커 하나 (사본). 없으면 None"""
        return instance().get(marker_id)

    @classmethod
    def is_visible(cls, marker_id):
        """마커 하나의 보이기 값"""
        return instance().is_visible(marker_id)

    @classmethod
    def all_visible(cls):
        """전체 표시"""
        return instance().all_visible()

    @classmethod
    def selected(cls):
        """고른 마커 id. 없으면 None"""
        return instance().selected()

    @classmethod
    def clicked(cls):
        """마지막으로 클릭한 패널의 마커 id. 없으면 None"""
        return instance().clicked()

    @classmethod
    def arrow_scale(cls):
        """화살표 크기 배수"""
        return instance().arrow_scale()

    @classmethod
    def creating(cls):
        """찍기를 기다리는 종류. 아니면 빈 칸"""
        return instance().creating()

    @classmethod
    def relocating(cls):
        """위치 재설정을 기다리는 마커 id. 아니면 None"""
        return instance().relocating()

    @classmethod
    def status(cls):
        """마지막 상태 글"""
        return instance().status()

    # -- 알림 -------------------------------------------------------------------

    @classmethod
    def add_listener(cls, fn):
        """바뀔 때마다 fn(reason, marker_id). 전체에 걸친 변경이면 marker_id 는 None"""
        return instance().add_listener(fn)

    @classmethod
    def remove_listener(cls, fn):
        """알림을 끊는다"""
        return instance().remove_listener(fn)
