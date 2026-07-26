"""
Deterministic chant-name normalization via a lookup table (no AI, no API).

`normalize(raw_label)` returns a canonical chant name, or None on a miss. A miss
is precisely the signal to invoke the LLM for that one label string, and then add
the new variant to chant_mappings.json so it is deterministic forever after.

The table is intentionally NOT fuzzy -- exact match after script-aware
simplification. simplify() is applied identically to the query and to every
stored variant, so English (Latin), Hindi (Devanagari) and Urdu (Arabic script)
all collapse to a stable key and match. Fuzziness is the LLM's job, and its
answers get cached here.
"""
import os
import re
import json
import unicodedata

_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "chant_mappings.json")

# Arabic/Urdu letter-form unification: the SAME letter is written many ways
# (alef with/without hamza, Arabic vs Farsi/Urdu yeh, kaf vs keheh, the several
# heh forms, teh-marbuta). Map each to one canonical code point so spellings match.
_AR_LETTER = {
    0x0622: 0x0627, 0x0623: 0x0627, 0x0625: 0x0627, 0x0671: 0x0627,  # آ أ إ ٱ -> ا
    0x0649: 0x064A, 0x06CC: 0x064A, 0x0626: 0x064A,                   # ى ی ئ -> ي
    0x06A9: 0x0643,                                                    # ک (keheh) -> ك (kaf)
    0x06C1: 0x0647, 0x06BE: 0x0647, 0x0629: 0x0647,                   # ہ ھ ة -> ه
    0x0624: 0x0648,                                                    # ؤ -> و
}
# tatweel (kashida) + zero-width joiners/marks + BOM: cosmetic, drop them.
_ZW_TATWEEL = "".join(chr(c) for c in
                      (0x0640, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF))


def simplify(s):
    s = unicodedata.normalize("NFC", s).lower()
    # drop all combining marks -- Devanagari matras, Arabic harakat/diacritics --
    # so vowel-mark presence never changes the key (matches with or without them).
    s = "".join(c for c in s if not unicodedata.category(c).startswith("M"))
    s = s.translate(_AR_LETTER)
    s = "".join(c for c in s if c not in _ZW_TATWEEL)
    s = re.sub(r"[^\w\s]", " ", s)   # drop punctuation (\w keeps all scripts' letters)
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
