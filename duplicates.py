"""Durable store of confirmed duplicate/superseded sends (known_duplicates.json).

A message lands here only when a human has confirmed it is an incomplete or
redundant resend whose numbers are already fully covered by another, clean
message in the same file (see CLAUDE.md step 3). Shared by build_extracted.py
(excludes the message entirely) and reconcile.py (excludes its raw numbers from
the per-date backstop) so both gates agree on what counts as "handled".
"""
import os
import json

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "known_duplicates.json")


def key(sender, date, text):
    return (sender.strip(), date, text.strip())


def load(path=DEFAULT_PATH, norm_date=None):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    if norm_date is None:
        def norm_date(d):
            return d
    return {
        key(r["sender"], norm_date(r["envelope_date"]), r["text"]): r["reason"]
        for r in records
    }
