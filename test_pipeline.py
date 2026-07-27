"""
Tests for the deterministic classification backbone (no AI, no API).

Run with either:
    python test_pipeline.py         # self-contained runner, prints PASS/FAIL
    python -m pytest test_pipeline.py
"""
import os
import json
import tempfile

import classify_verify as cv   # legacy backbone (no longer in the workflow; still tested)
import pair
import normalize
import compose
import resolve
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


def test_normalize_urdu_arabic_script():
    lut = normalize.load_table()
    darood_sharif = "درود شریف"  # درود شریف
    assert normalize.normalize(darood_sharif, lut) == "DAROOD"
    # harakat (diacritics) must not change the match
    with_harakat = "درُود شریف"
    assert normalize.normalize(with_harakat, lut) == "DAROOD"
    # Arabic yeh (ي) and Urdu yeh (ی) must unify
    arabic_yeh = "درود شريف"
    assert normalize.normalize(arabic_yeh, lut) == "DAROOD"
    kalma = "کلمہ شریف"  # کلمہ شریف
    assert normalize.normalize(kalma, lut) == "KALMA SHARIF"
    ikhlas = "سورہ اخلاص"  # سورہ اخلاص
    assert normalize.normalize(ikhlas, lut) == "SURAH IKHLAS"


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


def _write_blocks(d, blocks):
    bp = os.path.join(d, "blocks.jsonl")
    with open(bp, "w", encoding="utf-8") as f:
        for b in blocks:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    return bp


def test_build_template_sender_positional():
    # a rigid dot-template message: build parses it positionally, not by guessing.
    blocks = [{
        "id": 5, "envelope_date": "09/03", "sender": "+91 99264 85966",
        "text": "09.04.2026.abid.300.martba.darood.sharif.500.martba.darood."
                "sharif.500.msrtba.surs.iklas.500.martba.kalma.sharif.",
    }]
    with tempfile.TemporaryDirectory() as d:
        bp = _write_blocks(d, blocks)
        ep = os.path.join(d, "e.json"); rp = os.path.join(d, "r.txt")
        entries, review, hard = build_extracted.build(bp, ep, rp)
    assert hard == 0, review
    chants = sorted((e["chant"], e["count"]) for e in entries)
    assert chants == [("DAROOD", 300), ("DAROOD", 500),
                      ("KALMA SHARIF", 500), ("SURAH IKHLAS", 500)], chants
    assert all(e["date"] == "09/03/2026" for e in entries)


def test_build_known_duplicate_excluded_not_hard():
    # a message matching known_duplicates.json (sender+date+exact text) is
    # dropped silently as [INFO], contributes no entries, and does not
    # block the exit gate -- unlike a genuine unresolved flag.
    blocks = [{
        "id": 88, "envelope_date": "14/03/2026", "sender": "+91 98934 59753",
        "text": "darud Sharif 121 martba kalma Sharif 121 martba Sur ikhlas 31 "
                "martba 121 martba",
    }]
    with tempfile.TemporaryDirectory() as d:
        bp = _write_blocks(d, blocks)
        ep = os.path.join(d, "e.json"); rp = os.path.join(d, "r.txt")
        dp = os.path.join(d, "dups.json")
        with open(dp, "w", encoding="utf-8") as f:
            json.dump([{
                "sender": "+91 98934 59753", "envelope_date": "14/03/2026",
                "text": blocks[0]["text"], "reason": "superseded by a resend",
            }], f)
        entries, review, hard = build_extracted.build(bp, ep, rp, known_duplicates_path=dp)
    assert hard == 0, review
    assert entries == []
    assert any(r.startswith("[INFO] known-duplicate: msg 88") for r in review), review


def test_build_unmatched_duplicate_entry_still_flags():
    # same message but no matching known_duplicates.json -> still a HARD flag.
    blocks = [{
        "id": 88, "envelope_date": "14/03/2026", "sender": "+91 98934 59753",
        "text": "darud Sharif 121 martba kalma Sharif 121 martba Sur ikhlas 31 "
                "martba 121 martba",
    }]
    with tempfile.TemporaryDirectory() as d:
        bp = _write_blocks(d, blocks)
        ep = os.path.join(d, "e.json"); rp = os.path.join(d, "r.txt")
        dp = os.path.join(d, "dups.json")
        with open(dp, "w", encoding="utf-8") as f:
            json.dump([], f)
        entries, review, hard = build_extracted.build(bp, ep, rp, known_duplicates_path=dp)
    assert hard == 1, review
    assert entries == []


# ----------------------------------------------------------- compose ----
def test_compose_multiplicative_coverage():
    # combos never enumerated in any dictionary resolve from token classes.
    assert compose.match("sure kauser") == "SURAH KAUSER"
    assert compose.match("surh kausar") == "SURAH KAUSER"
    assert compose.match("surah koshar") == "SURAH KAUSER"
    assert compose.match("surh nash") == "surah naas"


def test_compose_optional_slots_and_scripts():
    assert compose.match("ikhlas") == "SURAH IKHLAS"          # SURAH optional
    assert compose.match("surah ikhlas sharif") == "SURAH IKHLAS"  # SHARIF optional
    assert compose.match("sur e kahf") == "Sur e Kahf"        # 'e' dropped
    assert compose.match("durood taj") == "DAROOD TAJ"
    assert compose.match("सूरह कौसर") == "SURAH KAUSER"        # Devanagari
    assert compose.match("سورہ کوثر") == "SURAH KAUSER"        # Urdu


def test_compose_conservative_no_false_match():
    assert compose.match("surah") is None          # shared word alone is ambiguous
    assert compose.match("surah khan") is None     # unknown word -> whole phrase fails
    assert compose.match("ali") is None            # a plain name
    assert compose.match("") is None


def test_compose_never_disagrees_with_exact_dict():
    # where compose CAN match a known dictionary variant, it must agree.
    data = json.load(open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "chant_mappings.json"), encoding="utf-8"))
    for canon, variants in data.items():
        for v in [canon] + variants:
            got = compose.match(v)
            assert got in (None, canon), f"{v!r}: compose={got!r} dict={canon!r}"


# ------------------------------------------------------------ end-to-end ----
def _block(text, mid=1, env="13/03", sender="Some Person"):
    return {"id": mid, "envelope_date": env, "sender": sender, "text": text}


def test_build_end_to_end():
    # The promoted flow: raw blocks.jsonl -> resolver -> extracted.json.
    blocks = [
        # clean id82-like -> one DAROOD entry (name + Devanagari chant + count)
        _block("गोरखपुर से वशीउल्लाह दरूद शरीफ 2000 बार", mid=82),
        # id67-like run-on -> four entries, correctly paired (names/units dropped)
        _block("Aaj dinank 13.3.2026 sufi afsar darud Sharif 121 martba "
               "kalma Sharif 121 martba surah ikhlas 30 martba "
               "toba astagfirullah 141", mid=67),
        # a brand-new (unknown) chant spelling: its count must NOT be guessed;
        # the dropped phrase leaves 50 dangling -> HARD flag, never in totals.
        _block("brandnewchant 50", mid=99),
    ]

    with tempfile.TemporaryDirectory() as d:
        bp = _write_blocks(d, blocks)
        epath = os.path.join(d, "extracted.json")
        rpath = os.path.join(d, "review.txt")
        entries, review, hard = build_extracted.build(bp, epath, rpath)

    # DAROOD 2000 + the four id67 chants = 5 clean entries
    assert len(entries) == 5, entries
    darood = [e for e in entries if e["chant"] == "DAROOD"]
    assert {e["count"] for e in darood} == {2000, 121}
    assert any(e["chant"] == "Astagfar" and e["count"] == 141 for e in entries)
    assert any(e["chant"] == "KALMA SHARIF" and e["count"] == 121 for e in entries)
    assert any(e["chant"] == "SURAH IKHLAS" and e["count"] == 30 for e in entries)
    # every entry carries the send date, never the in-body date
    assert all(e["date"] == "13/03/2026" for e in entries)
    # the unknown chant is a HARD review item, and its count never entered totals
    assert hard >= 1, review
    assert any("msg 99" in r for r in review)
    assert not any(e["count"] == 50 for e in entries)


def test_resolver_is_reproducible():
    # same input -> byte-identical entries across runs (the whole point).
    blocks = [_block("darud sharif 121 kalma sharif 121", mid=1),
              _block("सूरह कौसर 500", mid=2)]
    decisions = resolve._load_decisions()
    e1 = [resolve.resolve_message(b, decisions)[0] for b in blocks]
    e2 = [resolve.resolve_message(b, decisions)[0] for b in blocks]
    assert e1 == e2
    flat = [x for es in e1 for x in es]
    assert ("DAROOD", 121) in [(e["chant"], e["count"]) for e in flat]
    assert ("SURAH KAUSER", 500) in [(e["chant"], e["count"]) for e in flat]


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
