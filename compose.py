"""
Compositional (token-class) chant matcher.

An ADDITIVE second pass that runs ONLY after the exact-phrase dictionary
(chant_mappings.json) misses. It never overrides an exact match, so it cannot
change any resolution the current pipeline already makes; it can only rescue
phrases that would otherwise fall through to the LLM.

The idea (from the user): most chant names are built from a few recurring words —
`surah`, `darood`, the honorific `sharif`, the connective `e`. Instead of listing
every full-phrase spelling, we list the spelling variants of each WORD once
(`compose_rules.json` -> tokens) and describe each chant as an ordered sequence of
those word-classes (-> chants). Then a phrasing nobody ever enumerated — e.g.
`sure kauser` from SURAH={surah,surh,sure,...} x KAUSER={kauser,koshar,...} —
resolves for free, with no LLM call. Storage is additive; coverage is
multiplicative.

Safety properties (deliberately conservative):
  - A phrase matches only if EVERY word maps to a known token class (unknown word
    -> no match, so a person's name never sneaks in) ...
  - ... AND the class sequence matches EXACTLY ONE chant pattern. If two patterns
    match, the phrase is ambiguous and we return None (let the LLM decide) rather
    than guess.
  - There is no fuzziness beyond the shared simplify(); a genuinely new spelling
    of a DISTINCTIVE word (first time anyone writes it) still misses here and
    goes to the LLM, whose answer is then cached. Compose only recombines KNOWN
    tokens.

No LLM, no randomness -> two runs are identical.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import normalize   # noqa: E402  (reuse the SAME script-aware simplify())

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "compose_rules.json")


def _parse_pattern(pat):
    """'SURAH? KAUSER SHARIF?' -> [('SURAH', True), ('KAUSER', False), ('SHARIF', True)]."""
    out = []
    for tok in pat.split():
        opt = tok.endswith("?")
        out.append((tok[:-1] if opt else tok, opt))
    return out


def load_rules(path=_RULES_PATH):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # reverse index: simplified single word -> token class
    word2cls = {}
    for cls, variants in data["tokens"].items():
        for v in variants:
            key = normalize.simplify(v)
            if not key:
                continue
            if " " in key:            # tokens must be single words; skip stray multiword
                continue
            if key in word2cls and word2cls[key] != cls:
                raise ValueError(
                    f"token collision: {key!r} maps to both "
                    f"{word2cls[key]!r} and {cls!r}")
            word2cls[key] = cls
    drop = {normalize.simplify(w) for w in data.get("drop", [])}
    chants = {c: _parse_pattern(p) for c, p in data["chants"].items()}
    return {"word2cls": word2cls, "drop": drop, "chants": chants}


_RULES = None


def _rules():
    global _RULES
    if _RULES is None:
        _RULES = load_rules()
    return _RULES


def _seq_match(seq, pattern, si=0, pi=0):
    """Does the class sequence `seq` match `pattern` (list of (cls, optional))?"""
    if pi == len(pattern):
        return si == len(seq)
    cls, opt = pattern[pi]
    if si < len(seq) and seq[si] == cls and _seq_match(seq, pattern, si + 1, pi + 1):
        return True
    if opt and _seq_match(seq, pattern, si, pi + 1):
        return True
    return False


def match(phrase, rules=None):
    """Return a canonical chant name, or None. See module docstring for rules."""
    r = rules or _rules()
    seq = []
    for w in normalize.simplify(phrase).split():
        if w in r["drop"]:
            continue
        cls = r["word2cls"].get(w)
        if cls is None:
            return None                 # unknown word -> cannot fully compose
        seq.append(cls)
    if not seq:
        return None
    hits = {canon for canon, pat in r["chants"].items() if _seq_match(seq, pat)}
    if len(hits) == 1:
        return next(iter(hits))
    return None                         # 0 or >1 (ambiguous) -> defer to LLM


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for arg in sys.argv[1:]:
        print(f"{arg!r} -> {match(arg)}")
