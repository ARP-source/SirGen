"""Audit log: hash chain, tamper detection, degraded mode."""
import json

from backend.audit import GENESIS, AuditLog, entry_hash, verify_file


def make(tmp_path, n=5):
    log = AuditLog(tmp_path, session_id="t")
    for i in range(n):
        log.record("control", f"action {i}", {"i": i}, actor="presenter", clock_s=float(i), scenario_id="drug_effect")
    return log


def lines(log):
    return log.path.read_text().splitlines()


def write(log, new_lines):
    log.path.write_text("\n".join(new_lines) + "\n")


def test_chain_links_and_verifies(tmp_path):
    log = make(tmp_path)
    es = log.entries()
    assert [e["seq"] for e in es] == [1, 2, 3, 4, 5] and es[0]["prev_hash"] == GENESIS
    assert all(es[i]["prev_hash"] == es[i - 1]["hash"] for i in range(1, 5))
    assert all(entry_hash(e) == e["hash"] for e in es)
    v = log.verify()
    assert v.ok and v.entries == 5 and v.head_hash == log.head_hash == es[-1]["hash"]
    assert es[0]["id"] == "log-1" and es[2]["clock_s"] == 2.0 and es[0]["scenario_id"] == "drug_effect"


def test_file_matches_memory(tmp_path):
    log = make(tmp_path)
    assert [json.loads(l)["hash"] for l in lines(log)] == [e["hash"] for e in log.entries()]


def test_modified_entry_is_found(tmp_path):
    log = make(tmp_path)
    ls = lines(log)
    e = json.loads(ls[2])
    e["summary"] = "action changed"
    ls[2] = json.dumps(e, sort_keys=True)
    write(log, ls)
    v = log.verify()
    assert not v.ok and v.first_bad_seq == 3 and "modified" in v.reason


def test_modified_and_rehashed_entry_breaks_the_next_link(tmp_path):
    log = make(tmp_path)
    ls = lines(log)
    e = json.loads(ls[2])
    e["summary"] = "action changed"
    e["hash"] = entry_hash(e)  # a careful editor re-hashes the entry...
    ls[2] = json.dumps(e, sort_keys=True)
    write(log, ls)
    v = log.verify()  # ...but entry 4 still points at the old hash
    assert not v.ok and v.first_bad_seq == 4 and "does not link" in v.reason


def test_deleted_middle_entry_is_found(tmp_path):
    log = make(tmp_path)
    ls = lines(log)
    del ls[1]
    write(log, ls)
    v = log.verify()
    assert not v.ok and v.first_bad_seq == 2 and "numbering" in v.reason


def test_reordered_entries_are_found(tmp_path):
    log = make(tmp_path)
    ls = lines(log)
    ls[1], ls[2] = ls[2], ls[1]
    write(log, ls)
    assert not log.verify().ok


def test_truncated_tail_is_found_against_the_running_head(tmp_path):
    log = make(tmp_path)
    write(log, lines(log)[:3])
    assert verify_file(log.path).ok  # the file alone is still a valid chain...
    v = log.verify()  # ...but the running system knows it wrote 5
    assert not v.ok and v.entries == 3 and "removed from the end" in v.reason


def test_garbage_and_missing_file(tmp_path):
    log = make(tmp_path)
    write(log, lines(log)[:2] + ["not json"])
    assert not log.verify().ok
    log.path.unlink()
    assert not log.verify().ok


def test_info_for_the_screen(tmp_path):
    log = make(tmp_path, 3)
    info = log.info()
    assert info["entries"] == 3 and info["verified"] is None and info["status"] == "ok" and info["last_kind"] == "control"
    log.verify()
    info = log.info()
    assert info["verified"] is True and info["verified_entries"] == 3


def test_on_change_fires_for_each_entry(tmp_path):
    seen = []
    log = AuditLog(tmp_path, session_id="t", on_change=lambda a: seen.append(a.count))
    log.record("control", "a")
    log.record("control", "b")
    assert seen == [1, 2]


def test_unwritable_directory_degrades_but_keeps_working(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = AuditLog(blocker / "sub", session_id="t")  # cannot create the directory
    log.record("control", "still recorded in memory")
    assert log.status == "degraded" and log.count == 1 and "cannot open" in log.error
    assert not log.verify().ok


def test_unknown_kind_rejected(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        AuditLog(tmp_path, session_id="t").record("made_up", "x")
