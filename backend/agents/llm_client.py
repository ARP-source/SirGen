"""Small async client for Lightning's OpenAI-compatible endpoint (vLLM, default :8000).

- Structured output through `response_format: {"type": "json_schema", ...}`.
- Thinking off through `chat_template_kwargs: {"enable_thinking": false}`.
- One overall timeout per call (default 8 s, `AGENT_TIMEOUT_S`) that covers connect,
  the lazy model lookup, generation and reading the body.
- No retry here: the orchestrator retries validation failures once and decides what a
  timeout means. Errors are classified so the UI can say why an agent is unavailable.

Environment:
  LIGHTNING_URL    base URL including /v1   (default http://localhost:8000/v1)
  LIGHTNING_MODEL  model id                 (default: first id from GET /v1/models, resolved lazily)
  AGENT_TIMEOUT_S  seconds per call         (default 8)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

log = logging.getLogger("sentinel.llm")

DEFAULT_URL = "http://localhost:8000/v1"
DEFAULT_TIMEOUT_S = 8.0


class LLMError(Exception):
    """Base class. `str(exc)` is a short reason that is safe to show in the UI."""


class LLMTimeout(LLMError):
    """The call did not finish within the timeout."""


class LLMUnreachable(LLMError):
    """Could not connect to the endpoint (not running, wrong URL, network down)."""


class LLMBadResponse(LLMError):
    """The server answered, but not with usable content (HTTP error, bad JSON, empty,
    or finish_reason == "length", which prompts/README.md counts as a validation failure)."""


@dataclass(frozen=True)
class ChatResult:
    text: str
    finish_reason: Optional[str]
    latency_s: float
    model: str
    completion_tokens: Optional[int] = None  # from the server's `usage`, when it reports it
    prompt_tokens: Optional[int] = None


def _int_or_none(v: object) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


class LightningClient:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None,
                 timeout_s: Optional[float] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.base_url = (base_url or os.environ.get("LIGHTNING_URL") or DEFAULT_URL).rstrip("/")
        self._model: Optional[str] = model or os.environ.get("LIGHTNING_MODEL") or None
        self.timeout_s = float(timeout_s if timeout_s is not None
                               else os.environ.get("AGENT_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        self._http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(self.timeout_s))
        self._model_lock = asyncio.Lock()

    @property
    def model(self) -> Optional[str]:
        """The model id once known (set explicitly or resolved from /models), else None."""
        return self._model

    async def aclose(self) -> None:
        await self._http.aclose()

    async def resolve_model(self) -> str:
        """LIGHTNING_MODEL if set, else the first id served by GET /models (cached on success)."""
        if self._model:
            return self._model
        async with self._model_lock:
            if self._model:
                return self._model
            data = await self._request("GET", "/models")
            try:
                self._model = str(data["data"][0]["id"])
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMBadResponse("GET /models returned no model id") from exc
            log.info("resolved model id %s from %s/models", self._model, self.base_url)
            return self._model

    async def chat_json(self, system: str, user: str, schema: dict[str, Any], *, name: str = "AgentMessage",
                        max_tokens: int, temperature: float = 0.2) -> ChatResult:
        """One chat completion constrained to `schema`. Raises LLMTimeout, LLMUnreachable
        or LLMBadResponse. A cancelled caller cancels the HTTP request."""
        try:
            return await asyncio.wait_for(self._chat(system, user, schema, name, max_tokens, temperature),
                                          timeout=self.timeout_s)
        except asyncio.TimeoutError as exc:
            raise LLMTimeout(f"timed out after {self.timeout_s:g} s") from exc

    # ---- internals -------------------------------------------------------------------------
    async def _chat(self, system: str, user: str, schema: dict[str, Any], name: str,
                    max_tokens: int, temperature: float) -> ChatResult:
        started = time.perf_counter()
        model = await self.resolve_model()
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "strict": True, "schema": schema}},
        }
        data = await self._request("POST", "/chat/completions", json=body)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponse("response has no choices[0].message.content") from exc
        if finish == "length":
            raise LLMBadResponse(f"finish_reason=length at max_tokens={max_tokens}")
        if not isinstance(text, str) or not text.strip():
            raise LLMBadResponse("empty completion")
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return ChatResult(text=text, finish_reason=finish, latency_s=time.perf_counter() - started, model=model,
                          completion_tokens=_int_or_none(usage.get("completion_tokens")),
                          prompt_tokens=_int_or_none(usage.get("prompt_tokens")))

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            res = await self._http.request(method, self.base_url + path, **kwargs)
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"timed out after {self.timeout_s:g} s") from exc
        except httpx.TransportError as exc:  # connect errors, resets, DNS, protocol errors
            raise LLMUnreachable(f"cannot reach {self.base_url}: {type(exc).__name__}") from exc
        if res.status_code >= 400:
            raise LLMBadResponse(f"HTTP {res.status_code} from {path}")
        try:
            return res.json()
        except ValueError as exc:
            raise LLMBadResponse(f"{path} did not return JSON") from exc
