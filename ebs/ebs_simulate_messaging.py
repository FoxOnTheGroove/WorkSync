import asyncio

import carb.events
import omni.kit.app

from .ebs_simulate_service import EbsSimulateService

__all__ = ["EbsSimulateMessaging"]

IN_SIMULATE = "ebs.simulate"
IN_CLEAR    = "ebs.clear"
IN_RESULT   = "ebs.result"
IN_STATUS   = "ebs.status"
IN_SETTINGS = "ebs.settings"

OUT_DONE   = "ebs.done"
OUT_STATE  = "ebs.state"
OUT_FAILED = "ebs.failed"

SETTINGS = (("usd", "set_usd_path"), ("xml", "set_xml_path"),
            ("search_root", "set_search_root"), ("rail_root", "set_rail_root"),
            ("skin", "set_skin"), ("near_span", "set_near_span"))


class EbsSimulateMessaging:
    """웹에서 온 메시지를 서비스 API 한 줄로 옮기고 결과를 도로 보낸다

    들어오는 것과 나가는 것 둘 다 carb 메시지 버스를 탄다. 브라우저와
    주고받는 꼴은 {event_type, payload} 한 가지뿐이고, JSON 으로 바꾸는
    것은 omni.kit.livestream.messaging 이 한다
    동작은 길게 도므로 핸들러는 띄우기만 하고 바로 돌아온다. 끝나면
    OUT_DONE 을 보낸다. 진행률은 웹이 IN_STATUS 로 물어본다
    """

    def __init__(self):
        """구독 자리만. 거는 것은 start"""
        self._subs = []
        self._task = None

    def start(self) -> bool:
        """받을 것을 걸고 보낼 것을 등록한다. 익스텐션이 시작할 때 부른다"""
        bus = omni.kit.app.get_app().get_message_bus_event_stream()
        for name, work in ((IN_SIMULATE, self._on_simulate),
                           (IN_CLEAR, self._on_clear),
                           (IN_RESULT, self._on_result),
                           (IN_STATUS, self._on_status),
                           (IN_SETTINGS, self._on_settings)):
            self._subs.append(bus.create_subscription_to_pop_by_type(
                carb.events.type_from_string(name), work, name=f"ebs {name}"))
        self._open_out()
        return True

    def stop(self) -> None:
        """구독을 놓고 돌던 일을 접는다"""
        self._subs = []
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    @staticmethod
    def _open_out() -> None:
        """내보낼 이벤트 이름을 스트리밍 쪽에 걸어 둔다

        등록해 둔 이름만 브라우저로 넘어간다. 스트리밍이 아닌 자리에서
        켜면 이 확장이 없으므로 조용히 넘어가고 받는 쪽만 돈다
        """
        try:
            from omni.kit.livestream.messaging import LivestreamMessaging
            messaging = getattr(LivestreamMessaging, "instance", None)
            if messaging is None or isinstance(messaging, type):
                messaging = LivestreamMessaging()
            for name in (OUT_DONE, OUT_STATE, OUT_FAILED):
                messaging.register_event_type_to_send(name)
        except Exception as e:
            print(f"[ebs] messaging: nothing will reach the browser ({e})")

    @staticmethod
    def _send(name: str, payload: dict) -> None:
        """웹으로 한 벌 보낸다"""
        omni.kit.app.get_app().get_message_bus_event_stream().push(
            carb.events.type_from_string(name), payload=payload)

    @staticmethod
    def _field(event, key: str, fallback=None):
        """페이로드에서 한 칸. 없으면 fallback"""
        try:
            got = event.payload[key]
        except Exception:
            return fallback
        return fallback if got is None else got

    def _on_simulate(self, event) -> None:
        """그 장비로 SIM 을 띄운다. 끝나면 판정까지 실어 보낸다"""
        equipment = str(self._field(event, "equipment", ""))
        self._run(IN_SIMULATE, EbsSimulateService.simulate(equipment),
                  equipment)

    def _on_clear(self, event) -> None:
        """그린 것을 전부 놓는다"""
        self._run(IN_CLEAR, EbsSimulateService.clear(), "")

    def _on_result(self, event) -> None:
        """마지막 판정을 그대로 보낸다. 재지 않는다"""
        equipment = str(self._field(event, "equipment", ""))
        self._send(OUT_DONE, {"event": IN_RESULT, "ok": True,
                              "result": EbsSimulateService.get_result(equipment)})

    def _on_status(self, event) -> None:
        """지금 도는 일과 진행률"""
        self._send(OUT_STATE, {"busy": EbsSimulateService.busy(),
                               "progress": EbsSimulateService.get_progress()})

    def _on_settings(self, event) -> None:
        """넘어온 칸만 설정에 반영한다. 안 넘어온 것은 그대로 둔다"""
        done = []
        for key, name in SETTINGS:
            got = self._field(event, key)
            if got is None:
                continue
            getattr(EbsSimulateService, name)(str(got))
            done.append(key)
        two = (self._field(event, "ebs_2port"), self._field(event, "ebs_3port"))
        if two[0] is not None and two[1] is not None:
            EbsSimulateService.set_ebs_paths(str(two[0]), str(two[1]))
            done.append("ebs_paths")
        gaps = (self._field(event, "side_gap"), self._field(event, "ceiling_gap"))
        if gaps[0] is not None and gaps[1] is not None:
            EbsSimulateService.set_min_gaps(float(gaps[0]), float(gaps[1]))
            done.append("min_gaps")
        self._send(OUT_DONE, {"event": IN_SETTINGS, "ok": True, "set": done})

    def _run(self, event_name: str, work, equipment: str) -> None:
        """길게 도는 일 하나를 띄운다. 이미 도는 것이 있으면 안 받는다

        핸들러가 기다리면 그 프레임이 멈춘다. 띄워만 두고 바로 돌려준다
        """
        busy = EbsSimulateService.busy()
        if busy:
            work.close()
            self._send(OUT_FAILED, {"event": event_name, "reason": f"Busy: {busy}"})
            return
        self._task = asyncio.ensure_future(self._await(event_name, work,
                                                       equipment))

    async def _await(self, event_name: str, work, equipment: str) -> None:
        """끝날 때까지 기다렸다가 결과를 보낸다. 터져도 보낸다"""
        try:
            told = await work
        except Exception as e:
            self._send(OUT_FAILED, {"event": event_name,
                                    "reason": f"{type(e).__name__}: {e}"})
            return
        told = told or {}
        self._send(OUT_DONE, {
            "event": event_name,
            "ok": bool(told.get("ok")),
            "reason": told.get("reason", ""),
            "result": (EbsSimulateService.get_result(equipment)
                       if event_name == IN_SIMULATE else {}),
        })
