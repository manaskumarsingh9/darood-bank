"""Durable store of messages that carry no recitation counts at all
(non_chant_messages.json).

Group chats contain administrative chatter -- "please add this number", link
shares, greetings-only posts. These are not chant reports, and the digits they do
contain (phone numbers, most often) are not counts. The resolver would either
strand those digits as a dangling count or, worse, pair one with a nearby word.

A message lands here only when a human has confirmed it reports nothing. This is
deliberately NOT known_duplicates.json: a duplicate is a real report already
counted elsewhere, whereas this is a message that never had counts to begin with.
Keeping them apart keeps each store's audit trail honest.

Shared by build_extracted.py (skips the message) and reconcile.py (excludes its
raw digits from the per-date backstop) so both gates agree.
"""
import os
import json

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "non_chant_messages.json")


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
