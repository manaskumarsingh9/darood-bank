"""
Tests for the deterministic classification backbone (no AI, no API).

Run with either:
    python test_pipeline.py         # self-contained runner, prints PASS/FAIL
    python -m pytest test_pipeline.py
"""
import os
import json
import tempfile

import classify_verify as cv
import pair
import normalize
import build_extracted
import sender_templates


# ---------------------------------------------------------------- verify ----
def _rec(segments, text=None, env="13/03/2026", mid=1):
    if text is None:
        text = "".join(s["t"] for s in segments)
    return {"id": mid, "envelope_date": env, "text": text, "segments": segments}


def test_verify_clean_passes():
    rec = _rec([
        {"t": "दरूद शरीफ- ", "role": "chant-label"},
        {"t": "2000", "role": "count"},
        {"t": " बार", "role": "filler"},
    ])
    flags = cv.verify_record(rec)
    assert not [f for f in flags if f.level == "hard"], flags


def test_verify_reconstruction_catches_dropped_text():
    segs = [
        {"t": "दरूद शरीफ- ", "role": "chant-label"},
        {"t": "2000", "role": "count"},
    ]
    # original has extra text the segments do not cover
    rec = _rec(segs, text="दरूद शरीफ- 2000 बार EXTRA")
    codes = {f.code for f in cv.verify_record(rec) if f.level == "hard"}
    assert "reconstruction" in codes


def test_verify_census_catches_merged_number():
    # original has two 121s; a single merged count 121121 breaks the census
    rec = _rec(
        [
            {"t": "darud ", "role": "chant-label"},
            {"t": "121121", "role": "count"},
        ],
        text="darud 121 121",
    )
    codes = {f.code for f in cv.verify_record(rec) if f.level == "hard"}
    assert "census" in codes


def test_verify_count_shape():
    rec = _rec([
        {"t": "darud ", "role": "chant-label"},
        {"t": "121 30", "role": "count"},
    ])
    codes = {f.code for f in cv.verify_record(rec) if f.level == "hard"}
    assert "count-shape" in codes
    assert "census" not in codes  # numbers still all accounted for


def test_verify_uncertain_is_hard():
    rec = _rec([
        {"t": "Rekha Devi ", "role": "uncertain"},
        {"t": "108", "role": "count"},
        {"t": " bar", "role": "filler"},
    ])
    codes = {f.code for f in cv.verify_record(rec) if f.level == "hard"}
    assert "uncertain" in codes


def test_verify_date_yearlike_is_soft():
    rec = _rec([
        {"t": "darud ", "role": "chant-label"},
        {"t": "2026", "role": "count"},
    ])
    flags = cv.verify_record(rec)
    assert not [f for f in flags if f.level == "hard"]
    assert any(f.code == "count-yearlike" for f in flags)


# ------------------------------------------------------------------ pair ----
def test_pair_label_first():
    stream = [("L", "darud"), ("N", 121), ("L", "kalma"), ("N", 30)]
    pairs, err = pair.pair_stream(stream)
    assert err is None
    assert pairs == [("darud", 121), ("kalma", 30)]


def test_pair_count_first():
    stream = [("N", 32000), ("L", "darood"), ("N", 3600), ("L", "kalima")]
    pairs, err = pair.pair_stream(stream)
    assert err is None
    assert pairs == [("darood", 32000), ("kalima", 3600)]


def test_pair_non_alternating_flags():
    stream = [("L", "a"), ("L", "b"), ("N", 1), ("N", 2)]
    pairs, err = pair.pair_stream(stream)
    assert pairs == []
    assert err is not None


def test_pair_unbalanced_flags():
    stream = [("L", "a"), ("N", 1), ("L", "b")]  # dangling label
    pairs, err = pair.pair_stream(stream)
    assert pairs == []
    assert err is not None


def test_pair_empty_ok():
    assert pair.pair_stream([]) == ([], None)


def test_id67_shape_pairs_correctly():
    # the run-on message that used to mispair silently
    segs = [
        {"t": "darud Sharif ", "role": "chant-label"},
        {"t": "121", "role": "count"},
        {"t": " kalma Sharif ", "role": "chant-label"},
        {"t": "121", "role": "count"},
        {"t": " surah ikhlas ", "role": "chant-label"},
        {"t": "30", "role": "count"},
        {"t": " toba astagfirullah ", "role": "chant-label"},
        {"t": "141", "role": "count"},
    ]
    pairs, err = pair.pair_stream(pair.stream_from_segments(segs))
    assert err is None
    assert pairs == [("darud Sharif", 121), ("kalma Sharif", 121),
                     ("surah ikhlas", 30), ("toba astagfirullah", 141)]


# ------------------------------------------------------------- normalize ----
def test_normalize_known_latin_and_devanagari():
    lut = normalize.load_table()
    assert normalize.normalize("Darood Sharif", lut) == "DAROOD"
    assert normalize.normalize("दरूद शरीफ", lut) == "DAROOD"
    assert normalize.normalize("darood e taj", lut) == "DAROOD TAJ"
    assert normalize.normalize("toba astagfirullah", lut) == "Astagfar"
    assert normalize.normalize("श्री गायत्री मंत्र", lut) == "Gayatri Mantra"


def test_normalize_miss_returns_none():
    lut = normalize.load_table()
    assert normalize.normalize("some brand new chant", lut) is None


# ------------------------------------------------------- sender templates ----
def test_template_dot_sender_positional():
    text = ("09.04.2026.sufi.molana.ssb.abid.bhai.300.martba.darood.sharif."
            "sabnam.bee.500.martba.darood.sharif.500.msrtba.surs.iklas.500."
            "martba.kalma.sharif.")
    res = sender_templates.extract("+91 99264 85966", text)
    assert res == [
        {"chant": "DAROOD", "count": 300},
        {"chant": "DAROOD", "count": 500},
        {"chant": "SURAH IKHLAS", "count": 500},
        {"chant": "KALMA SHARIF", "count": 500},
    ], res


def test_template_50_correction():
    # the SURAH IKHLAS slot corrupted to 50 is corrected to 500 for this sender
    text = "10.03.2026.abid.300.darood.500.darood.50.surs.iklas.500.kalma."
    res = sender_templates.extract("+91 99264 85966", text)
    assert res[2] == {"chant": "SURAH IKHLAS", "count": 500}, res


def test_template_shape_mismatch_returns_none():
    # only two counts -> shape does not match the 4-slot template -> None
    text = "10.03.2026.abid.300.darood.500.darood."
    assert sender_templates.extract("+91 99264 85966", text) is None


def test_template_unknown_sender_none():
    assert sender_templates.extract("Some Other Person", "300 darood 500 kalma") is None


def test_build_template_overrides_llm_labeling(tmp_path=None):
    import os as _os, json as _json, tempfile as _tf
    # a template-sender message that the LLM mislabeled (garbage chant-label);
    # with blocks passed, the deterministic template must win.
    classified = [{
        "id": 5, "envelope_date": "09/03",
        "text": "09.04.2026.abid.300.martba.darood.sharif.500.martba.darood."
                "sharif.500.msrtba.surs.iklas.500.martba.kalma.sharif.",
        "segments": [
            {"t": "09.04.2026.abid.", "role": "filler"},
            {"t": "300", "role": "count"},
            {"t": ".martba.darood.sharif.500.martba.darood.sharif.500.msrtba."
                  "surs.iklas.500.martba.kalma.sharif.", "role": "chant-label"},
        ],
    }]
    blocks = [{"id": 5, "envelope_date": "09/03", "sender": "+91 99264 85966",
               "text": classified[0]["text"]}]
    with _tf.TemporaryDirectory() as d:
        cp = _os.path.join(d, "c.jsonl"); bp = _os.path.join(d, "b.jsonl")
        with open(cp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(classified[0], ensure_ascii=False) + "\n")
        with open(bp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(blocks[0], ensure_ascii=False) + "\n")
        ep = _os.path.join(d, "e.json"); rp = _os.path.join(d, "r.txt")
        entries, review, hard = build_extracted.build(cp, ep, rp, bp)
    # template produced the 4 correct entries; the LLM's garbage label was ignored
    assert hard == 0, review
    chants = sorted((e["chant"], e["count"]) for e in entries)
    assert chants == [("DAROOD", 300), ("DAROOD", 500),
                      ("KALMA SHARIF", 500), ("SURAH IKHLAS", 500)], chants
    assert all(e["date"] == "09/03/2026" for e in entries)


# ------------------------------------------------------------ end-to-end ----
def test_build_end_to_end():
    records = [
        # clean id82-like -> one DAROOD entry
        _rec([
            {"t": "गोरखपुर से ", "role": "filler"},
            {"t": "13-03-2026", "role": "date"},
            {"t": "\nवशीउल्लाह\n", "role": "name"},
            {"t": "दरूद शरीफ- ", "role": "chant-label"},
            {"t": "2000", "role": "count"},
            {"t": " बार", "role": "filler"},
        ], mid=82),
        # id67-like run-on -> four entries, correctly paired
        _rec([
            {"t": "Aaj dinank ", "role": "filler"},
            {"t": "13.3.2026", "role": "date"},
            {"t": " sufi afsar ", "role": "name"},
            {"t": "darud Sharif ", "role": "chant-label"},
            {"t": "121", "role": "count"},
            {"t": " martba kalma Sharif ", "role": "filler"},
            {"t": "kalma Sharif ", "role": "chant-label"},
            {"t": "121", "role": "count"},
            {"t": " martba surah ikhlas ", "role": "filler"},
            {"t": "surah ikhlas ", "role": "chant-label"},
            {"t": "30", "role": "count"},
            {"t": " martba toba astagfirullah ", "role": "filler"},
            {"t": "toba astagfirullah ", "role": "chant-label"},
            {"t": "141", "role": "count"},
        ], mid=67),
        # unknown label -> routed to review, not into entries
        _rec([
            {"t": "brandnewchant ", "role": "chant-label"},
            {"t": "50", "role": "count"},
        ], mid=99),
    ]

    with tempfile.TemporaryDirectory() as d:
        cpath = os.path.join(d, "classified.jsonl")
        with open(cpath, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        epath = os.path.join(d, "extracted.json")
        rpath = os.path.join(d, "review.txt")
        entries, review, hard = build_extracted.build(cpath, epath, rpath)

    # DAROOD 2000 + the four id67 chants = 5 clean entries
    assert len(entries) == 5, entries
    darood = [e for e in entries if e["chant"] == "DAROOD"]
    assert {e["count"] for e in darood} == {2000, 121}
    assert any(e["chant"] == "Astagfar" and e["count"] == 141 for e in entries)
    # every entry carries the send date, never the in-body date
    assert all(e["date"] == "13/03/2026" for e in entries)
    # the unknown label is a HARD review item, and its count never entered totals
    assert hard >= 1
    assert any("unknown-label" in r and "brandnewchant" in r for r in review)
    assert not any(e["count"] == 50 for e in entries)


# --------------------------------------------------------------- runner ----
def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run() else 0)
