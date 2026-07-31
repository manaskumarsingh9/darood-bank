"""
Step 0b of the pipeline: slice the merged `raw/chatlog.txt` into per-week input
files. Fully deterministic (no AI, no API calls).

Usage:
    python split_weeks.py                          # raw/chatlog.txt -> inputs/
    python split_weeks.py --dry-run
    python split_weeks.py --since 2026-04-15       # ignore anything older (sticky)
    python split_weeks.py somefile.txt             # slice a different source

Weeks are Monday-Sunday calendar weeks, named with the existing inputs/ convention:

    inputs/06-to-12-Apr-2026.txt          week inside one month
    inputs/30-Mar-to-05-Apr-2026.txt      week spanning two months
    inputs/29-Dec-2026-to-04-Jan-2027.txt week spanning two years

Making input files by hand still works exactly as before, and this script is
entirely optional. It will **never overwrite a file it did not create**: the week
files it generates are recorded in `raw/state.json`, and any other file in inputs/
is left alone with a warning. The in-progress current week is skipped too, so a
half-captured week is never frozen as final.

`--since` is sticky (remembered in raw/state.json) and drops messages dated before
it. Use it once when switching over from hand-made files, so already-counted dates
are never regenerated under a different filename.
"""
import sys
import os
import json
import argparse
from datetime import date, timedelta

from split_blocks import parse_message_header

# Bidi control characters WhatsApp inserts at the start of copied lines.
BIDI = "‎‏‪‫‬‭‮"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3,
            "fri": 4, "sat": 5, "sun": 6}

RAW_DIR = "raw"
CHATLOG = os.path.join(RAW_DIR, "chatlog.txt")
STATE = os.path.join(RAW_DIR, "state.json")


def strip_leading_bidi(line):
    """Drop the bidi marks WhatsApp puts at the start of each copied line.

    Only the leading run of bidi marks is removed. Whitespace is deliberately
    left alone: continuation lines often start with a space, and that space is
    part of the message body that the resolver goes on to read — stripping it
    would silently alter the text being counted. Urdu/Arabic body text likewise
    keeps whatever directional marks it was sent with.
    """
    return line.lstrip(BIDI)


def parse_envelope_date(raw, default_year=None, month_first=False):
    """Turn an envelope date like '08/04/2026' or '24/02' into a date object.

    WhatsApp uses the phone's locale. These chats are day-first, which is the
    default; --month-first flips it.
    """
    parts = [p for p in raw.replace(".", "/").replace("-", "/").split("/") if p]
    if len(parts) == 2:
        if default_year is None:
            raise ValueError(f"date {raw!r} has no year; pass --year YYYY")
        parts.append(str(default_year))
    if len(parts) != 3:
        raise ValueError(f"unrecognised date {raw!r}")

    a, b, y = int(parts[0]), int(parts[1]), int(parts[2])
    day, month = (b, a) if month_first else (a, b)
    if y < 100:
        y += 2000
    return date(y, month, day)


def week_start(d, first_weekday):
    """Start of the week containing d."""
    return d - timedelta(days=(d.weekday() - first_weekday) % 7)


def week_filename(start, end):
    """Name a week the way the existing inputs/ files are named."""
    if start.year != end.year:
        return (f"{start.day:02d}-{MONTHS[start.month - 1]}-{start.year}"
                f"-to-{end.day:02d}-{MONTHS[end.month - 1]}-{end.year}.txt")
    if start.month != end.month:
        return (f"{start.day:02d}-{MONTHS[start.month - 1]}"
                f"-to-{end.day:02d}-{MONTHS[end.month - 1]}-{end.year}.txt")
    return (f"{start.day:02d}-to-{end.day:02d}"
            f"-{MONTHS[start.month - 1]}-{start.year}.txt")


def load_state(path):
    if not os.path.exists(path):
        return {"since": None, "generated": []}
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("since", None)
    state.setdefault("generated", [])
    return state


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def bucket_lines(lines, default_year, month_first, first_weekday, since=None):
    """Group raw lines into {week_start: [lines]}, dropping anything before since.

    Continuation lines follow their message, so a multi-line message is never
    torn across two week files.
    """
    weeks = {}
    current = None   # week bucket of the message being read
    keeping = False  # whether that message passed the --since floor
    skipped = 0
    dropped = 0
    for raw in lines:
        line = strip_leading_bidi(raw.rstrip("\n"))
        header = parse_message_header(line)
        if header:
            d = parse_envelope_date(header[0], default_year, month_first)
            keeping = since is None or d >= since
            if not keeping:
                dropped += 1
                current = None
                continue
            current = week_start(d, first_weekday)
            weeks.setdefault(current, []).append(line)
        elif current is not None and keeping:
            weeks[current].append(line)
        elif not keeping:
            pass  # continuation of a message below the floor
        else:
            skipped += 1  # preamble before the first message
    return weeks, skipped, dropped


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", default=CHATLOG,
                    help=f"merged chat log to slice (default: {CHATLOG})")
    ap.add_argument("-o", "--out-dir", default="inputs",
                    help="where week files are written (default: inputs)")
    ap.add_argument("--year", type=int, default=None,
                    help="year to assume for dates without one")
    ap.add_argument("--since", default=None,
                    help="ignore messages before this YYYY-MM-DD (remembered)")
    ap.add_argument("--week-start", default="mon", choices=sorted(WEEKDAYS),
                    help="first day of the week (default: mon)")
    ap.add_argument("--month-first", action="store_true",
                    help="dates are mm/dd (US locale) rather than dd/mm")
    ap.add_argument("--include-current", action="store_true",
                    help="also write the in-progress current week")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be written without writing it")
    ap.add_argument("--overwrite", action="store_true",
                    help="also rewrite week files this script did not create")
    ap.add_argument("--state", default=STATE,
                    help=f"provenance/since store (default: {STATE})")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        print(f"No {args.source}. Run: python ingest.py")
        return 1

    state = load_state(args.state)
    if args.since:
        state["since"] = args.since
    since = date.fromisoformat(state["since"]) if state["since"] else None

    with open(args.source, "r", encoding="utf-8") as f:
        lines = f.readlines()

    weeks, skipped, dropped = bucket_lines(
        lines, args.year, args.month_first, WEEKDAYS[args.week_start], since)
    if not weeks:
        print("No messages found in range.")
        return 1

    generated = set(state["generated"])
    processed_dir = os.path.join(args.out_dir, "processed")
    this_week = week_start(date.today(), WEEKDAYS[args.week_start])

    written = skipped_n = protected = 0
    for start in sorted(weeks):
        end = start + timedelta(days=6)
        name = week_filename(start, end)
        path = os.path.join(args.out_dir, name)
        count = sum(1 for ln in weeks[start] if parse_message_header(ln))

        if start >= this_week and not args.include_current:
            print(f"  skip  {name}  ({count} messages, week still in progress)")
            skipped_n += 1
            continue

        if os.path.exists(os.path.join(processed_dir, name)):
            print(f"  skip  {name}  ({count} messages, already processed)")
            skipped_n += 1
            continue

        if os.path.exists(path) and name not in generated and not args.overwrite:
            print(f"  KEEP  {name}  ({count} messages) - existing file was not "
                  f"created by this script, leaving it untouched")
            protected += 1
            continue

        verb = "rewrite" if os.path.exists(path) else "write"
        print(f"  {'would ' + verb if args.dry_run else verb}  "
              f"{name}  ({count} messages)")
        if not args.dry_run:
            os.makedirs(args.out_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(weeks[start]) + "\n")
            generated.add(name)
        written += 1

    if not args.dry_run:
        state["generated"] = sorted(generated)
        save_state(args.state, state)

    tail = [f"{written} week file(s) "
            f"{'to write' if args.dry_run else 'written'}",
            f"{skipped_n} skipped"]
    if protected:
        tail.append(f"{protected} hand-made file(s) protected")
    if dropped:
        tail.append(f"{dropped} message(s) before --since ignored")
    if skipped:
        tail.append(f"{skipped} preamble line(s) ignored")
    print("\n" + ", ".join(tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
