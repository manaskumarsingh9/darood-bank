"""
Deterministic accuracy check (no AI, no API calls).

For each send-date it compares two multisets of numbers:
  * SOURCE: every number found in the raw message bodies for that date, after
    removing date expressions and list-item markers.
  * EXTRACTED: the `count` values in extracted.json for that date.

Anything in SOURCE but not EXTRACTED is a *possible missed count*.
Anything in EXTRACTED but not in SOURCE is a *possible typo / hallucination*.

Some noise is expected (stray day/month fragments, enumeration markers). The
point is that any *large, real* count that goes missing will stand out.

Usage:
    python reconcile.py blocks.jsonl extracted.json
"""
import sys
import json
import re
from collections import Counter, defaultdict

# mixed separator then space before year: "26.2 2026", "27.2 2026"
MIXED_DATE_RE = re.compile(r'\d{1,2}\s*[./,=\-]\s*\d{1,2}\s+20\d\d')
# 24.2.26  24/02/2026  24-02-026  24=2=2026  24,2, 26  27 - 02- 26
DATE_RE = re.compile(r'\d{1,2}\s*[./,=\-]\s*\d{1,2}\s*[./,=\-]\s*\d{2,4}')
# space-separated trailing date like "27 2 2025"
SPACE_DATE_RE = re.compile(r'\b\d{1,2}\s+\d{1,2}\s+20\d\d\b')
# line-leading list markers: "1.", "03.", "4)"
LIST_MARKER_RE = re.compile(r'(?m)^\s*\d{1,2}[.)]')


def numbers_in(text):
    t = MIXED_DATE_RE.sub(' ', text)
    t = DATE_RE.sub(' ', t)
    t = SPACE_DATE_RE.sub(' ', t)
    t = LIST_MARKER_RE.sub(' ', t)
    return [int(n) for n in re.findall(r'\d+', t)]


def norm_date(envelope_date):
    # blocks store "24/02"; extracted store "24/02/2026"
    parts = re.split(r'[./-]', envelope_date)
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}/2026"
    return envelope_date


def main():
    blocks_path, extracted_path = sys.argv[1], sys.argv[2]

    source = defaultdict(Counter)
    with open(blocks_path, encoding="utf-8") as f:
        for line in f:
            msg = json.loads(line)
            date = norm_date(msg["envelope_date"])
            source[date].update(numbers_in(msg["text"]))

    extracted = defaultdict(Counter)
    with open(extracted_path, encoding="utf-8") as f:
        for e in json.load(f):
            extracted[str(e["date"])].update([int(e["count"])])

    clean = True
    for date in sorted(set(source) | set(extracted)):
        missed = source[date] - extracted[date]   # in source, not extracted
        extra = extracted[date] - source[date]     # extracted, not in source
        if not missed and not extra:
            continue
        clean = False
        print(f"\n=== {date} ===")
        if missed:
            print("  possible MISSED counts (in message, not extracted):")
            print("    " + ", ".join(f"{v}x{c}" for v, c in sorted(missed.items())))
        if extra:
            print("  possible TYPO/EXTRA counts (extracted, not in message):")
            print("    " + ", ".join(f"{v}x{c}" for v, c in sorted(extra.items())))

    if clean:
        print("CLEAN: every extracted count is backed by a number in the source, "
              "and no source number is unaccounted for.")
    else:
        print("\n(Interpret: large/real counts flagged above are worth checking; "
              "small stray day/month fragments are expected noise.)")


if __name__ == "__main__":
    main()
