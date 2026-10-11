#!/usr/bin/env python3
"""End-to-end check of M3 through the RUNNING backend, against the TEST-ONLY stand-in LLM.

Starts (and always kills) three local servers:
  - backend/tests/fake_llm.py   on :8765  (stand-in for Lightning; NOT a model)
  - the backend                  on :7000  (LIGHTNING_URL pointed at the stand-in)
  - vite preview (only with --shots, needs `npm run build` first) on :5173

For each scenario it selects, plays, lets the scenario event fire, follows WS /ws/state and checks
that the alert comes first, round 1 lands before round 2, and the outcome and context_status fill in.
With --shots it saves dashboard screenshots through the Chrome DevTools protocol (headless_shell)
to docs/screenshots/m3_*.png.

    python scripts/e2e_m3.py [--shots] [--scenarios true_bleed,drug_effect,artifact]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
BACKEND, FAKE, VITE, CDP = 7000, 8765, 5173, 9222
EXPECTED = {"true_bleed": ("split", None), "drug_effect": ("converged", "drug_effect"), "artifact": ("converged", "artifact")}
CHROME = next((str(p) for p in sorted(Path("/opt/pw-browsers").glob("chromium_headless_shell-*/chrome-linux/headless_shell"))), None)
procs: list[subprocess.Popen] = []


def spawn(cmd: list[str], env: Optional[dict[str, str]] = None, name: str = "") -> subprocess.Popen:
    log = open(f"/tmp/e2e_m3_{name or cmd[0]}.log", "w")
    p = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
    procs.append(p)
    return p


def kill_all() -> None:
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(0.8)
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def wait_http(url: str, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


class Shooter:
    """Chrome DevTools protocol, no extra packages: headless_shell + websockets."""

    def __init__(self) -> None:
        spawn([CHROME, "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={CDP}", "--hide-scrollbars",
               "--window-size=1920,1080", "--lang=en-US", "about:blank"],
              env={"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "LANGUAGE": "en_US"},  # the sandbox's locale is en-US@posix, which breaks Intl.NumberFormat
              name="chrome")
        wait_http(f"http://127.0.0.1:{CDP}/json/version")
        tab = httpx.put(f"http://127.0.0.1:{CDP}/json/new?about:blank", timeout=5).json()
        self.ws = connect(tab["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024).__enter__()
        self.n = 0
        self.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == self.n:
                return msg.get("result", {})

    def goto(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        time.sleep(1.5)

    def shot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))
        print(f"   screenshot {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KiB)")


def post(path: str, **kw: Any) -> dict[str, Any]:
    r = httpx.post(f"http://127.0.0.1:{BACKEND}{path}", timeout=10, **kw)
    r.raise_for_status()
    return r.json()


def fake_config(**cfg: Any) -> None:
    httpx.post(f"http://127.0.0.1:{FAKE}/_fake/config", json=cfg, timeout=5).raise_for_status()


def follow(ws, until, timeout: float, on_state=None) -> list[dict[str, Any]]:
    """Collect WS states until `until(state)` is true. Returns every state seen."""
    seen: list[dict[str, Any]] = []
    end = time.time() + timeout
    while time.time() < end:
        try:
            st = json.loads(ws.recv(timeout=max(0.1, end - time.time())))
        except TimeoutError:
            break
        seen.append(st)
        if on_state:
            on_state(st)
        if until(st):
            return seen
    raise AssertionError("timed out waiting for the state; last = " + json.dumps(
        {k: seen[-1].get(k) for k in ("clock_s", "deliberation")} if seen else {}))


def run_scenario(sid: str, shooter: Optional[Shooter], shot_dir: Path) -> None:
    exp_outcome, exp_cat = EXPECTED[sid]
    print(f"\n== {sid}: expect {exp_outcome}" + (f" ({exp_cat})" if exp_cat else ""))
    fake_config(scenario_id=sid, delay_s=2.0, failures={}, reset_log=True)  # 2 s per call: UI shows round 1 first
    post(f"/control/scenario/{sid}")
    mid_shot = {"done": False}
    with connect(f"ws://127.0.0.1:{BACKEND}/ws/state") as ws:
        post("/control/play")
        alert_state: list[dict[str, Any]] = []

        def watch(st: dict[str, Any]) -> None:
            ev = st.get("event")
            if ev and not alert_state:
                alert_state.append(st)
            msgs = [a["message"]["round"] if a["message"] else 0 for a in st["agents"].values()]
            if shooter and sid == "true_bleed" and not mid_shot["done"] and msgs == [1, 1, 1] and st["deliberation"]["round"] == 2:
                shooter.shot(shot_dir / "m3_mid_deliberation.png")
                mid_shot["done"] = True
        seen = follow(ws, lambda s: s["deliberation"]["status"] in ("done", "unavailable"), timeout=150, on_state=watch)
        final = seen[-1]
        # 1. the alert state was published before any agent produced anything
        a = alert_state[0]
        assert a["event"]["alert_shown_at"] is not None and a["event"]["alert_text"], "no alert in the first event state"
        assert all(x["message"] is None for x in a["agents"].values()), "agent output present in the alert state"
        # 2. round 1 was visible before round 2
        rounds = [tuple(x["message"]["round"] if x["message"] else 0 for x in s["agents"].values()) for s in seen]
        assert (1, 1, 1) in rounds and rounds.index((1, 1, 1)) < rounds.index((2, 2, 2)), f"round order: {sorted(set(rounds))}"
        # 3. outcome, status, metrics
        d = final["deliberation"]
        assert (d["outcome"], d["shared_category"]) == (exp_outcome, exp_cat), d
        assert final["event"]["context_status"] == "available", final["event"]
        assert final["metrics"]["deliberation_s"] and final["metrics"]["deliberation_s"] > 3.5, final["metrics"]
        print(f"   alert t={a['event']['alert_shown_at']} s, event_to_alert_ms={final['event']['event_to_alert_ms']}")
        print(f"   round states seen: {sorted(set(rounds))}")
        print(f"   outcome={d['outcome']} shared={d['shared_category']} context={final['event']['context_status']} "
              f"deliberation_s={final['metrics']['deliberation_s']}")
        print(f"   question: {d['team_question']}")
        for name, slot in final["agents"].items():
            m = slot["message"]
            print(f"   {name:10s} {m['category']:17s} {m['confidence']:6s} cites {[e['source_id'] for e in m['evidence']]}")
        time.sleep(0.6)
        if shooter:
            shooter.goto(f"http://127.0.0.1:{VITE}/dashboard")
            shooter.shot(shot_dir / f"m3_{sid}_{exp_outcome}.png")
        post("/control/pause")


def run_presenter_trigger_and_failure(shooter: Optional[Shooter], shot_dir: Path) -> None:
    print("\n== presenter trigger with one agent failing validation (stand-in cites an id that does not exist)")
    fake_config(scenario_id="drug_effect", delay_s=0.0, failures={"record": {"mode": "bad_source_id", "times": None}}, reset_log=True)
    post("/control/scenario/drug_effect")
    post("/control/play")
    time.sleep(3)
    ev = post("/control/trigger")["event"]
    with connect(f"ws://127.0.0.1:{BACKEND}/ws/state") as ws:
        final = follow(ws, lambda s: s["deliberation"]["status"] in ("done", "unavailable"), timeout=30)[-1]
    rec = final["agents"]["record"]
    assert ev["trigger"] == "presenter" and final["event"]["context_status"] == "available"
    assert rec["state"] == "unavailable" and "lab-99" in rec["reason"] and rec["message"] is None
    assert final["deliberation"]["unavailable_agents"] == ["record"]
    print(f"   record: {rec['status']}: {rec['reason']}")
    print(f"   others completed; outcome={final['deliberation']['outcome']}; context={final['event']['context_status']}")
    if shooter:
        shooter.goto(f"http://127.0.0.1:{VITE}/dashboard")
        shooter.shot(shot_dir / "m3_agent_unavailable.png")
    post("/control/pause")
    post("/control/reset")
    with connect(f"ws://127.0.0.1:{BACKEND}/ws/state") as ws:
        s = json.loads(ws.recv(timeout=5))
    assert all(a["state"] == "idle" and a["message"] is None for a in s["agents"].values()) and s["deliberation"]["outcome"] is None
    print("   reset cleared agents and deliberation")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", action="store_true")
    ap.add_argument("--scenarios", default="true_bleed,drug_effect,artifact")
    args = ap.parse_args()
    shot_dir = ROOT / "docs" / "screenshots"
    try:
        spawn([sys.executable, "-m", "uvicorn", "backend.tests.fake_llm:app", "--port", str(FAKE)], name="fake_llm")
        wait_http(f"http://127.0.0.1:{FAKE}/v1/models")
        spawn([sys.executable, "-m", "uvicorn", "backend.app:app", "--port", str(BACKEND), "--timeout-graceful-shutdown", "2"],
              env={"LIGHTNING_URL": f"http://127.0.0.1:{FAKE}/v1", "AGENT_TIMEOUT_S": "8"}, name="backend")
        wait_http(f"http://127.0.0.1:{BACKEND}/health")
        print("backend health:", json.dumps({k: v for k, v in httpx.get(f"http://127.0.0.1:{BACKEND}/health").json().items()
                                             if k in ("milestone", "agents", "lightning")}))
        shooter = None
        if args.shots:
            assert CHROME, "no headless_shell found under /opt/pw-browsers"
            spawn(["npm", "run", "preview", "--prefix", "frontend"], name="vite")
            wait_http(f"http://127.0.0.1:{VITE}/")
            shooter = Shooter()
            shooter.goto(f"http://127.0.0.1:{VITE}/dashboard")
        for sid in args.scenarios.split(","):
            run_scenario(sid, shooter, shot_dir)
        run_presenter_trigger_and_failure(shooter, shot_dir)
        print("\nE2E OK (stand-in LLM only: nothing here says how the real model behaves)")
        return 0
    finally:
        kill_all()


if __name__ == "__main__":
    sys.exit(main())
