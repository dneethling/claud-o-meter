import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_code_usage as cc  # noqa: E402


def _rec(mid, rid, ts, model, inp, out, cr, ccr):
    return json.dumps({
        "timestamp": ts,
        "requestId": rid,
        "message": {"id": mid, "model": model,
                    "usage": {"input_tokens": inp, "output_tokens": out,
                              "cache_read_input_tokens": cr,
                              "cache_creation_input_tokens": ccr}},
    })


def test_parse_file_keys_on_id_and_reqid(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text("\n".join([
        _rec("m1", "r1", "2026-07-13T10:00:00Z", "claude-opus-4", 100, 10, 0, 0),
        _rec("m1", "r1", "2026-07-13T10:00:00Z", "claude-opus-4", 100, 10, 0, 0),  # dup line
    ]))
    recs = cc.parse_file(f)
    # both lines share (m1,r1) -> a single record survives
    assert len(recs) == 1
    assert list(recs.keys())[0] == "m1\x1fr1"


def test_missing_ids_are_not_collapsed(tmp_path):
    f = tmp_path / "b.jsonl"
    # no message.id / requestId -> each occurrence must be counted (unique keys)
    line = json.dumps({"timestamp": "2026-07-13T10:00:00Z",
                       "message": {"model": "x", "usage": {"input_tokens": 5}}})
    f.write_text(line + "\n" + line + "\n")
    recs = cc.parse_file(f)
    assert len(recs) == 2   # both kept via synthetic path#lineno keys


def test_global_merge_dedupes_across_files(tmp_path):
    # Same (m1,r1) appears in two different files -> counted once when merged.
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    rec = _rec("m1", "r1", "2026-07-13T10:00:00Z", "claude-sonnet-4", 1000, 100, 50, 5)
    a.write_text(rec)
    b.write_text(rec)
    merged = {}
    merged.update(cc.parse_file(a))
    merged.update(cc.parse_file(b))
    assert len(merged) == 1
    day, model, inp, out, cr, ccr = list(merged.values())[0]
    assert (inp, out, cr, ccr) == (1000, 100, 50, 5)   # not doubled
