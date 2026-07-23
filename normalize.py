"""
Deterministic chant-name normalization via a lookup table (no AI, no API).

`normalize(raw_label)` returns a canonical chant name, or None on a miss. A miss
is precisely the signal to invoke the LLM for that one label string, and then add
the new variant to chant_mappings.json so it is deterministic forever after.

The table is intentionally NOT fuzzy -- exact match after light simplification
(lowercase, strip punctuation/digits, collapse whitespace). Fuzziness is the
LLM's job, and its answers get cached here.
"""
import os
import re
import json

_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "chant_mappings.json")


def simplify(s):
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)   # drop punctuation (unicode-aware \w keeps Devanagari)
    s = re.sub(r"\d+", " ", s)        # drop stray digits
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_table(path=_MAP_PATH):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lookup = {}
    for canonical, variants in data.items():
        lookup[simplify(canonical)] = canonical
        for v in variants:
            lookup[simplify(v)] = canonical
    return lookup


_LOOKUP = None


def normalize(raw, lookup=None):
    global _LOOKUP
    if lookup is None:
        if _LOOKUP is None:
            _LOOKUP = load_table()
        lookup = _LOOKUP
    return lookup.get(simplify(raw))


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    lut = load_table()
    for arg in sys.argv[1:]:
        print(f"{arg!r} -> {normalize(arg, lut)}")
