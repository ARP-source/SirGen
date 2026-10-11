from pathlib import Path

import pytest

from backend.scenario import Scenario, load_all

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


@pytest.fixture()
def scenarios() -> dict[str, Scenario]:
    return load_all(DATA)


# ---- M3 helpers: orchestrator wired to the TEST-ONLY fake_llm over an in-process ASGI transport ----
import json  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any, Optional  # noqa: E402

import httpx  # noqa: E402

from backend.agents.llm_client import LightningClient  # noqa: E402
from backend.agents.orchestrator import Orchestrator  # noqa: E402
from backend.state import StateStore  # noqa: E402
from backend.telemetry import ScenarioPlayer  # noqa: E402

from .fake_llm import FakeLLM  # noqa: E402
from .scripted_video import ScriptedScene  # noqa: E402


@dataclass
class Stack:
    store: StateStore
    player: ScenarioPlayer
    orch: Orchestrator
    fake: FakeLLM
    client: LightningClient
    scene: Optional[ScriptedScene] = None

    def fire(self, at: Optional[float] = None) -> None:
        """Advance the scenario clock to its event so the scheduled alert fires (paused clock, no real time)."""
        ev_t = self.player.scenario.event_t_s if at is None else at
        self.player.clock = float(ev_t)
        if self.scene is not None:
            self.store.update(phase=self.scene.phase_state(float(ev_t)), scene=self.scene.view())
        self.player.tick(0.0)

    @property
    def state(self):
        return self.store.state


def make_stack(scenarios: dict, scenario_id: str, *, timeout_s: float = 5.0, delay_s: float = 0.0,
               fake: Optional[FakeLLM] = None, scripted_video: bool = True, **orch_kwargs: Any) -> Stack:
    """Must be called inside a running event loop (the client and orchestrator create loop objects)."""
    fake = fake or FakeLLM(scenario_id)
    fake.configure(scenario_id=scenario_id, delay_s=delay_s)
    client = LightningClient(base_url="http://fake-lightning/v1", timeout_s=timeout_s,
                             transport=httpx.ASGITransport(app=fake.app))
    store = StateStore()
    player = ScenarioPlayer(store, scenarios, initial=scenario_id)
    patient = json.loads((DATA / "patient" / "patient_record.json").read_text())
    phase_json = json.loads((DATA / "procedure" / "phase_rules.json").read_text())
    orch_kwargs.setdefault("poll_s", 0.01)
    scene = ScriptedScene(scenarios[scenario_id], phase_json) if scripted_video else None
    orch = Orchestrator(store, player, patient, phase_json, client=client, scene=scene, **orch_kwargs)
    player.listener = orch
    return Stack(store, player, orch, fake, client, scene)


@pytest.fixture(autouse=True)
def _audit_dir(tmp_path, monkeypatch):
    """Tests that start the real app write their audit log under tmp, never into the repo."""
    import backend.app as app_module
    monkeypatch.setattr(app_module, "AUDIT_DIR", tmp_path / "audit")
