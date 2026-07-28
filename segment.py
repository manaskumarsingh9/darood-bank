"""
Deterministic segmentation (no AI, no randomness).

Turns a message into an ordered list of atoms using NUMBERS as anchors:
    {"kind": "num", "value": <int>}
    {"kind": "phrase", "text": "<verbatim>", "key": "<normalized>"}

Numbers are unambiguous, so the split is identical on every run. Between the
numbers, text is broken into phrases on punctuation and on a small stoplist of
count-units / connectives / greetings (never on plain spaces, so multi-word
chant names like "kalma sharif" stay intact). Person names survive as ordinary
phrases and are dropped later by the resolver.

This is the piece that removes the run-to-run variance: the LLM never produces
the tiling.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reconcile      # noqa: E402  (reuse the date-stripping regexes)
import normalize      # noqa: E402  (reuse simplify() for phrase keys)

# Words that separate phrases and carry no chant/name content: count units,
# connectives/date words, and greeting/closing tokens. NOT honorifics or names
# (those stay in the phrase so the resolver can drop them as non-chants).
SEPARATORS = {
    # count units
    "martba", "martaba", "maratba", "mrtba", "msrtba", "martbaa", "bar", "baar",
    "times", "dafa", "para", "pare", "बार", "मर्तबा", "दफा", "वक्त", "मरतबा",
    "पारे", "पारा",
    # connectives / date words
    "se", "ko", "ki", "ka", "ke", "mein", "main", "aur", "and", "tak", "dinank",
    "aaj", "wala", "wale", "ka",
    "से", "को", "की", "का", "के", "में", "और", "तक", "दिनांक", "आज",
    # greeting / closing
    "assalam", "walekum", "walaikum", "salam", "salaam", "rahmtullahi", "wa",
    "barakatuh", "barkatuhu", "shukriya", "pranam", "namaste", "alhamdulillah",
    "ameen", "amin", "प्रणाम", "नमस्ते", "शुक्रिया",
    # family
    "family", "parivar", "परिवार",
}

_SPLIT_RE = re.compile(r"(\d+)")           # split keeping numbers
# Separators BETWEEN words. Deliberately does NOT use \w (which drops Devanagari
# combining vowel marks); we split only on whitespace/punctuation so scripts stay
# intact, then simplify() normalizes the keys.
_SEP_RE = re.compile(r"[\s.,;=\-:/()\[\]{}!?|~'\"“”‘’।]+")


def _strip_dates(text):
    t = reconcile.MIXED_DATE_RE.sub(" ", text)
    t = reconcile.CONCAT_DDMM_DATE_RE.sub(" ", t)
    t = reconcile.DATE_RE.sub(" ", t)
    t = reconcile.COLON_DATE_RE.sub(" ", t)
    t = reconcile.SPACE_DATE_RE.sub(" ", t)
    t = reconcile.DINANK_SHORT_DATE_RE.sub(" ", t)
    t = reconcile.DINANK_DMY_SHORT_RE.sub(" ", t)
    t = reconcile.LEADING_SHORT_YEAR_DATE_RE.sub(" ", t)
    t = reconcile.LEADING_DASH_GLUED_DATE_RE.sub(" ", t)
    t = reconcile.LIST_MARKER_RE.sub(" ", t)  # drop line-leading "1." "03)" "1-" enumeration
    return t


def _phrases(text_run):
    """Break a non-number text run into phrases, dropping separator words."""
    phrases, cur = [], []
    for w in _SEP_RE.split(text_run):
        if not w:
            continue
        if w.lower() in SEPARATORS:
            if cur:
                phrases.append(" ".join(cur))
                cur = []
        else:
            cur.append(w)
    if cur:
        phrases.append(" ".join(cur))
    return phrases


def atoms(text):
    """Ordered list of num/phrase atoms for a message."""
    out = []
    for i, part in enumerate(_SPLIT_RE.split(_strip_dates(text))):
        if i % 2 == 1:                      # the captured number
            out.append({"kind": "num", "value": int(part)})
        else:
            for ph in _phrases(part):
                out.append({"kind": "phrase", "text": ph,
                            "key": normalize.simplify(ph)})
    return out
