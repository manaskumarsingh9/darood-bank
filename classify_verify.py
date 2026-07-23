"""
Deterministic verification of a classified.jsonl labeling layer (no AI, no API).

Given the LLM's per-message segmentation, this proves the labels are trustworthy
BEFORE any pairing or aggregation happens:

  * reconstruction -- the segments, concatenated in order, must rebuild the
    original message (whitespace aside). Catches invented/dropped/altered text.
  * number census  -- the multiset of numbers across the segments must equal the
    multiset in the original. No count can silently vanish or appear.
  * count shape    -- a `count` segment holds exactly one number.
  * uncertain      -- any `uncertain` segment is routed to a human.
  * date sanity    -- a `count` that looks like a year, or a `date` segment with
    a non-date-looking number, is flagged (soft) for review.

HARD flags mean the message cannot be trusted for automated aggregation.
SOFT flags are worth a human glance but do not block.

Usage:
    python classify_verify.py <classified.jsonl>
Exit code: 1 if any HARD flag, else 0.
"""
import sys
import json
import re
from collections import Counter

CONTENT_ROLES = {"chant-label", "count"}
DROP_ROLES = {"filler", "name", "date", "greeting", "phone", "list-marker", "other"}
REVIEW_ROLES = {"uncertain"}
VALID_ROLES = CONTENT_ROLES | DROP_ROLES | REVIEW_ROLES

WS_RE = re.compile(r"\s+")
NUM_RE = re.compile(r"\d+")


def _no_ws(s):
    return WS_RE.sub("", s)


def _nums(s):
    return [int(n) for n in NUM_RE.findall(s)]


class Flag:
    def __init__(self, level, code, msg):
        self.level = level  # "hard" or "soft"
        self.code = code
        self.msg = msg

    def __str__(self):
        return f"[{self.level.upper()}] {self.code}: {self.msg}"

    __repr__ = __str__


def _envelope_parts(env):
    parts = [int(p) for p in re.split(r"[./-]", env) if p.isdigit()]
    day = parts[0] if len(parts) > 0 else None
    month = parts[1] if len(parts) > 1 else None
    year = parts[2] if len(parts) > 2 else 2026
    return day, month, year


def verify_record(rec):
    """Return a list of Flag for one classified message record."""
    flags = []
    mid = rec.get("id")
    segs = rec.get("segments", [])
    text = rec.get("text", "")

    # -- role validity + uncertain routing
    for seg in segs:
        role = seg.get("role")
        if role not in VALID_ROLES:
            flags.append(Flag("hard", "bad-role",
                              f"msg {mid}: unknown role {role!r} on {seg.get('t')!r}"))
        if role in REVIEW_ROLES:
            flags.append(Flag("hard", "uncertain",
                              f"msg {mid}: uncertain segment {seg.get('t')!r} needs a human"))

    # -- reconstruction (tiling)
    concat = "".join(seg.get("t", "") for seg in segs)
    if _no_ws(concat) != _no_ws(text):
        flags.append(Flag("hard", "reconstruction",
                          f"msg {mid}: segments do not reconstruct the original "
                          "text (something added, dropped, or altered)"))

    # -- number census
    orig = Counter(_nums(text))
    seg_all = Counter()
    for seg in segs:
        seg_all.update(_nums(seg.get("t", "")))
    if orig != seg_all:
        missing = dict(orig - seg_all)
        extra = dict(seg_all - orig)
        flags.append(Flag("hard", "census",
                          f"msg {mid}: number census mismatch "
                          f"missing={missing} extra={extra}"))

    # -- count shape (exactly one number per count segment)
    for seg in segs:
        if seg.get("role") == "count":
            ns = _nums(seg.get("t", ""))
            if len(ns) != 1:
                flags.append(Flag("hard", "count-shape",
                                  f"msg {mid}: count segment {seg.get('t')!r} must "
                                  "hold exactly one number"))

    # -- date plausibility (soft)
    _day, _month, _year = _envelope_parts(rec.get("envelope_date", ""))
    for seg in segs:
        role = seg.get("role")
        if role == "count":
            for v in _nums(seg.get("t", "")):
                if 2020 <= v <= 2030:
                    flags.append(Flag("soft", "count-yearlike",
                                      f"msg {mid}: count {v} looks like a year/date "
                                      "-- verify it is really a count"))
        elif role == "date":
            for v in _nums(seg.get("t", "")):
                if 100 <= v <= 1999 or v > 2099:
                    flags.append(Flag("soft", "date-oddnumber",
                                      f"msg {mid}: date segment holds non-date-looking "
                                      f"number {v} -- verify it is not a count"))

    return flags


def load(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def verify_all(recs):
    flags = []
    for rec in recs:
        flags.extend(verify_record(rec))
    return flags


def main():
    try:  # flags can quote Devanagari text; survive a Windows cp1252 console
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) != 2:
        print("Usage: python classify_verify.py <classified.jsonl>")
        sys.exit(2)
    recs = load(sys.argv[1])
    flags = verify_all(recs)
    hard = [f for f in flags if f.level == "hard"]
    soft = [f for f in flags if f.level == "soft"]
    for f in flags:
        print(f)
    print(f"\n{len(recs)} messages checked | {len(hard)} hard, {len(soft)} soft flags")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
