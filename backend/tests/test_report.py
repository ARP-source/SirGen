"""Post-op report: deterministic sections from the log; narrative validated; failures never replaced by text."""
import asyncio
import json
import re

import httpx

from backend import report as R
from backend.agents.llm_client import LightningClient

from .conftest import DATA, make_stack
from .test_audit_hooks import attach
from .test_orchestrator import run_event

PATIENT = json.loads((DATA / "patient" / "patient_record.json").read_text())
PROMPT = (DATA.parent / "prompts" / "report.md").read_text()


def narrative_client(handler):
    """A client whose server is a plain function body -> content string (TEST ONLY, not a model)."""
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "stub"}]})
        body = json.loads(request.content)
        content = handler(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})
    return LightningClient(base_url="http://stub/v1", timeout_s=5, transport=httpx.MockTransport(serve))


def allowed(body):
    return body["response_format"]["json_schema"]["schema"]["properties"]["sentences"]["items"]["properties"]["cites"]["items"]["enum"]


def good_handler(body):
    ids = allowed(body)
    alert = next(i for i in ids if i.startswith("log-"))
    return json.dumps({"sentences": [{"text": "A blood pressure alert was raised during the replayed segment.", "cites": [alert]}]})


async def ran(scenarios, tmp_path, sid="drug_effect"):
    st = make_stack(scenarios, sid)
    audit = attach(st, tmp_path)
    await run_event(st, sid)
    return st, audit


def test_report_has_every_section_and_logs_itself(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        n = audit.count
        rep = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT, out_dir=tmp_path)
        h = rep.html_text
        for part in ("DRAFT", "Procedure", "Alerts and what", "Medications given", "Cuff blood-pressure", "Chart records referred to",
                     "Presenter and team actions", "Log integrity", "chain verified", "Fixed blood-pressure rule"):
            assert part in h, part
        assert rep.narrative and rep.narrative[0]["cites"][0].startswith("log-")
        assert "Composite" in h or "composite" in h
        assert "propofol" in h.lower()  # the drug log made it in
        assert "ev-1" in h and "converged" in h
        assert (tmp_path / f"report-t-run{rep.run}.html").read_text() == h
        last = audit.entries()[-1]
        assert audit.count == n + 1 and last["kind"] == "report_generated" and last["data"]["sha256"] == rep.sha256
        assert audit.verify().ok
    asyncio.run(go())


def test_report_without_a_model_is_complete_and_says_so(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        dead = LightningClient(base_url="http://127.0.0.1:9/v1", timeout_s=1)
        rep = await R.generate(audit, dead, PATIENT, PROMPT)
        await dead.aclose()
        assert rep.narrative is None and R.NARRATIVE_UNAVAILABLE in rep.html_text
        assert "Alerts and what" in rep.html_text and "propofol" in rep.html_text.lower()
        assert audit.entries()[-1]["data"]["narrative"] == "unavailable"
    asyncio.run(go())


def test_narrative_retries_once_then_gives_up(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        calls = []

        def bad(body):
            calls.append(1)
            return json.dumps({"sentences": [{"text": "The patient has a haemorrhage.", "cites": [allowed(body)[0]]}]})
        rep = await R.generate(audit, narrative_client(bad), PATIENT, PROMPT)
        assert len(calls) == 2 and rep.narrative is None and "banned" in rep.narrative_note

        calls.clear()

        def flaky(body):
            calls.append(1)
            return "not json" if len(calls) == 1 else good_handler(body)
        rep = await R.generate(audit, narrative_client(flaky), PATIENT, PROMPT)
        assert len(calls) == 2 and rep.narrative
    asyncio.run(go())


def test_narrative_validation_rules(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        facts = R.extract_facts(audit.entries())
        lines = R.fact_lines(facts, {e["source_id"]: e for e in R.flatten_chart(PATIENT)})
        alert_id = next(i for i in lines if i.startswith("log-") and "| alert |" in lines[i])
        nums = re.findall(r"\d+(?:\.\d+)?", lines[alert_id])
        ok = json.dumps({"sentences": [{"text": f"Alert with value {nums[0]}.", "cites": [alert_id]}]})
        assert R.check_narrative(ok, lines)[0]["cites"] == [alert_id]
        cases = {
            "unknown id": {"text": "Fine.", "cites": ["log-9999"]},
            "no cites": {"text": "Fine.", "cites": []},
            "invented number": {"text": "MAP fell to 31337.", "cites": [alert_id]},
            "banned wording": {"text": "You should recommend a transfusion.", "cites": [alert_id]},
            "too long": {"text": "x" * 400, "cites": [alert_id]},
            "video claim citing an alert": {"text": "The video model logged the Critical View of Safety.", "cites": [alert_id]},
            "agent claim citing an alert": {"text": "The anesthesia agent suggested a drug effect.", "cites": [alert_id]},
            "step claim citing an alert": {"text": "The procedure step changed to clipping.", "cites": [alert_id]},
        }
        for name, s in cases.items():
            try:
                R.check_narrative(json.dumps({"sentences": [s]}), lines)
            except R.InvalidNarrative:
                continue
            raise AssertionError(f"accepted: {name}")
        for junk in ("[]", "{}", "nope", json.dumps({"sentences": []})):
            try:
                R.check_narrative(junk, lines)
            except R.InvalidNarrative:
                continue
            raise AssertionError(f"accepted: {junk}")
    asyncio.run(go())


def test_tampered_log_is_stated_in_the_report(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        ls = audit.path.read_text().splitlines()
        e = json.loads(ls[3])
        e["summary"] = "edited afterwards"
        ls[3] = json.dumps(e, sort_keys=True)
        audit.path.write_text("\n".join(ls) + "\n")
        rep = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT)
        assert not rep.verification.ok and "CHAIN BROKEN" in rep.html_text
    asyncio.run(go())


def test_split_scenario_puts_the_team_question_in_the_report(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path, "true_bleed")
        rep = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT)
        assert "split" in rep.html_text and "Question put to the team" in rep.html_text
    asyncio.run(go())


def test_report_covers_only_the_requested_run(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        st.player.select("artifact")  # run 2, no alert
        rep2 = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT)
        assert rep2.run == 2 and "No alert fired in this run" in rep2.html_text
        rep1 = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT, run=1)
        assert rep1.run == 1 and "ev-1" in rep1.html_text
        try:
            await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT, run=99)
        except ValueError:
            return
        raise AssertionError("run 99 should not exist")
    asyncio.run(go())


def test_html_escapes_log_text(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        audit.record("voice_qa", "q", {"question": "<script>alert(1)</script>", "answer": "a & b", "source_ids": ["drug-01"]},
                     actor="voice")
        rep = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT)
        assert "<script>alert(1)</script>" not in rep.html_text and "&lt;script&gt;" in rep.html_text
    asyncio.run(go())


def test_fake_llm_report_double_produces_valid_narratives(scenarios, tmp_path):
    """The TEST-ONLY fake server also answers the report prompt, so the full app can be run without a model."""
    async def go():
        import httpx as hx
        st, audit = await ran(scenarios, tmp_path)
        rep = await R.generate(audit, st.client, PATIENT, PROMPT)
        assert rep.narrative and len(rep.narrative) >= 3, rep.narrative_note
        assert "converged" in rep.html_text
        del hx
    asyncio.run(go())


def test_aftercare_is_a_cited_clinician_draft_and_coding_is_off_by_default(scenarios, tmp_path, monkeypatch):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        monkeypatch.delenv("SENTINEL_REPORT_CODING", raising=False)
        monkeypatch.delenv("SENTINEL_REPORT_AFTERCARE", raising=False)
        off = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT, out_dir=tmp_path)
        assert off.aftercare is None and "Aftercare" not in off.html_text and off.coding is None
        monkeypatch.setenv("SENTINEL_REPORT_AFTERCARE", "1")
        rep = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT, out_dir=tmp_path)
        h = rep.html_text
        assert "Aftercare and medication: Draft for clinician review" in h and "Medication reconciliation" in h
        assert "meds-01" in h and "apixaban" in h  # every medicine line names its chart record
        for gone in ("Insurance coding", "K80.00", "47562", "I48.0", "SAGES", "American College of Surgeons", "ERAS",
                     "When to call", "Small port-site incisions", "Strasberg", "bile duct injury"):
            assert gone not in h, gone
        assert rep.coding is None
        monkeypatch.setenv("SENTINEL_REPORT_CODING", "1")
        flagged = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT, out_dir=tmp_path)
        assert "Insurance coding" in flagged.html_text and flagged.coding is not None
    asyncio.run(go())
