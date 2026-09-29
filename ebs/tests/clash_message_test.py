"""웹 요청 t2v_req_clash 를 받아 시뮬을 돌리고 v2t_res_clash 로 답하는 핸들러 쪽 코드"""
import asyncio

from ebs.ebs_simulate_service import EbsSimulateService


class ClashHandler:
    """이미 있는 메시지 핸들러에 붙일 두 메서드. send 는 그 핸들러의 것을 쓴다"""

    def _on_req_clash(self, event):
        """t2v_req_clash 를 받으면 시뮬을 띄우고 태스크를 잡아 둔다"""
        self._task = asyncio.ensure_future(self._answer_clash(event.payload))

    async def _answer_clash(self, payload):
        """시뮬이 끝나면 v2t_res_clash 로 ok 와 code 를 보낸다"""
        result = await EbsSimulateService.simulate(payload.get("equipment", ""))
        send("v2t_res_clash", {"ok": bool(result and result.get("ok")),
                               "code": (result or {}).get("code")})
