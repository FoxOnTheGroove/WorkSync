def _on_req_clash(self, event):
    self._task = asyncio.ensure_future(self._answer_clash(event.payload))

async def _answer_clash(self, payload):
    result = await EbsSimulateService.simulate(payload.get("equipment", ""))
    send("v2t_res_clash", {"ok": bool(result and result.get("ok")),
                           "code": (result or {}).get("code")})
