"""The agents never read a script: guards for what a reader of the repo would check first."""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT_FIELDS = ("surgical_events", "agent_evidence", "expected_hypotheses", "expected_convergence",
                 "expected_sentinel_output", "ground_truth_cause", "scoring", "instrument_timeline",
                 "procedure_phase_at_event")


def test_scenario_files_hold_only_replay_data():
    for path in sorted((REPO / "data" / "scenarios").glob("*.json")):
        raw = json.loads(path.read_text())
        assert not set(raw) & set(SCRIPT_FIELDS), path.name
        assert raw["alert_level"] in ("banner", "quiet") and raw["event_type"] in ("map_drop", "art_spike")


def test_product_code_never_reads_expected_answers_or_scripted_events():
    pattern = re.compile(r"data/eval|[\"\']eval[\"\']|\.expected\.json|surgical_script|tests/fixtures|"
                         r"\[\"(?:%s)\"\]|get\(\"(?:%s)\"" % ("|".join(SCRIPT_FIELDS), "|".join(SCRIPT_FIELDS)))
    files = [p for d in ("backend", "vision", "scripts") for p in (REPO / d).rglob("*.py")
             if "tests" not in p.parts and "__pycache__" not in p.parts]
    assert len(files) > 15
    for p in files:
        hit = pattern.search(p.read_text())
        assert hit is None, f"{p.relative_to(REPO)} reads {hit.group(0)}"


def test_the_record_agent_has_no_subset_mode():
    text = (REPO / "backend" / "agents" / "evidence.py").read_text() + (REPO / "backend" / "agents" / "orchestrator.py").read_text()
    assert "RECORD_EVIDENCE" not in text and "record_mode" not in text
