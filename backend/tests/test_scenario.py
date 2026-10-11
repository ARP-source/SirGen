import json
import shutil
from pathlib import Path

import pytest

from backend.scenario import interpolate, load_scenario, series_from_keyframes

KF = [{"t_s": 0, "hr": 60, "sbp": 100, "dbp": 50, "map": 70, "spo2": 99},
      {"t_s": 10, "hr": 70, "sbp": 120, "dbp": 60, "map": 80, "spo2": 97}]


def test_interpolate_midpoint_and_clamp():
    assert interpolate(KF, 5) == {"hr": 65, "sbp": 110, "dbp": 55, "map": 75, "spo2": 98}
    assert interpolate(KF, 2.5)["map"] == pytest.approx(72.5)
    assert interpolate(KF, -3)["hr"] == 60
    assert interpolate(KF, 99)["hr"] == 70


def test_interpolate_rejects_unsorted_keyframes():
    with pytest.raises(ValueError):
        interpolate(list(reversed(KF)), 1)


def test_series_is_1hz_and_hits_keyframes():
    s = series_from_keyframes(KF, 10, "test", "simulated")
    assert len(s) == 11
    assert s.at(0)["map"] == 70 and s.at(10)["map"] == 80 and s.at(3)["map"] == 73


def test_drug_effect_uses_labelled_fallback_without_csv(tmp_path, data_dir):
    (tmp_path / "scenarios").mkdir()
    shutil.copy(data_dir / "scenarios" / "drug_effect.json", tmp_path / "scenarios")
    sc = load_scenario(tmp_path / "scenarios" / "drug_effect.json", tmp_path)
    assert sc.series.source_kind == "approximate"
    assert "approximate" in sc.series.source
    assert sc.series.at(30)["map"] == 61
    assert sc.baseline["fires_at_t_s"] == 81


def test_drug_effect_uses_vitaldb_csv_when_present(tmp_path, data_dir):
    (tmp_path / "scenarios").mkdir()
    shutil.copy(data_dir / "scenarios" / "drug_effect.json", tmp_path / "scenarios")
    rows = ["time_s,HR,ART_SBP,ART_DBP,ART_MBP,SPO2"]
    for t in range(300, 600):
        rows.append(f"{t},{60 + (t % 3)},{t - 250},{40},{t - 300},{99}" if t % 7 else f"{t},,,,,")
    (tmp_path / "vitaldb_case59_tracks.csv").write_text("\n".join(rows))
    sc = load_scenario(tmp_path / "scenarios" / "drug_effect.json", tmp_path)
    assert sc.series.source_kind == "real_time_shifted"
    assert "time-shifted" in sc.series.source
    # excerpt_real_start_s 400 -> t_s 0 is real time_s 400; the fixture's ART_MBP is time_s - 300
    assert sc.series.at(0)["map"] == pytest.approx(100)
    assert sc.series.at(10)["map"] == pytest.approx(110)
    assert sc.baseline["fires_at_t_s"] is not None


def test_event_type_and_alert_level(scenarios):
    assert scenarios["artifact"].event_type == "art_spike"
    assert scenarios["artifact"].alert_level == "quiet"
    assert scenarios["true_bleed"].event_type == "map_drop"
    assert scenarios["true_bleed"].alert_level == "banner"


def test_null_nibp_entries_are_dropped(scenarios):
    assert [n["source_id"] for n in scenarios["drug_effect"].nibp] == ["vit-nibp-01", "vit-nibp-02"]
    assert [n["source_id"] for n in scenarios["artifact"].nibp] == ["vit-nibp-01", "vit-nibp-02"]
