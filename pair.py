"""
Deterministic label<->count pairing via local adjacency (no AI).

After the noise is labelled away, a message is a sequence of chant-labels (L) and
counts (N). Their order (label-first vs count-first) is NOT fixed across the
corpus -- and it can even flip partway through the SAME message (a sender writes
"1400 Bismillah" count-first, then "Sidra sarif 28 baar" label-first two lines
later). So the direction is decided locally, not once for the whole stream: scan
left to right, and whenever the current item and its immediate neighbour differ
in kind (one L, one N), that is an unambiguous pair -- consume both and move on.

  * every item is claimed this way, two at a time, with nothing left over ->
    fully paired.
  * otherwise (two labels or two counts land immediately adjacent once the
    already-claimed items are skipped, or a single item is left dangling at the
    end) -> the stream is ambiguous at that point (a chant with no count, a
    count with no chant, or a real multi-count/multi-label pileup). Route the
    WHOLE message to a human; never guess, and never return a partial result.

This is a strict superset of "the whole stream must be globally alternating":
it agrees with that rule whenever the stream IS globally alternating (identical
pairs, identical direction), and only accepts additional streams where the
convention flips at a point that is still structurally unambiguous.

Usage:
    python pair.py <classified.jsonl>
"""
import sys
import json


def stream_from_segments(segs):
    """Ordered L/N stream from a record's segments. L=("L", label), N=("N", int)."""
    stream = []
    for seg in segs:
        role = seg.get("role")
        if role == "chant-label":
            stream.append(("L", seg.get("t", "").strip()))
        elif role == "count":
            digits = "".join(ch for ch in seg.get("t", "") if ch.isdigit())
            stream.append(("N", int(digits)))
    return stream


def pair_stream(stream):
    """Return (pairs, error). pairs = [(label, count), ...]; error=None on success."""
    if not stream:
        return [], None

    kinds = [k for k, _ in stream]
    pairs = []
    i, n = 0, len(stream)
    while i < n:
        if i + 1 >= n:
            return [], f"dangling item at position {i} (no partner): {kinds}"
        k0, v0 = stream[i]
        k1, v1 = stream[i + 1]
        if k0 == k1:
            return [], (f"non-pairable stream at position {i} "
                        f"(two {k0}s in a row): {kinds}")
        pairs.append((v0, v1) if k0 == "L" else (v1, v0))
        i += 2
    return pairs, None


def main():
    try:  # Devanagari labels must survive a Windows cp1252 console
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) != 2:
        print("Usage: python pair.py <classified.jsonl>")
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pairs, err = pair_stream(stream_from_segments(rec.get("segments", [])))
            if err:
                print(f"msg {rec.get('id')}: NEEDS REVIEW -- {err}")
            else:
                for label, count in pairs:
                    print(f"msg {rec.get('id')}: {label!r} = {count}")


if __name__ == "__main__":
    main()
