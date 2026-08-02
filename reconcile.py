"""
Deterministic accuracy check (no AI, no API calls) AND a hard gate.

For each send-date it compares two multisets of numbers:
  * SOURCE: every number found in the raw message bodies for that date, after
    removing date expressions and list-item markers.
  * EXTRACTED: the `count` values in extracted.json for that date.

Anything in SOURCE but not EXTRACTED is a *possible missed count*.
Anything in EXTRACTED but not in SOURCE is a *possible typo / hallucination*.

Some noise is expected (stray day/month fragments, enumeration markers), so a
tiny 3 or 13 showing up here is normal. The gate therefore fails ONLY on
*significant* discrepancies -- a flagged value at or above SIGNIFICANCE_THRESHOLD
(default 100, override with env RECONCILE_MIN). That is exactly the class of
error that inflates a daily total (e.g. a duplicated 2500 or an invented 5000);
small fragments are reported but do not fail the run.

Exit code:
    0  -> CLEAN, or only minor sub-threshold fragments (PASS)
    1  -> at least one significant discrepancy (FAIL) -- do NOT aggregate

NOTE: this gate catches dropped/invented/misdated counts, but NOT chant
*misidentification* (a real number paired with the wrong chant): moving a number
from one chant column to another leaves the per-date number multiset unchanged.
For that, use a verifier subagent.

Messages matching known_duplicates.json (see build_extracted.py) are excluded
from SOURCE entirely, since build_extracted excludes them too -- otherwise this
gate would always flag a confirmed duplicate's numbers as "missed".

Usage:
    python reconcile.py blocks.jsonl extracted.json [report_out.txt]
"""
import os
import sys
import json
import re
from collections import Counter, defaultdict

import duplicates
import non_chant
import text_corrections
import sender_templates

# mixed separator then space before year: "26.2 2026", "27.2 2026"
MIXED_DATE_RE = re.compile(r'\d{1,2}\s*[./,=\-]+\s*\d{1,2}\s+20\d\d')
# 24.2.26  24/02/2026  24-02-026  24=2=2026  24,2, 26  27 - 02- 26  16-03--2026
DATE_RE = re.compile(r'\d{1,2}\s*[./,=\-]+\s*\d{1,2}\s*[./,=\-]+\s*\d{2,4}')
# colon-separated dates: 31:03:2026
COLON_DATE_RE = re.compile(r'\d{1,2}:\d{1,2}:\d{2,4}')
# space-separated trailing date like "27 2 2025"
SPACE_DATE_RE = re.compile(r'\b\d{1,2}\s+\d{1,2}\s+20\d\d\b')
# "dinank" (=date) keyword immediately followed by a day+year with the month
# dropped by typo: "dinank 23 2026"
DINANK_SHORT_DATE_RE = re.compile(r'\bdinank\s+\d{1,2}\s+20\d\d\b', re.IGNORECASE)
# "dinank" followed by day, month, and a 2-digit year all space-separated:
# "dinank 1 4 26"
DINANK_DMY_SHORT_RE = re.compile(r'\bdinank\s+\d{1,2}\s+\d{1,2}\s+\d{2}\b', re.IGNORECASE)
# leading space-separated date with a 2-digit year: "26 3 26" or "31 3 .26". Only anchored
# at the very start of the message -- a bare number triple is a date prefix
# there (this sender group's convention), never three unrelated chant counts.
LEADING_SHORT_YEAR_DATE_RE = re.compile(r'^\s*\d{1,2}\s+\d{1,2}\s+\.?\d{2}\b')
# leading day-dash-glued-month+year with a missing second separator: "07-04026"
# (meant "07-04-2026"). Only anchored at message start, same rationale as above.
LEADING_DASH_GLUED_DATE_RE = re.compile(r'^\s*\d{1,2}-\d{4,6}\b')
# glued day+month (no separator) then a punctuation-separated year: "0404.2026"
CONCAT_DDMM_DATE_RE = re.compile(r'\b\d{3,4}\s*[./,=\-]+\s*20\d\d\b')
# line-leading list markers: "1.", "03.", "4)", "1-", "2-"
LIST_MARKER_RE = re.compile(r'(?m)^\s*\d{1,2}[.)-]')

# --- value-based date detection (generalises every pattern above) -------------
# The regexes above each enumerate one SEPARATOR spelling, so a new punctuation
# combination slips through (e.g. "9, 4 26" -- punctuation first, bare space
# second, 2-digit year -- matched none of them and became three phantom counts).
#
# This rule ignores the separator entirely and decides on the VALUES, the way a
# reader does: three numbers with NOTHING but punctuation/whitespace between them
# (never a word), which are simultaneously plausible as day / month / year in the
# Indian DD-MM-YY(YY) convention, are a date. It is self-guarding -- if the middle
# number exceeds 12 it cannot be a month, so the triple is left alone as counts.
# The separator class deliberately excludes letters and digits, so a real
# "500 martaba durood 300" (words in between) can never match.
_DSEP = r'[\s.,;:/\\=|~\-]+'
DATE_TRIPLE_RE = re.compile(
    r'(?<!\d)(\d{1,2})' + _DSEP + r'(\d{1,2})' + _DSEP + r'(\d{2,4})(?!\d)')

# Fallback band for a 2-digit year when no reference year is supplied. Real call
# sites always pass ref_year (the message's own send year), so this only applies
# to bare unit-test calls.
_YEAR_2DIGIT_MIN, _YEAR_2DIGIT_MAX = 20, 40


def _year_ok(year, ref_year):
    """Is `year` a plausible year for a message sent in `ref_year`?

    A 2-digit year is read as 20xx and accepted only when it is the send year or
    the one before it -- a message posted early in a year often reports the tail
    of the previous one. Deliberately anchored to the message's OWN send date and
    never to the wall clock: the clock would make the same input file resolve
    differently in a later year, breaking the pipeline's reproducibility.
    """
    if year >= 100:                      # written in full: 2026
        return 2000 <= year <= 2099
    if ref_year is None:
        return _YEAR_2DIGIT_MIN <= year <= _YEAR_2DIGIT_MAX
    # The send year, the one before it (a message early in a year reporting the
    # tail of the previous one), or the one after it (a fat-fingered year, e.g.
    # "12 6.27" posted in June 2026). Day/month must already be valid, so this
    # stays tight.
    return year in ((ref_year - 1) % 100, ref_year % 100, (ref_year + 1) % 100)


def is_date_triple(day, month, year, ref_year=None):
    """True if (day, month, year) is simultaneously plausible as an Indian date."""
    if not 1 <= day <= 31:
        return False
    if not 1 <= month <= 12:
        return False
    return _year_ok(year, ref_year)


# The sign-off date is usually last, but a name often trails it
# ("... 800 तोबा आसतगफार 17 7 25 सूफी रजाक"). Allow any run of non-digit text
# after the triple, so the name does not hide it.
TRAILING_DATE_TRIPLE_RE = re.compile(
    r'(?<!\d)(\d{1,2})' + _DSEP + r'(\d{1,2})' + _DSEP + r'(\d{2,4})(\s*\D*)$')


def strip_date_triples(text, ref_year=None):
    """Blank out number triples that are plausible dates; leave all others."""
    def _sub(mo):
        day, month, year = (int(g) for g in mo.groups())
        return ' ' if is_date_triple(day, month, year, ref_year) else mo.group(0)
    t = DATE_TRIPLE_RE.sub(_sub, text)

    # A triple sitting at the very END of the message, with nothing after it, is
    # a sign-off date even when the year is mistyped ("सूफी रजाक 13 7 20" -- sent
    # in 2026, so "20" fails the year test above, yet it is plainly a fat-fingered
    # 2026). Counts never trail a message with no chant name after them, so the
    # day/month checks alone are enough to call it here.
    def _sub_trailing(mo):
        day, month = int(mo.group(1)), int(mo.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return ' ' + mo.group(4)      # drop the date, keep the trailing name
        return mo.group(0)
    return TRAILING_DATE_TRIPLE_RE.sub(_sub_trailing, t)


def year_of(envelope_date):
    """Send year from an envelope date, or None if it cannot be read.

    norm_date() yields DD/MM/YYYY, so the year is the trailing component.
    """
    parts = re.split(r'[./-]', norm_date(envelope_date or ""))
    if parts and parts[-1].isdigit():
        year = int(parts[-1])
        if 1000 <= year <= 9999:
            return year
    return None

# Discrepancies at or above this magnitude fail the gate; smaller values are
# treated as expected stray date/enumeration fragments and only warned about.
SIGNIFICANCE_THRESHOLD = int(os.environ.get("RECONCILE_MIN", "100"))


# Number words written out instead of in digits. Segmentation anchors on digits,
# so these would otherwise leave their chant with no count at all:
#   "दुरूद ताज एक बार"       -> DAROOD TAJ 1
#   "अस्तगफिरुल्लाह सौ बार"   -> Astagfar 100
# `सौ` is ONLY the numeral 100 here: the honorific "सौ." (Mrs, as in
# "सौ.राजेश्वरी शर्मा") is always followed by a NAME, never a count unit, so
# requiring a trailing count unit keeps the two apart. A `सौ`/`sau` PRECEDED by a
# digit is a hundreds multiplier ("15 sau" = 1500) and is left alone here.
#
# Lives here, not in segment.py, so numbers_in() and segment.atoms() expand
# identically -- otherwise the per-date gate reports the expanded count as an
# invented number.
WORD_NUMBERS = {"ek": 1, "एक": 1, "sau": 100, "सौ": 100}
_COUNT_UNIT_AFTER = {"bar", "baar", "martba", "martaba", "maratba", "mrtba",
                     "martbaa", "बार", "मर्तबा", "मरतबा", "दफा", "times", "dafa"}
_WORD_NUM_RE = re.compile(
    r"(?:^|(?<=[\s.,;:=\-/()\[\]|~]))"
    r"(" + "|".join(sorted(map(re.escape, WORD_NUMBERS), key=len, reverse=True)) + r")"
    r"([\s.,;:=\-/]+)"
    r"(?=(?:" + "|".join(sorted(map(re.escape, _COUNT_UNIT_AFTER), key=len,
                                reverse=True)) + r")(?:[\s.,;:=\-/]|$))",
    re.IGNORECASE)


def expand_word_numbers(text):
    """Rewrite spelled-out numbers that are followed by a count unit into digits.

    Rewrites IN PLACE via re.sub -- it must not rebuild the string by splitting and
    re-joining, because the later date and list-marker patterns depend on the
    original newlines and punctuation ("1.सूफ़ी" is a list marker; "1 सूफ़ी" is not).
    """
    def _sub(mo):
        before = text[:mo.start()].rstrip(" \t")
        if before and before[-1].isdigit():
            return mo.group(0)      # "15 sau" -- a multiplier, not a bare 100
        return str(WORD_NUMBERS[mo.group(1).lower()]) + mo.group(2)
    return _WORD_NUM_RE.sub(_sub, text)


def numbers_in(text, ref_year=None):
    t = expand_word_numbers(text)
    t = strip_date_triples(t, ref_year)
    t = MIXED_DATE_RE.sub(' ', t)
    t = CONCAT_DDMM_DATE_RE.sub(' ', t)
    t = DATE_RE.sub(' ', t)
    t = COLON_DATE_RE.sub(' ', t)
    t = SPACE_DATE_RE.sub(' ', t)
    t = DINANK_SHORT_DATE_RE.sub(' ', t)
    t = DINANK_DMY_SHORT_RE.sub(' ', t)
    t = LEADING_SHORT_YEAR_DATE_RE.sub(' ', t)
    t = LEADING_DASH_GLUED_DATE_RE.sub(' ', t)
    t = LIST_MARKER_RE.sub(' ', t)
    return [int(n) for n in re.findall(r'\d+', t)]


def norm_date(envelope_date):
    # blocks store "24/02"; extracted store "24/02/2026"
    parts = re.split(r'[./-]', envelope_date)
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}/2026"
    return envelope_date


def _fmt(counter):
    """Render a Counter of {value: occurrences}, marking significant values."""
    parts = []
    for v, c in sorted(counter.items()):
        mark = "  (!) significant" if v >= SIGNIFICANCE_THRESHOLD else ""
        parts.append(f"{v}x{c}{mark}")
    return ", ".join(parts)


def analyze(blocks_path, extracted_path, duplicates_path=duplicates.DEFAULT_PATH,
            non_chant_path=non_chant.DEFAULT_PATH,
            corrections_path=text_corrections.DEFAULT_PATH):
    """Return (any_diff, significant, report_str)."""
    known_duplicates = duplicates.load(duplicates_path, norm_date=norm_date)
    known_non_chant = non_chant.load(non_chant_path, norm_date=norm_date)
    corrections = text_corrections.load(corrections_path, norm_date=norm_date)
    source = defaultdict(Counter)
    with open(blocks_path, encoding="utf-8") as f:
        for line in f:
            msg = json.loads(line)
            date = norm_date(msg["envelope_date"])
            sender = msg.get("sender", "")
            raw_text = msg.get("text", "")
            if duplicates.key(sender, date, raw_text) in known_duplicates:
                continue
            # A confirmed non-chant message reports nothing, so its digits (phone
            # numbers, mostly) must not enter the per-date backstop either.
            if non_chant.key(sender, date, raw_text) in known_non_chant:
                continue
            # Apply the same confirmed typo fix build_extracted applies, so both
            # sides of the gate count the same digits.
            msg_text, _ = text_corrections.apply_to(sender, date, raw_text, corrections)
            ref_year = year_of(msg["envelope_date"])
            template_entries = None
            if sender_templates.is_template_sender(sender):
                template_entries = sender_templates.extract(sender, msg_text, ref_year)
            if template_entries is not None:
                # A template sender's raw text can carry known OCR/typo count
                # corruptions (e.g. "500" -> "5p0") that sender_templates
                # already corrects positionally; comparing against the raw
                # digits here would flag that correction as a fabricated
                # number every time it fires. Use its corrected counts instead
                # -- they ARE what build_extracted wrote for this message.
                source[date].update(e["count"] for e in template_entries)
            else:
                source[date].update(numbers_in(msg_text, ref_year))

    extracted = defaultdict(Counter)
    with open(extracted_path, encoding="utf-8") as f:
        for e in json.load(f):
            extracted[str(e["date"])].update([int(e["count"])])

    lines = []
    any_diff = False
    significant = False

    for date in sorted(set(source) | set(extracted)):
        missed = source[date] - extracted[date]   # in source, not extracted
        extra = extracted[date] - source[date]     # extracted, not in source
        if not missed and not extra:
            continue
        any_diff = True
        if any(v >= SIGNIFICANCE_THRESHOLD for v in missed) or \
           any(v >= SIGNIFICANCE_THRESHOLD for v in extra):
            significant = True
        lines.append(f"\n=== {date} ===")
        if missed:
            lines.append("  possible MISSED counts (in message, not extracted):")
            lines.append("    " + _fmt(missed))
        if extra:
            lines.append("  possible TYPO/EXTRA counts (extracted, not in message):")
            lines.append("    " + _fmt(extra))

    if not any_diff:
        lines.append(
            "CLEAN: every extracted count is backed by a number in the source, "
            "and no source number is unaccounted for."
        )
    elif significant:
        lines.append(
            f"\nFAIL: significant discrepancies (value >= {SIGNIFICANCE_THRESHOLD}, "
            "marked '(!)') are unaccounted for. A large number in MISSED means a "
            "real count was dropped; in TYPO/EXTRA means one was invented or "
            "duplicated. Investigate before aggregating."
        )
    else:
        lines.append(
            f"\nPASS: only minor stray fragments (value < {SIGNIFICANCE_THRESHOLD}) "
            "differ -- expected day/month/enumeration noise, no real count affected."
        )

    return any_diff, significant, "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print("Usage: python reconcile.py <blocks.jsonl> <extracted.json> "
              "[report_out.txt]")
        sys.exit(2)

    blocks_path, extracted_path = sys.argv[1], sys.argv[2]
    report_path = sys.argv[3] if len(sys.argv) > 3 else None

    any_diff, significant, report = analyze(blocks_path, extracted_path)
    print(report)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report + "\n")

    sys.exit(1 if significant else 0)


if __name__ == "__main__":
    main()
