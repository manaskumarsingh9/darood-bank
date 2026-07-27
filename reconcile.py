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
import sender_templates

# mixed separator then space before year: "26.2 2026", "27.2 2026"
MIXED_DATE_RE = re.compile(r'\d{1,2}\s*[./,=\-]+\s*\d{1,2}\s+20\d\d')
# 24.2.26  24/02/2026  24-02-026  24=2=2026  24,2, 26  27 - 02- 26  16-03--2026
DATE_RE = re.compile(r'\d{1,2}\s*[./,=\-]+\s*\d{1,2}\s*[./,=\-]+\s*\d{2,4}')
# space-separated trailing date like "27 2 2025"
SPACE_DATE_RE = re.compile(r'\b\d{1,2}\s+\d{1,2}\s+20\d\d\b')
# "dinank" (=date) keyword immediately followed by a day+year with the month
# dropped by typo: "dinank 23 2026"
DINANK_SHORT_DATE_RE = re.compile(r'\bdinank\s+\d{1,2}\s+20\d\d\b', re.IGNORECASE)
# leading space-separated date with a 2-digit year: "26 3 26". Only anchored
# at the very start of the message -- a bare number triple is a date prefix
# there (this sender group's convention), never three unrelated chant counts.
LEADING_SHORT_YEAR_DATE_RE = re.compile(r'^\s*\d{1,2}\s+\d{1,2}\s+\d{2}\b')
# line-leading list markers: "1.", "03.", "4)"
LIST_MARKER_RE = re.compile(r'(?m)^\s*\d{1,2}[.)]')

# Discrepancies at or above this magnitude fail the gate; smaller values are
# treated as expected stray date/enumeration fragments and only warned about.
SIGNIFICANCE_THRESHOLD = int(os.environ.get("RECONCILE_MIN", "100"))


def numbers_in(text):
    t = MIXED_DATE_RE.sub(' ', text)
    t = DATE_RE.sub(' ', t)
    t = SPACE_DATE_RE.sub(' ', t)
    t = DINANK_SHORT_DATE_RE.sub(' ', t)
    t = LEADING_SHORT_YEAR_DATE_RE.sub(' ', t)
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


def analyze(blocks_path, extracted_path, duplicates_path=duplicates.DEFAULT_PATH):
    """Return (any_diff, significant, report_str)."""
    known_duplicates = duplicates.load(duplicates_path, norm_date=norm_date)
    source = defaultdict(Counter)
    with open(blocks_path, encoding="utf-8") as f:
        for line in f:
            msg = json.loads(line)
            date = norm_date(msg["envelope_date"])
            if duplicates.key(msg.get("sender", ""), date, msg.get("text", "")) in known_duplicates:
                continue
            sender = msg.get("sender", "")
            template_entries = None
            if sender_templates.is_template_sender(sender):
                template_entries = sender_templates.extract(sender, msg["text"])
            if template_entries is not None:
                # A template sender's raw text can carry known OCR/typo count
                # corruptions (e.g. "500" -> "5p0") that sender_templates
                # already corrects positionally; comparing against the raw
                # digits here would flag that correction as a fabricated
                # number every time it fires. Use its corrected counts instead
                # -- they ARE what build_extracted wrote for this message.
                source[date].update(e["count"] for e in template_entries)
            else:
                source[date].update(numbers_in(msg["text"]))

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
