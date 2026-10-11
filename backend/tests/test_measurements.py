"""The numbers the pitch quotes are measured, logged, and None when not measured."""
import asyncio
import json

import httpx

from backend.agents.llm_client import LightningClient
from backend.agents.orchestrator import _Turn, llm_throughput
from backend.metrics import read_mem_used_gb

from .conftest import make_stack
from .test_audit_hooks import attach
from .test_orchestrator import run_event


def test_mem_used_from_meminfo(tmp_path):
    f = tmp_path / "meminfo"
    f.write_text("MemTotal:       125829120 kB\nMemFree:  1 kB\nMemAvailable:    25165824 kB\n")
    assert read_mem_used_gb(f) == round((125829120 - 25165824) * 1024 / 1e9, 1)
    assert read_mem_used_gb(tmp_path / "missing") is None
    f.write_text("MemTotal: 5 kB\n")
    assert read_mem_used_gb(f) is None


def test_usage_is_read_from_the_server_response():
    def serve(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        usage = {"prompt_tokens": 900, "completion_tokens": 120}
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": usage})

    async def go():
        c = LightningClient(base_url="http://x/v1", timeout_s=5, transport=httpx.MockTransport(serve))
        r = await c.chat_json("s", "u", {"type": "object"}, max_tokens=10)
        assert (r.completion_tokens, r.prompt_tokens) == (120, 900)
        await c.aclose()
    asyncio.run(go())


def test_throughput_math_and_none_when_unmeasured():
    # three concurrent calls of 100 tokens each over 1 s, then three of 200 over 2 s
    r1 = [_Turn(completion_tokens=100, latency_ms=1000, started=0.0, ended=1.0) for _ in range(3)]
    r2 = [_Turn(completion_tokens=200, latency_ms=2000, started=1.0, ended=3.0) for _ in range(3)]
    agg, each = llm_throughput([r1, r2])
    assert agg == 300.0 and each == 100.0  # 900 tokens / 3 s ; each call 100 tok/s
    assert llm_throughput([[_Turn(latency_ms=1000, started=0, ended=1)]]) == (None, None)  # no usage reported
    assert llm_throughput([]) == (None, None)


def test_snapshot_is_logged_and_shown(scenarios, tmp_path):
    async def go():
        st = make_stack(scenarios, "drug_effect")
        audit = attach(st, tmp_path)
        await run_event(st, "drug_effect")
        snaps = [e for e in audit.entries() if e["kind"] == "metrics_snapshot"]
        assert len(snaps) == 1
        d = snaps[0]["data"]
        assert d["deliberation_s"] == st.state.metrics.deliberation_s
        assert d["llm_tps_aggregate"] and d["llm_tps_per_agent"]  # the stand-in reports usage
        assert d["event_to_alert_ms"] is not None and d["scenario_id"] == "drug_effect"
        assert d["detector_ms"] is None and d["detector_fps"] is None  # nothing invented before M2
        assert st.state.metrics.llm_tps_aggregate == d["llm_tps_aggregate"]
        agent_msgs = [e for e in audit.entries() if e["kind"] == "agent_message"]
        assert all(e["data"]["completion_tokens"] and e["data"]["tokens_per_s"] for e in agent_msgs)
        assert audit.verify().ok
    asyncio.run(go())


def test_no_usage_means_not_measured(scenarios, tmp_path):
    async def go():
        st = make_stack(scenarios, "drug_effect")
        audit = attach(st, tmp_path)
        # the stand-in always reports usage; strip it with a transport wrapper
        real = st.client._http._transport  # httpx.ASGITransport

        class NoUsage(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                resp = await real.handle_async_request(request)
                body = json.loads(await resp.aread())
                body.pop("usage", None)
                return httpx.Response(resp.status_code, json=body)
        st.client._http = httpx.AsyncClient(transport=NoUsage(), timeout=httpx.Timeout(5))
        await run_event(st, "drug_effect")
        assert st.state.metrics.llm_tps_aggregate is None and st.state.metrics.llm_tps_per_agent is None
        snap = next(e for e in audit.entries() if e["kind"] == "metrics_snapshot")
        assert snap["data"]["llm_tps_aggregate"] is None
    asyncio.run(go())
