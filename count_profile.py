"""Evidence tool for judging a typo'd count (no AI, read-only, changes nothing).

When a message contains a count that cannot be right -- a number split by a stray
space ("9 1"), a spurious extra number ("11000 11"), a digit swallowed by a letter
-- the message alone cannot settle what was meant. What settles it is the sender's
OWN history: a person whose Darood counts are always 4-digit did not suddenly send
11, and a person who wrote "Sure ikhlas 91" three weeks earlier very likely meant
91 again.

This prints that history, so the judgement is made against evidence and the same
way every time, instead of ad-hoc grepping. It does NOT decide anything: the agent
reads the profile, decides once, and records the decision (with the evidence) in
text_corrections.json, which is deterministic from then on.

Usage:
    python count_profile.py blocks.jsonl --sender "+91 98934 59753"
    python count_profile.py blocks.jsonl --sender "Bashir Patel Uncle Sufi" \
                                         --name "निजामुद्दीन"
    python count_profile.py blocks.jsonl --sender "..." --chant "SURAH IKHLAS"
"""
import os
import sys
import json
import argparse
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resolve      # noqa: E402


def _load(blocks_path):
    out = []
    with open(blocks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def profile(blocks_path, sender=None, name=None, chant=None):
    decisions = resolve._load_decisions()
    per_chant = defaultdict(list)          # chant -> [(date, count), ...]
    unresolved = 0

    for rec in _load(blocks_path):
        if sender and rec.get("sender", "").strip() != sender.strip():
            continue
        if name and name not in rec.get("text", ""):
            continue
        entries, flag, _ = resolve.resolve_message(rec, decisions)
        if flag:
            unresolved += 1
            continue
        for e in entries:
            if chant and e["chant"] != chant:
                continue
            per_chant[e["chant"]].append((e["date"], e["count"]))
    return per_chant, unresolved


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blocks")
    ap.add_argument("--sender", help="exact sender string to profile")
    ap.add_argument("--name", help="only messages containing this person name")
    ap.add_argument("--chant", help="restrict to one canonical chant")
    args = ap.parse_args()

    per_chant, unresolved = profile(args.blocks, args.sender, args.name, args.chant)
    if not per_chant:
        print("no resolved entries matched that filter")
        if unresolved:
            print(f"({unresolved} message(s) matched but are themselves unresolved)")
        return

    scope = " / ".join(x for x in (args.sender, args.name) if x)
    print(f"count profile for: {scope or 'ALL SENDERS'}")
    if unresolved:
        print(f"(ignoring {unresolved} still-unresolved message(s) from this scope)")

    for chant in sorted(per_chant, key=lambda c: -len(per_chant[c])):
        rows = sorted(per_chant[chant])
        counts = [c for _, c in rows]
        widths = Counter(len(str(c)) for c in counts)
        print(f"\n--- {chant}  ({len(counts)} reports) ---")
        print(f"  distinct values : {sorted(set(counts))}")
        print(f"  min / max       : {min(counts)} / {max(counts)}")
        print("  digit lengths   : " +
              ", ".join(f"{w}-digit x{n}" for w, n in sorted(widths.items())))
        print(f"  most common     : "
              f"{', '.join(f'{v} (x{n})' for v, n in Counter(counts).most_common(6))}")


if __name__ == "__main__":
    main()
