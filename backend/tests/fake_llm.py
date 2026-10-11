"""TEST-ONLY stand-in for the Lightning endpoint (OpenAI-compatible /v1). Never imported by product code.

It is NOT a model and says nothing about real model behaviour. It returns schema-valid
AgentMessages derived from each scenario's `expected_hypotheses` so the orchestrator, validation,
convergence and UI can be exercised without vLLM or a GPU:

  - category and confidence come from `expected_hypotheses` (round 1 for the surgical agent in the
    artifact scenario is `unclear`, as prompts/README.md expects; everything else is the expected
    final position in both rounds);
  - the hypothesis is the first sentence of the expected `gist`;
  - evidence cites the expected `cites` that the request allows (the request's own json_schema enum);
  - round 2 answers each other available agent with agree / adds / disagree from their round-1 category.

Failure modes for tests (per agent): bad_source_id, hang, http500, length, banned.

Run as a server for the end-to-end check:
    FAKE_LLM_SCENARIO=true_bleed uvicorn backend.tests.fake_llm:app --port 8765
    curl -X POST localhost:8765/_fake/config -d '{"scenario_id":"drug_effect","delay_s":1.5}'
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DATA = Path(__file__).resolve().parents[2] / "data"
MODEL_ID = "fake-lightning-test"
ROUND1_OVERRIDES = {("artifact", "surgical"): ("unclear", "medium")}  # prompts/README.md self-check table
FAILURES = {"bad_source_id", "hang", "http500", "length", "banned"}


def _scenario_expected(scenario_id: str) -> dict[str, Any]:
    raw = json.loads((DATA / "eval" / f"{scenario_id}.expected.json").read_text())
    return raw["expected_hypotheses"]


class FakeLLM:
    def __init__(self, scenario_id: str = "true_bleed") -> None:
        self.scenario_id = scenario_id
        self.delay_s = 0.0
        self.failures: dict[str, dict[str, Any]] = {}  # agent -> {"mode": str, "times": int | None}
        self.log: list[dict[str, Any]] = []
        self.responses_sent = 0
        self.first_response_at: Optional[float] = None
        self.app = self._build_app()

    # ---- configuration (tests set attributes directly; the e2e check uses POST /_fake/config) ----
    def configure(self, scenario_id: Optional[str] = None, delay_s: Optional[float] = None,
                  failures: Optional[dict[str, Any]] = None, reset_log: bool = False) -> None:
        if scenario_id is not None:
            self.scenario_id = scenario_id
        if delay_s is not None:
            self.delay_s = float(delay_s)
        if failures is not None:
            self.failures = {a: dict(f) for a, f in failures.items()}
        if reset_log:
            self.log.clear()
            self.responses_sent = 0
            self.first_response_at = None

    def fail(self, agent: str, mode: str, times: Optional[int] = None) -> None:
        assert mode in FAILURES
        self.failures[agent] = {"mode": mode, "times": times}

    def requests_for(self, agent: str, round_: Optional[int] = None) -> list[dict[str, Any]]:
        return [r for r in self.log if r["agent"] == agent and (round_ is None or r["round"] == round_)]

    # ---- message generation -------------------------------------------------------------------
    def _message(self, agent: str, round_: int, schema: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, Any]:
        exp = _scenario_expected(self.scenario_id)[agent]
        category, confidence = exp["category"], exp["confidence"]
        if round_ == 1 and (self.scenario_id, agent) in ROUND1_OVERRIDES:
            category, confidence = ROUND1_OVERRIDES[(self.scenario_id, agent)]
        props = schema["properties"]
        allowed = props["evidence"]["items"]["properties"]["source_id"]["enum"]
        cites = [c for c in exp["cites"] if c in allowed][:3] or allowed[:1]
        gist = re.split(r"(?<=[.!?])\s+(?=[A-Z])", exp["gist"])[0]
        msg: dict[str, Any] = {
            "agent": agent, "round": round_, "category": category,
            "hypothesis": gist[:240], "confidence": confidence,
            "evidence": [{"source_id": c, "note": f"{c} supports this reading"} for c in cites],
            "responds_to": [],
        }
        if round_ == 2:
            wanted = props["responds_to"]["items"]["properties"]["agent"]["enum"]
            for o in others:
                if o.get("agent") not in wanted:
                    continue
                theirs = o.get("category")
                stance = "agree" if theirs == category else ("adds" if "unclear" in (theirs, category) else "disagree")
                msg["responds_to"].append({"agent": o["agent"], "stance": stance,
                                           "note": f"Their round-1 view is {theirs}; my reading stays {category}."})
        return msg

    def _apply_failure(self, agent: str, msg: dict[str, Any]) -> tuple[str, Optional[str]]:
        """Returns (content, finish_reason) after applying the configured failure mode."""
        f = self.failures.get(agent)
        if not f:
            return json.dumps(msg), "stop"
        if f.get("times") is not None:
            if f["times"] <= 0:
                return json.dumps(msg), "stop"
            f["times"] -= 1
        mode = f["mode"]
        if mode == "bad_source_id":
            msg["evidence"][0]["source_id"] = "lab-99"
        elif mode == "banned":
            msg["hypothesis"] = "The patient has a haemorrhage and you should transfuse."
        elif mode == "length":
            return json.dumps(msg)[:40], "length"
        return json.dumps(msg), "stop"

    @staticmethod
    def _narrative(user: str) -> str:
        """Test double for the report summary: one fixed sentence per kind of log line, citing that line."""
        words = {"alert": "A blood pressure alert was raised.", "drug_given": "A drug entry was logged.",
                 "agent_message": "An assistant agent reported its reading.",
                 "deliberation_result": "The agents' positions were compared by code."}
        seen: set[str] = set()
        out = []
        for m in re.finditer(r"^\[([^\]]+)\] [^|]*\| (\w+) \|", user, re.M):
            if m.group(2) in words and m.group(2) not in seen:
                seen.add(m.group(2))
                out.append({"text": words[m.group(2)], "cites": [m.group(1)]})
        return json.dumps({"sentences": out[:5] or [{"text": "Nothing happened.", "cites": [re.search(r"^\[([^\]]+)\]", user, re.M).group(1)]}]})

    # ---- the app ---------------------------------------------------------------------------------
    def _build_app(self) -> FastAPI:
        app = FastAPI(title="fake_llm (test only)")

        @app.get("/v1/models")
        async def models() -> dict[str, Any]:
            return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> JSONResponse:
            body = await request.json()
            system, user = body["messages"][0]["content"], body["messages"][1]["content"]
            if body["response_format"]["json_schema"]["name"] == "ReportNarrative":  # post-op report summary (M4a)
                return JSONResponse({"choices": [{"message": {"content": self._narrative(user)}, "finish_reason": "stop"}]})
            agent = re.search(r"You are the (\w+) agent", system).group(1)  # type: ignore[union-attr]
            labels = {m.group(1): m.group(2) for m in re.finditer(r"^([A-Z_]+): (.*)$", user, re.M)}
            round_ = int(labels["ROUND"])
            others = json.loads(labels.get("OTHER_AGENTS", "[]"))
            entry = {"agent": agent, "round": round_, "body": body, "user": user, "system": system,
                     "labels": labels, "received_at": time.monotonic()}
            self.log.append(entry)
            f = self.failures.get(agent)
            if f and f["mode"] == "hang" and (f.get("times") is None or f["times"] > 0):
                if f.get("times") is not None:
                    f["times"] -= 1
                await asyncio.sleep(3600)
            if f and f["mode"] == "http500" and (f.get("times") is None or f["times"] > 0):
                if f.get("times") is not None:
                    f["times"] -= 1
                return JSONResponse({"error": "boom"}, status_code=500)
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            schema = body["response_format"]["json_schema"]["schema"]
            content, finish = self._apply_failure(agent, self._message(agent, round_, schema, others))
            self.responses_sent += 1
            if self.first_response_at is None:
                self.first_response_at = time.monotonic()
            entry["responded_at"] = time.monotonic()
            return JSONResponse({"id": "fake", "object": "chat.completion", "model": body["model"],
                                 "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                              "finish_reason": finish}],
                                 "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": max(1, len(content) // 4)}})

        @app.post("/_fake/config")
        async def config(request: Request) -> dict[str, Any]:
            self.configure(**(await request.json()))
            return {"scenario_id": self.scenario_id, "delay_s": self.delay_s, "failures": self.failures}

        @app.get("/_fake/log")
        async def get_log() -> dict[str, Any]:
            return {"scenario_id": self.scenario_id, "responses_sent": self.responses_sent,
                    "requests": [{"agent": r["agent"], "round": r["round"]} for r in self.log]}

        return app


_default = FakeLLM(os.environ.get("FAKE_LLM_SCENARIO", "true_bleed"))
app = _default.app  # for `uvicorn backend.tests.fake_llm:app`
