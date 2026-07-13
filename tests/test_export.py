import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import export_usage  # noqa: E402


def test_last_dates_length_and_order():
    ds = export_usage.last_dates(7)
    assert len(ds) == 7
    assert ds == sorted(ds)          # oldest -> newest
    assert ds[-1] >= ds[0]


def test_build_rows_shape(monkeypatch, tmp_path):
    # Point the module at fixtures with known daily arrays and history.
    cc = tmp_path / "cc.json"
    codex = tmp_path / "codex.json"
    hist = tmp_path / "hist"
    cc.write_text('{"daily":[1,2,3,4,5,6,7]}')
    codex.write_text('{"available":true,"daily":[10,20,30,40,50,60,70]}')
    hist.write_text("")   # empty history -> blank percentages
    monkeypatch.setattr(export_usage, "CC_SUMMARY", cc)
    monkeypatch.setattr(export_usage, "CODEX_SUMMARY", codex)
    monkeypatch.setattr(export_usage, "HISTORY", hist)

    rows = export_usage.build_rows()
    assert len(rows) == 7
    assert rows[-1]["cc_tokens"] == 7          # newest day = last element
    assert rows[-1]["codex_tokens"] == 70
    assert set(rows[0].keys()) == {"date", "cc_tokens", "codex_tokens", "session_pct", "weekly_pct"}


def test_history_last_sample_wins(monkeypatch, tmp_path):
    hist = tmp_path / "hist"
    # two samples same day: 1000000000 -> local day X, later epoch overrides
    hist.write_text("1000000000 10 20\n1000000000 55 66\n")
    monkeypatch.setattr(export_usage, "HISTORY", hist)
    by_day = export_usage.history_by_day()
    assert len(by_day) == 1
    assert list(by_day.values())[0] == (55, 66)
