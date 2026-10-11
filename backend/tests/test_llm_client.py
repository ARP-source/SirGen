import asyncio
import json

import httpx
import pytest

from backend.agents.llm_client import (DEFAULT_URL, LightningClient, LLMBadResponse, LLMTimeout, LLMUnreachable)

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def mock_client(handler, **kw):
    return LightningClient(base_url="http://lightning.test/v1", transport=httpx.MockTransport(handler), **kw)


def ok(content='{"a":"b"}', finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def test_defaults_and_env(monkeypatch):
    monkeypatch.delenv("LIGHTNING_URL", raising=False)
    monkeypatch.delenv("LIGHTNING_MODEL", raising=False)
    monkeypatch.delenv("AGENT_TIMEOUT_S", raising=False)

    async def go():
        c = LightningClient()
        assert (c.base_url, c.model, c.timeout_s) == (DEFAULT_URL, None, 8.0)
        monkeypatch.setenv("LIGHTNING_URL", "http://gx10:8000/v1/")
        monkeypatch.setenv("LIGHTNING_MODEL", "m1")
        monkeypatch.setenv("AGENT_TIMEOUT_S", "3.5")
        c2 = LightningClient()
        assert (c2.base_url, c2.model, c2.timeout_s) == ("http://gx10:8000/v1", "m1", 3.5)
        await c.aclose(); await c2.aclose()
    asyncio.run(go())


def test_request_shape_and_lazy_model_resolution():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "served-name"}, {"id": "other"}]})
        return ok()

    async def go():
        c = mock_client(handler)
        assert c.model is None and seen == []  # nothing is resolved at construction
        res = await c.chat_json("SYS", "USER", SCHEMA, max_tokens=200)
        assert res.text == '{"a":"b"}' and res.model == "served-name"
        await c.chat_json("SYS", "USER", SCHEMA, max_tokens=256)
        await c.aclose()
    asyncio.run(go())
    assert [s[1] for s in seen] == ["/v1/models", "/v1/chat/completions", "/v1/chat/completions"]  # /models once
    body = seen[1][2]
    assert body["model"] == "served-name" and body["temperature"] == 0.2 and body["max_tokens"] == 200
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["schema"] == SCHEMA


def test_error_classes():
    async def go():
        async def call(handler, **kw):
            c = mock_client(handler, model="m", **kw)
            try:
                return await c.chat_json("s", "u", SCHEMA, max_tokens=10)
            finally:
                await c.aclose()

        def boom(req):
            raise httpx.ConnectError("refused")
        with pytest.raises(LLMUnreachable):
            await call(boom)
        with pytest.raises(LLMBadResponse, match="HTTP 500"):
            await call(lambda r: httpx.Response(500, text="x"))
        with pytest.raises(LLMBadResponse, match="finish_reason=length"):
            await call(lambda r: ok(finish="length"))
        with pytest.raises(LLMBadResponse, match="empty"):
            await call(lambda r: ok(content=""))
        with pytest.raises(LLMBadResponse, match="JSON"):
            await call(lambda r: httpx.Response(200, text="<html>"))
        with pytest.raises(LLMBadResponse, match="no models|model id"):
            c = mock_client(lambda r: httpx.Response(200, json={"data": []}))
            await c.chat_json("s", "u", SCHEMA, max_tokens=10)

        async def slow(req):
            await asyncio.sleep(5)
            return ok()
        with pytest.raises(LLMTimeout, match="timed out after 0.2 s"):
            await call(slow, timeout_s=0.2)
    asyncio.run(go())
