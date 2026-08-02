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
    "aaj", "wale", "ka",
    # NOTE: "wala" was formerly a separator here. It is an integral word of the
    # dhikr "lahola WALA quvvata illa billa hil alliyil azeem", and splitting on
    # it tore that chant into two unmatchable halves, stranding its count.
    "से", "को", "की", "का", "के", "में", "और", "तक", "दिनांक", "आज",
    # greeting / closing
    "assalam", "walekum", "walaikum", "salam", "salaam", "rahmtullahi", "wa",
    "barakatuh", "barkatuhu", "shukriya", "pranam", "namaste",
    "ameen", "amin", "प्रणाम", "नमस्ते", "शुक्रिया",
    # NOTE: "alhamdulillah" was formerly listed here as a greeting/closing token.
    # It is NOT a separator -- it is a counted dhikr in its own right (canonical
    # `Alhamdulillah`), and stripping it here deleted the word before the resolver
    # could match it, leaving its count dangling. Every occurrence in the corpus is
    # a counted recitation, none is a bare interjection.
    # family
    "family", "parivar", "परिवार",
}

# Name-suffix words. In this group people are written "<given name> bi/bee/bano"
# or "<given name> Patel", and a report often runs several people together with no
# punctuation: "1 martaba Yaseen Sharif nafisa bi durood Sharif 500 martaba".
# Longest-match alone keeps only ONE chant out of such a phrase and strands the
# other's count, so the suffix is treated as a phrase boundary that also swallows
# the single word before it (the given name):
#   "Yaseen Sharif nafisa bi durood Sharif" -> "Yaseen Sharif" | "durood Sharif"
# Dropping only ONE preceding word is what keeps the classic case correct:
#   "bismillah bi durood sharif" -> "" | "durood sharif"  (DAROOD, never BISMILLAH)
# i.e. "Bismillah Bi" is a woman's name, not the chant BISMILLAH SHARIF.
NAME_SUFFIXES = {"bi", "bee", "bano", "patel", "begum", "khatun", "khatoon"}

# Word-number expansion lives in reconcile so that BOTH this module and
# reconcile.numbers_in() apply it identically -- see reconcile.expand_word_numbers.
_SPLIT_RE = re.compile(r"(\d+)")           # split keeping numbers
# Separators BETWEEN words. Deliberately does NOT use \w (which drops Devanagari
# combining vowel marks); we split only on whitespace/punctuation so scripts stay
# intact, then simplify() normalizes the keys.
_SEP_RE = re.compile(r"[\s.,;=\-:/()\[\]{}!?|~'\"“”‘’।]+")


def _strip_dates(text, ref_year=None):
    # Value-based triple rule first (separator-agnostic); the patterns below then
    # mop up the 2-number and keyword-anchored forms it deliberately ignores.
    # Must stay identical to reconcile.numbers_in() or the per-date multiset gate
    # would compare two different sets of numbers against each other.
    t = reconcile.strip_date_triples(text, ref_year)
    t = reconcile.MIXED_DATE_RE.sub(" ", t)
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
        low = w.lower()
        if low in SEPARATORS:
            if cur:
                phrases.append(" ".join(cur))
                cur = []
        elif low in NAME_SUFFIXES:
            # "<given name> bi" -> the person, not a chant. Drop the given name
            # (one word) and close the phrase; whatever follows starts fresh.
            if cur:
                cur.pop()
            if cur:
                phrases.append(" ".join(cur))
            cur = []
        else:
            cur.append(w)
    if cur:
        phrases.append(" ".join(cur))
    return phrases




def atoms(text, ref_year=None):
    """Ordered list of num/phrase atoms for a message.

    `ref_year` is the message's own send year, used to judge whether a 2-digit
    year in a number triple is plausible. Pass it whenever the record is known.
    """
    out = []
    prepared = reconcile.expand_word_numbers(_strip_dates(text, ref_year))
    for i, part in enumerate(_SPLIT_RE.split(prepared)):
        if i % 2 == 1:                      # the captured number
            out.append({"kind": "num", "value": int(part)})
        else:
            for ph in _phrases(part):
                out.append({"kind": "phrase", "text": ph,
                            "key": normalize.simplify(ph)})
    return out
