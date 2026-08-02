"""
Deterministic resolver over the segmented atoms (no AI, no randomness).

This is the core of the promoted pipeline: it replaces the old per-message LLM
labeling step. build_extracted.py drives it over blocks.jsonl.

For each message:
  1. Segment deterministically (segment.atoms).
  2. Classify each phrase:
       - known chant variant (chant_mappings.json exact, then compose.match) -> L
       - anything else                                                       -> dropped
  3. Build the L/N stream and pair by the alternation rule (pair.pair_stream).

Key property: an UNKNOWN phrase is dropped, NOT flagged — but its neighbouring
number is kept. So if a dropped phrase was actually a new chant spelling, its
number is left unpaired and the alternation check FAILS, surfacing the message
for review. A real chant can never be silently dropped; it either resolves or
trips a flag. And because there is no LLM in this path, two runs are identical.

A message resolves cleanly, or it lands in one of:
  - "flag": non-alternating stream (dangling count, dropped-chant, or multi-count)

Usage:
    python resolve.py <blocks.jsonl>
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import normalize   # noqa: E402
import pair        # noqa: E402
import reconcile   # noqa: E402
import segment     # noqa: E402
import compose     # noqa: E402  (additive compositional fallback)

# Optional extra decisions for phrases that are NOT chants but recur (kept for
# symmetry / future use). Unknown phrases already default to "drop", so this is
# only needed if we ever want to force a phrase to a specific non-chant role.
_DEC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "phrase_decisions.json")


def _load_decisions():
    if os.path.exists(_DEC_PATH):
        with open(_DEC_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _lookup(text, decisions):
    canon = normalize.normalize(text)          # 1. exact-phrase dictionary
    if canon:
        return canon
    dec = decisions.get(normalize.simplify(text))
    if dec and dec.get("role") == "chant":     # 2. cached phrase decision
        return dec["canonical"]
    return compose.match(text)                 # 3. compositional fallback (or None)


def _tile(words, decisions):
    """Try to cover `words` end-to-end with back-to-back known chants and
    nothing else. Returns the list of canonicals in order, or None if any
    word is left unconsumed (greedy longest-match-first at each position)."""
    i, n, out = 0, len(words), []
    while i < n:
        for j in range(n, i, -1):
            canon = _lookup(" ".join(words[i:j]), decisions)
            if canon:
                out.append(canon)
                i = j
                break
        else:
            return None
    return out


def classify(phrase, decisions):
    """Return the list of canonical chants found in `phrase` (possibly empty).

    A phrase is usually "<name> <chant>" or "<chant> <name>" with no delimiter,
    so the default is the LONGEST contiguous word-subsequence that is a
    known chant; the surrounding words are the person's name and are discarded.
    Longest-match makes a real multi-word chant win over a coincidental single
    name-word (e.g. person "Bismillah Bi durood sharif" -> DAROOD, not BISMILLAH).

    But two real chants are sometimes written back-to-back with no number
    between them ("1400 Bismillah / Sidra sarif 28 baar" -- no count separates
    the two names). That can only be told apart from the name+chant case by
    there being NO leftover word once every word is claimed by some chant, so
    a full end-to-end tiling is tried FIRST; only that unambiguous case yields
    more than one chant out of a single phrase.
    """
    words = phrase.split()
    tiled = _tile(words, decisions)
    if tiled is not None:
        return tiled

    best, best_len = None, 0
    for i in range(len(words)):
        for j in range(len(words), i, -1):
            if j - i <= best_len:
                break
            canon = _lookup(" ".join(words[i:j]), decisions)
            if canon:
                best, best_len = canon, j - i
                break
    return [best] if best else []


def resolve_message(rec, decisions):
    """Return (entries, flag, dropped_phrases)."""
    date = reconcile.norm_date(rec.get("envelope_date", ""))
    ref_year = reconcile.year_of(rec.get("envelope_date", ""))
    stream, dropped = [], []
    for a in segment.atoms(rec.get("text", ""), ref_year):
        if a["kind"] == "num":
            stream.append(("N", a["value"]))
        else:
            canons = classify(a["text"], decisions)
            if canons:
                stream.extend(("L", canon) for canon in canons)
            else:
                dropped.append(a["text"])
    pairs, err = pair.pair_stream(stream)
    if err:
        rescued = _drop_uncounted_trailing_label(stream)
        if rescued is not None:
            pairs, err = rescued, None
    if err:
        pairs = _carry_forward(stream)   # header-chant fallback
        if pairs is None:
            return [], err, dropped
    entries = [{"date": date, "chant": c, "count": n} for c, n in pairs]
    return entries, None, dropped


def _drop_uncounted_trailing_label(stream):
    """Rescue a message whose ONLY defect is a chant named last with no count.

    Senders sometimes trail off: "...Surye iklhas 75 martba toba astagfirullah"
    -- the final chant is named but its count was never typed. That lone label
    breaks the alternation and would discard the message's other three counts,
    which are perfectly good.

    Dropping the label loses NO number (there was none to lose) and cannot invent
    one, so it is safe in a way that guessing a count would not be. Deliberately
    narrow: it fires only when the LAST item is a label and removing exactly that
    one item makes the whole remaining stream pair cleanly. Anything else -- a
    dangling count, a label stranded mid-message -- still flags for a human.
    """
    if len(stream) < 2 or stream[-1][0] != "L":
        return None
    pairs, err = pair.pair_stream(stream[:-1])
    return None if err else pairs


def _carry_forward(stream):
    """Header-chant fallback for the ONE unambiguous case: a single chant named
    once as a header, followed by several people's counts
    ('Gayatri Mantra:  Person A 108  Person B 363  Person C 108').

    Deliberately narrow: it fires only when the stream contains exactly one
    distinct chant and starts with it. A stray trailing count after several
    different chants (a genuinely dangling, unattributable number) has >1 chant
    in the stream, so it is NOT swallowed here — it stays flagged for a human."""
    labels = [v for k, v in stream if k == "L"]
    nums = [v for k, v in stream if k == "N"]
    if len(set(labels)) != 1 or not nums or stream[0][0] != "L":
        return None
    chant = labels[0]
    return [(chant, n) for n in nums]


def resolve_all(blocks_path, decisions=None):
    if decisions is None:
        decisions = _load_decisions()
    entries, flagged = [], {}
    with open(blocks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            es, flag, _ = resolve_message(rec, decisions)
            entries.extend(es)
            if flag:
                flagged[rec["id"]] = flag
    return entries, flagged


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    entries, flagged = resolve_all(sys.argv[1])
    print(f"resolved entries: {len(entries)} | flagged messages: {len(flagged)}")
    for mid, flag in sorted(flagged.items()):
        print(f"  flag msg {mid}: {flag}")


if __name__ == "__main__":
    main()
