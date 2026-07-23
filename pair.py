"""
Deterministic label<->count pairing via the alternation/elimination rule (no AI).

After the noise is labelled away, a message is a sequence of chant-labels (L) and
counts (N). Their order (label-first vs count-first) is NOT fixed across the
corpus, but it does not need to be assumed: a correctly-labelled stream is
strictly alternating and balanced, so exactly one direction pairs everything with
nothing left over. That is the "try both, the wrong one leaves a dangling item"
idea, stated directly:

  * strictly alternating AND equal #L and #N  -> pair adjacent items; direction
    is decided by whether the stream starts with a label or a count.
  * otherwise (two labels or two counts in a row, or unequal counts) -> the
    stream is ambiguous (a chant with no count, a count with no chant, or a strip
    error). Route that message to a human; never guess.

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
    alternating = all(kinds[i] != kinds[i + 1] for i in range(len(kinds) - 1))
    balanced = kinds.count("L") == kinds.count("N")
    if not (alternating and balanced):
        return [], (f"non-pairable stream (alternating={alternating}, "
                    f"balanced={balanced}): {kinds}")

    pairs = []
    if kinds[0] == "L":  # label then count
        for i in range(0, len(stream), 2):
            pairs.append((stream[i][1], stream[i + 1][1]))
    else:  # count then label
        for i in range(0, len(stream), 2):
            pairs.append((stream[i + 1][1], stream[i][1]))
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
