"""Durable store of confirmed raw-text typo corrections (text_corrections.json).

Some messages contain a typo in the DIGITS themselves, which no spelling store can
fix: a count split by a stray space ("Sure iklas 9 1 martba" -- one number typed as
two), or a spurious extra number ("11000 11 मर्तबा दरूद शरीफ"). These are not new
chant spellings and not duplicates, so neither chant_mappings.json nor
known_duplicates.json is the right home.

An entry replaces the message's raw text with a corrected text, keyed by the exact
(sender, envelope_date, text) triple. Both build_extracted.py and reconcile.py
apply it, so the resolver and the per-date backstop see the same digits and the
gate cannot disagree with itself.

HOW A CORRECTION IS DECIDED (see CLAUDE.md "Resolving a typo'd count"):
never by guessing at the message alone. Run `python count_profile.py <blocks.jsonl>
--sender "<sender>"` to print what this sender/person actually reports for that
chant across the whole corpus, and read the candidate against that distribution.
A sender whose Darood counts are always 4-digit did not suddenly send 11. The
judgement is made ONCE, recorded here with its evidence, and is deterministic
forever after.

Keep this store small and evidence-backed. It is an exception list, not a
substitute for fixing segmentation.
"""
import os
import json

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "text_corrections.json")


def key(sender, date, text):
    return (sender.strip(), date, text.strip())


def load(path=DEFAULT_PATH, norm_date=None):
    """Map (sender, date, text) -> {"corrected_text", "reason"}."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    if norm_date is None:
        def norm_date(d):
            return d
    return {
        key(r["sender"], norm_date(r["envelope_date"]), r["text"]):
            {"corrected_text": r["corrected_text"], "reason": r["reason"]}
        for r in records
    }


def apply_to(sender, date, text, corrections):
    """Return (text_to_use, reason_or_None)."""
    hit = corrections.get(key(sender, date, text))
    if hit is None:
        return text, None
    return hit["corrected_text"], hit["reason"]
