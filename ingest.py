"""
Step 0a of the pipeline: merge pasted WhatsApp chunks into one canonical
`raw/chatlog.txt`. Fully deterministic (no AI, no API calls).

Usage:
    python ingest.py                      # ingest every .txt in raw/inbox/
    python ingest.py somepaste.txt        # ingest specific files
    python ingest.py --dry-run
    python ingest.py --year 2026          # if pasted dates lack a year

The Darood Bank group has "Export chat" disabled, so messages have to be copied
out of WhatsApp by hand: select messages -> Copy -> paste into a new .txt under
`raw/inbox/`. That is the only manual step. This script then merges every paste
into `raw/chatlog.txt`, dropping anything it has already seen.

**Overlapping pastes are safe, and that is the point.** Scrolling up through a
group and selecting as you go inevitably re-copies messages you already grabbed.
Duplicates are identified exactly (same timestamp, sender and body) and collapsed,
so you never have to track where you left off — copy generously and paste often.

Messages are stored in timestamp order regardless of the order you paste them in,
so working backwards through the group's history is fine.
"""
import sys
import os
import re
import argparse

from split_blocks import parse_message_header_full
from split_weeks import strip_leading_bidi, parse_envelope_date

RAW_DIR = "raw"
CHATLOG = os.path.join(RAW_DIR, "chatlog.txt")
INBOX = os.path.join(RAW_DIR, "inbox")
CONSUMED = os.path.join(INBOX, "consumed")

TIME_RE = re.compile(
    r'^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap])\.?m\.?$',
    re.IGNORECASE,
)

# A message header appearing mid-line, which happens when one paste is appended
# to another without a newline between them.
EMBEDDED_HEADER = re.compile(
    r'(?=\[\s*(?:\d{1,2}:\d{2}(?::\d{2})?\s*[ap]\.?m\.?\s*,'
    r'|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\s*,))',
    re.IGNORECASE,
)


def split_glued(line):
    """Break a line that has a second message's header buried inside it.

    Pasting one chunk straight after another without a newline silently welds
    two messages together, which would file the second sender's counts under the
    first sender and date. A fragment is only split off when it genuinely parses
    as a header, so bracketed text inside a normal message is left alone.
    """
    parts = [p for p in EMBEDDED_HEADER.split(line) if p]
    if len(parts) <= 1:
        return [line]
    out = [parts[0]]
    for part in parts[1:]:
        if parse_message_header_full(part):
            out.append(part)
        else:
            out[-1] += part
    return out


def parse_time(raw):
    """'4:48 am' -> (4, 48, 0) in 24-hour form, for sorting."""
    # WhatsApp sometimes uses a narrow no-break space before am/pm.
    m = TIME_RE.match(raw.replace(" ", " ").replace(" ", " ").strip())
    if not m:
        raise ValueError(f"unrecognised time {raw!r}")
    hour, minute, second, meridiem = m.groups()
    hour = int(hour) % 12
    if meridiem.lower() == "p":
        hour += 12
    return hour, int(minute), int(second or 0)


def read_messages(lines, default_year, source):
    """Group raw pasted lines into messages.

    Continuation lines stay attached to their message, so the multi-line
    Devanagari/Urdu chant lists are never torn apart. Returns a list of dicts
    carrying both a normalised identity (for dedupe/sort) and the original lines
    (so the chatlog stays a faithful copy of what was posted).
    """
    messages = []
    current = None
    skipped = 0
    for raw in lines:
        for line in split_glued(strip_leading_bidi(raw.rstrip("\n"))):
            header = parse_message_header_full(line)
            if header:
                time_raw, date_raw, sender, text = header
                current = {
                    "date": parse_envelope_date(date_raw, default_year),
                    "time": parse_time(time_raw),
                    "sender": sender,
                    "body": [text],
                    "lines": [line],
                    "source": source,
                }
                messages.append(current)
            elif current is not None:
                current["body"].append(line)
                current["lines"].append(line)
            else:
                skipped += 1  # WhatsApp preamble before the first message
    return messages, skipped


def identity(msg):
    """Exact key two copies of the same message must share."""
    return (msg["date"], msg["time"], msg["sender"], body_text(msg))


def body_text(msg):
    return "\n".join(msg["body"]).strip()


def drop_truncated(messages):
    """Remove partial copies of a message left behind by a cut-off paste.

    A paste that ends mid-message yields a message with the same timestamp and
    sender but a shortened body. Exact-match dedupe cannot see that, and keeping
    it would count its numbers a second time.

    Only copies from *different* pastes are considered. Within a single paste a
    prefix relationship is real data — senders do post one short message per
    person in the same minute, and one entry's text can legitimately be a prefix
    of another's — so those are always left alone.
    """
    groups = {}
    for msg in messages:
        groups.setdefault((msg["date"], msg["time"], msg["sender"]), []).append(msg)

    dropped = []
    for group in groups.values():
        if len(group) < 2:
            continue
        for msg in group:
            text = body_text(msg)
            if any(other is not msg
                   and other["source"] != msg["source"]
                   and body_text(other).startswith(text)
                   and len(body_text(other)) > len(text)
                   for other in group):
                dropped.append(msg)

    kept = [m for m in messages if not any(m is d for d in dropped)]
    return kept, len(dropped)


def merge(existing, chunk):
    """Fold one pasted chunk into the messages already stored.

    Repeats are resolved by **multiplicity, not presence**: for each distinct
    message the result keeps as many copies as the fullest single source had.
    Two overlapping pastes that each contain a message once yield one copy, but a
    sender who genuinely posted the same line twice in the same minute keeps
    both — collapsing those would silently drop a real count.

    (If the two genuine copies are split across a paste boundary so that no
    single paste holds both, only one survives. Overlap your selections and that
    cannot happen.)
    """
    counts = {}
    for msg in existing:
        counts[identity(msg)] = counts.get(identity(msg), 0) + 1

    by_key = {}
    for msg in chunk:
        by_key.setdefault(identity(msg), []).append(msg)

    order = list(existing)
    added = 0
    for key, msgs in by_key.items():
        shortfall = len(msgs) - counts.get(key, 0)
        for msg in msgs[:max(0, shortfall)]:
            order.append(msg)
            added += 1

    order, truncated = drop_truncated(order)
    order.sort(key=lambda m: (m["date"], m["time"]))
    return order, added, truncated


def load_chatlog(path, default_year):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        messages, _ = read_messages(f.readlines(), default_year, path)
    return messages


def collect_sources(args):
    if args.files:
        return list(args.files)
    if not os.path.isdir(INBOX):
        return []
    return sorted(
        os.path.join(INBOX, n)
        for n in os.listdir(INBOX)
        if n.lower().endswith(".txt")
        and os.path.isfile(os.path.join(INBOX, n))
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*",
                    help=f"paste files to ingest (default: every .txt in {INBOX})")
    ap.add_argument("--chatlog", default=CHATLOG,
                    help=f"canonical merged log (default: {CHATLOG})")
    ap.add_argument("--year", type=int, default=None,
                    help="year to assume for pasted dates without one")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be merged without writing")
    ap.add_argument("--keep", action="store_true",
                    help=f"leave ingested files in place instead of moving to {CONSUMED}")
    args = ap.parse_args()

    sources = collect_sources(args)
    if not sources:
        print(f"Nothing to ingest. Paste copied messages into a .txt under {INBOX}/")
        return 0

    existing = load_chatlog(args.chatlog, args.year)
    print(f"chatlog: {len(existing)} message(s) already stored")

    # Merged one paste at a time: repeat counting is per-paste (see merge()).
    merged = existing
    total_in = truncated = 0
    for path in sources:
        with open(path, "r", encoding="utf-8") as f:
            msgs, skipped = read_messages(f.readlines(), args.year, path)
        if not msgs:
            print(f"  !! {path}: no messages found - is this a WhatsApp copy?")
            continue
        note = f", {skipped} preamble line(s) ignored" if skipped else ""
        print(f"  -> {path}: {len(msgs)} message(s){note}")
        total_in += len(msgs)
        merged, _added, trunc = merge(merged, msgs)
        truncated += trunc

    if not total_in:
        print("No messages to merge.")
        return 0

    new_count = len(merged) - len(existing)
    dupes = total_in - new_count - truncated

    print(f"\n{new_count} new, {dupes} already-seen copy(ies) skipped"
          + (f", {truncated} truncated copy(ies) dropped" if truncated else "")
          + f" -> {len(merged)} total")
    if merged:
        print(f"range: {merged[0]['date']} .. {merged[-1]['date']}")

    if args.dry_run:
        print("(dry run — nothing written)")
        return 0

    os.makedirs(os.path.dirname(args.chatlog) or ".", exist_ok=True)
    tmp = args.chatlog + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for msg in merged:
            f.write("\n".join(msg["lines"]) + "\n")
    os.replace(tmp, args.chatlog)
    print(f"wrote {args.chatlog}")

    from_inbox = [p for p in sources
                  if os.path.normpath(os.path.dirname(p)) == os.path.normpath(INBOX)]
    if from_inbox and not args.keep:
        os.makedirs(CONSUMED, exist_ok=True)
        for path in from_inbox:
            os.replace(path, os.path.join(CONSUMED, os.path.basename(path)))
        print(f"moved {len(from_inbox)} ingested file(s) to {CONSUMED}/")

    print(f"\nNext: python split_weeks.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
