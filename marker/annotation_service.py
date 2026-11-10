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
    def cancel_create(cls):
        """찍기 대기를 거둔다"""
        return instance().cancel_create()

    # -- 고르기와 고치기 ------------------------------------------------------------

    @classmethod
    def select(cls, marker_id):
        """그 마커를 고른다. None 이면 풀기"""
        return instance().select(marker_id)

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
    def status(cls):
        """마지막 상태 글"""
        return instance().status()

    # -- 알림 -------------------------------------------------------------------

    @classmethod
    def add_listener(cls, fn):
        """바뀔 때마다 fn(reason)"""
        return instance().add_listener(fn)

    @classmethod
    def remove_listener(cls, fn):
        """알림을 끊는다"""
        return instance().remove_listener(fn)
