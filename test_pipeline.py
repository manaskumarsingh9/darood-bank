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
import split_blocks
import ingest
import split_weeks


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


# ------------------------------------------------- ingest / split_weeks ----
def _msg(time, date, sender, text):
    return f"[{time}, {date}] {sender}: {text}"


A = _msg("4:48 am", "08/04/2026", "Alice", "darood sharif 100 martba")
B = _msg("6:13 am", "08/04/2026", "Bob", "surah ikhlas 51 bar")
C = _msg("7:33 am", "09/04/2026", "Carol", "kalma sharif 21 bar")
D = _msg("9:01 pm", "23/03/2026", "Dara", "sijra sharif 11 bar")


def _ingest(*chunks):
    """Merge chunks the way ingest.py does; returns (messages, new_from_last).

    Each chunk is a separate paste, which matters: repeat handling is per-paste.
    """
    merged, new = [], 0
    for i, chunk in enumerate(chunks):
        msgs, _ = ingest.read_messages(chunk.split("\n"), None, f"paste{i}")
        merged, new, _trunc = ingest.merge(merged, msgs)
    return merged, new


def test_ingest_dedupes_overlapping_pastes():
    # Second paste re-copies A and B (as happens scrolling up) plus one new one.
    merged, new = _ingest("\n".join([A, B]), "\n".join([A, B, C]))
    assert len(merged) == 3, [m["sender"] for m in merged]
    assert new == 1, new
    assert [m["sender"] for m in merged] == ["Alice", "Bob", "Carol"]


def test_ingest_is_idempotent():
    once, _ = _ingest("\n".join([A, B, C]))
    twice, new = _ingest("\n".join([A, B, C]), "\n".join([A, B, C]))
    assert len(once) == len(twice) == 3
    assert new == 0, new


def test_ingest_sorts_out_of_order_pastes():
    # Newer chunk pasted first, older chunk second (scrolling backwards).
    merged, _ = _ingest("\n".join([B, C]), D)
    assert [m["sender"] for m in merged] == ["Dara", "Bob", "Carol"]


def test_ingest_orders_within_a_day_by_time():
    merged, _ = _ingest("\n".join([B, A]))  # 6:13 am pasted before 4:48 am
    assert [m["sender"] for m in merged] == ["Alice", "Bob"]


def test_ingest_preserves_multiline_devanagari():
    text = ("[10:37 am, 08/04/2026] Mushtaq: 08/04/2026\n"
            "200 दुरूद शरीफ\n"
            "200 कलमा शरीफ\n"
            "22 सूरेह फातेहा")
    merged, _ = _ingest(text)
    assert len(merged) == 1, merged
    assert merged[0]["body"] == ["08/04/2026", "200 दुरूद शरीफ",
                                 "200 कलमा शरीफ", "22 सूरेह फातेहा"]


def test_ingest_preserves_urdu_body():
    merged, _ = _ingest("[8:00 pm, 08/04/2026] Sufi: درود شریف 100 مرتبہ")
    assert len(merged) == 1
    assert "درود شریف" in merged[0]["body"][0]


def test_ingest_strips_leading_bidi_marks():
    # WhatsApp prefixes copied lines with LRM/RLM; str.strip() does not remove them.
    assert "‎".strip() == "‎", "precondition: strip() leaves bidi marks"
    merged, _ = _ingest("‎" + A)
    assert len(merged) == 1, "bidi-prefixed header must still parse"
    assert merged[0]["sender"] == "Alice"


def test_ingest_preserves_leading_space_on_continuation():
    # A continuation line's leading space is part of the body the resolver reads;
    # stripping it silently alters the text being counted.
    text = "[10:37 am, 08/04/2026] Raj: 20/03/2026\n राजेश राघव \nदरूद शरीफ - 108"
    merged, _ = _ingest(text)
    assert merged[0]["body"][1] == " राजेश राघव ", ascii(merged[0]["body"][1])


def test_strip_leading_bidi_leaves_whitespace():
    assert split_weeks.strip_leading_bidi("‎ x") == " x"
    assert split_weeks.strip_leading_bidi("‏‫ abc") == " abc"
    assert split_weeks.strip_leading_bidi("  indented") == "  indented"


def test_ingest_keeps_genuine_double_send():
    # Real case from inputs/processed/01-to-07-Apr-2026.txt: Bashir Patel posts
    # one message per person in the same minute, and the same name appears twice.
    # Collapsing these would silently drop a real 2021 darood.
    one = _msg("11:14 am", "07/04/2026", "Bashir", "जन्नतुल फिरदौस 2021 मर्तबा दरूद शरीफ")
    two = _msg("11:14 am", "07/04/2026", "Bashir", "फरजाना जन्नत 2021 मर्तबा दरूद शरीफ")
    merged, _ = _ingest("\n".join([one, two, one]))
    assert len(merged) == 3, [m["body"] for m in merged]


def test_ingest_double_send_survives_overlapping_pastes():
    one = _msg("11:14 am", "07/04/2026", "Bashir", "जन्नतुल फिरदौस 2021")
    two = _msg("11:14 am", "07/04/2026", "Bashir", "फरजाना जन्नत 2021")
    # Both pastes contain the whole run; the doubled message must stay doubled.
    merged, _ = _ingest("\n".join([one, two, one]), "\n".join([one, two, one]))
    assert len(merged) == 3, [m["body"] for m in merged]


def test_ingest_multiplicity_is_max_not_sum():
    # One paste has the message once, another twice -> two copies, not three.
    merged, _ = _ingest(A, "\n".join([A, A]))
    assert len(merged) == 2, [m["body"] for m in merged]
    merged, _ = _ingest("\n".join([A, A]), A)
    assert len(merged) == 2, [m["body"] for m in merged]


def test_ingest_prefix_within_one_paste_is_kept():
    # Same minute, one body a prefix of the other, but both from one paste:
    # that is real data, not a truncation artifact.
    short = _msg("11:14 am", "07/04/2026", "Bashir", "जमीला भी 2021 मर्तबा दरूद")
    long = _msg("11:14 am", "07/04/2026", "Bashir", "जमीला भी 2021 मर्तबा दरूद शरीफ")
    merged, _ = _ingest("\n".join([short, long]))
    assert len(merged) == 2, [m["body"] for m in merged]


def test_ingest_splits_glued_pastes():
    # Two chunks concatenated with no newline between them weld the last message
    # of one onto the first of the next; the counts must not cross senders.
    merged, _ = _ingest(A + B)          # no separator at all
    assert len(merged) == 2, [m["sender"] for m in merged]
    assert [m["sender"] for m in merged] == ["Alice", "Bob"]
    assert merged[0]["body"] == ["darood sharif 100 martba"]
    assert merged[1]["body"] == ["surah ikhlas 51 bar"]


def test_ingest_does_not_split_bracketed_body_text():
    line = "[4:48 am, 08/04/2026] Alice: darood [see note] 100 martba"
    merged, _ = _ingest(line)
    assert len(merged) == 1, merged
    assert merged[0]["body"] == ["darood [see note] 100 martba"]


def test_ingest_drops_truncated_copy_of_a_message():
    # A paste cut off mid-message leaves a shortened copy that exact-match
    # dedupe cannot see; keeping it would count its numbers twice.
    full = "[10:37 am, 08/04/2026] Raj: 200 darood\n200 kalma\n22 fatiha"
    cut = "[10:37 am, 08/04/2026] Raj: 200 darood\n200 kalma"
    merged, _ = _ingest(full, cut)
    assert len(merged) == 1, [m["body"] for m in merged]
    assert merged[0]["body"] == ["200 darood", "200 kalma", "22 fatiha"]


def test_ingest_truncated_drop_is_order_independent():
    full = "[10:37 am, 08/04/2026] Raj: 200 darood\n200 kalma\n22 fatiha"
    cut = "[10:37 am, 08/04/2026] Raj: 200 darood\n200 kalma"
    merged, _ = _ingest(cut, full)      # truncated copy arrives first
    assert len(merged) == 1, [m["body"] for m in merged]
    assert merged[0]["body"][-1] == "22 fatiha"


def test_ingest_keeps_distinct_messages_at_same_timestamp():
    # Same sender and minute, but neither body is a prefix of the other.
    one = "[10:37 am, 08/04/2026] Raj: 200 darood"
    two = "[10:37 am, 08/04/2026] Raj: 51 kalma"
    merged, _ = _ingest(one, two)
    assert len(merged) == 2, [m["body"] for m in merged]


def test_ingest_skips_preamble_before_first_message():
    msgs, skipped = ingest.read_messages(
        ("Messages are end-to-end encrypted.\n" + A).split("\n"), None, "test")
    assert len(msgs) == 1 and skipped == 1, (len(msgs), skipped)


def test_ingest_identity_distinguishes_senders():
    other = _msg("4:48 am", "08/04/2026", "Zed", "darood sharif 100 martba")
    merged, _ = _ingest("\n".join([A, other]))
    assert len(merged) == 2, "same text from a different sender is not a duplicate"


def test_parse_time_meridiem():
    assert ingest.parse_time("12:05 am") == (0, 5, 0)
    assert ingest.parse_time("12:05 pm") == (12, 5, 0)
    assert ingest.parse_time("4:48 am") == (4, 48, 0)
    assert ingest.parse_time("11:49 pm") == (23, 49, 0)


def test_header_wrapper_matches_full_parser():
    # parse_message_header must keep its old 3-tuple contract after the refactor.
    assert split_blocks.parse_message_header(A) == ("08/04/2026", "Alice",
                                                    "darood sharif 100 martba")
    assert split_blocks.parse_message_header("not a message") is None
    full = split_blocks.parse_message_header_full(A)
    assert full[0] == "4:48 am"
    assert full[1:] == split_blocks.parse_message_header(A)


def test_week_filename_spans():
    from datetime import date as _d
    assert split_weeks.week_filename(_d(2026, 4, 6), _d(2026, 4, 12)) == \
        "06-to-12-Apr-2026.txt"
    assert split_weeks.week_filename(_d(2026, 3, 30), _d(2026, 4, 5)) == \
        "30-Mar-to-05-Apr-2026.txt"
    assert split_weeks.week_filename(_d(2026, 12, 28), _d(2027, 1, 3)) == \
        "28-Dec-2026-to-03-Jan-2027.txt"


def test_week_bucketing_is_monday_based():
    from datetime import date as _d
    weeks, _, _ = split_weeks.bucket_lines([A, C, D], None, False, 0)
    assert sorted(weeks) == [_d(2026, 3, 23), _d(2026, 4, 6)], sorted(weeks)


def test_since_floor_drops_older_messages():
    from datetime import date as _d
    weeks, _, dropped = split_weeks.bucket_lines(
        [D, A, C], None, False, 0, since=_d(2026, 4, 1))
    assert dropped == 1, dropped
    assert sorted(weeks) == [_d(2026, 4, 6)]


def test_since_floor_drops_continuation_lines_too():
    from datetime import date as _d
    weeks, _, _ = split_weeks.bucket_lines(
        [D, "51 bar surah fatiha", A], None, False, 0, since=_d(2026, 4, 1))
    kept = [ln for group in weeks.values() for ln in group]
    assert "51 bar surah fatiha" not in kept, kept


def _run_split_weeks(tmp, *extra):
    """Invoke split_weeks.main() against a temp dir; returns its printed output."""
    import io
    import sys as _sys
    import contextlib
    argv = _sys.argv
    _sys.argv = ["split_weeks.py", os.path.join(tmp, "chatlog.txt"),
                 "-o", os.path.join(tmp, "inputs"),
                 "--state", os.path.join(tmp, "state.json")] + list(extra)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            split_weeks.main()
    finally:
        _sys.argv = argv
    return buf.getvalue()


def _setup_split(tmp):
    os.makedirs(os.path.join(tmp, "inputs"), exist_ok=True)
    with open(os.path.join(tmp, "chatlog.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join([A, B, C]) + "\n")
    return os.path.join(tmp, "inputs", "06-to-12-Apr-2026.txt")


def test_split_weeks_writes_and_records_week():
    with tempfile.TemporaryDirectory() as tmp:
        week = _setup_split(tmp)
        _run_split_weeks(tmp)
        assert os.path.exists(week), "week file should be written"
        with open(os.path.join(tmp, "state.json"), encoding="utf-8") as f:
            state = json.load(f)
        assert "06-to-12-Apr-2026.txt" in state["generated"]


def test_split_weeks_never_overwrites_handmade_file():
    with tempfile.TemporaryDirectory() as tmp:
        week = _setup_split(tmp)
        with open(week, "w", encoding="utf-8") as f:
            f.write("HAND MADE\n")
        out = _run_split_weeks(tmp)        # no state -> not ours -> protected
        with open(week, encoding="utf-8") as f:
            assert f.read() == "HAND MADE\n", \
                "a file this script did not create must never be overwritten"
        assert "KEEP" in out, out


def test_split_weeks_rewrites_its_own_file():
    with tempfile.TemporaryDirectory() as tmp:
        week = _setup_split(tmp)
        _run_split_weeks(tmp)              # creates it and records provenance
        with open(week, "w", encoding="utf-8") as f:
            f.write("stale\n")
        _run_split_weeks(tmp)              # ours, so it may be refreshed
        with open(week, encoding="utf-8") as f:
            assert "stale" not in f.read()


def test_split_weeks_overwrite_flag_forces_handmade():
    with tempfile.TemporaryDirectory() as tmp:
        week = _setup_split(tmp)
        with open(week, "w", encoding="utf-8") as f:
            f.write("HAND MADE\n")
        _run_split_weeks(tmp, "--overwrite")
        with open(week, encoding="utf-8") as f:
            assert "HAND MADE" not in f.read()


def test_split_weeks_skips_already_processed_week():
    with tempfile.TemporaryDirectory() as tmp:
        week = _setup_split(tmp)
        processed = os.path.join(tmp, "inputs", "processed")
        os.makedirs(processed)
        with open(os.path.join(processed, "06-to-12-Apr-2026.txt"), "w",
                  encoding="utf-8") as f:
            f.write("already counted\n")
        out = _run_split_weeks(tmp)
        assert not os.path.exists(week), "processed week must not be regenerated"
        assert "already processed" in out, out


def test_split_weeks_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        week = _setup_split(tmp)
        _run_split_weeks(tmp, "--dry-run")
        assert not os.path.exists(week)
        assert not os.path.exists(os.path.join(tmp, "state.json"))


def test_split_weeks_skips_in_progress_current_week():
    from datetime import date as _d
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "inputs"))
        today = _d.today()
        line = _msg("4:48 am", today.strftime("%d/%m/%Y"), "Alice", "darood 100")
        with open(os.path.join(tmp, "chatlog.txt"), "w", encoding="utf-8") as f:
            f.write(line + "\n")
        out = _run_split_weeks(tmp)
        assert "still in progress" in out, out
        assert not os.listdir(os.path.join(tmp, "inputs")), \
            "a half-captured current week must not be frozen as final"


def test_split_weeks_output_reparses_through_split_blocks():
    # A generated week file must be indistinguishable from a hand-made one.
    with tempfile.TemporaryDirectory() as tmp:
        week = _setup_split(tmp)
        _run_split_weeks(tmp)
        with open(week, encoding="utf-8") as f:
            msgs = split_blocks.split_messages(f.readlines())
        assert len(msgs) == 3, msgs
        assert [m["sender"] for m in msgs] == ["Alice", "Bob", "Carol"]
        assert msgs[0]["envelope_date"] == "08/04/2026"


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
