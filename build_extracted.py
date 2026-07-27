"""
Deterministic driver: blocks.jsonl -> extracted.json (+ review.txt). No AI.

This is the promoted pipeline's core builder. It replaces the old per-message
LLM-labeling step (classified.jsonl): instead of the model tiling every message,
a deterministic resolver reads each raw message and produces the entries itself.

Per message, in order:
  1. Per-sender template (sender_templates): rigid-format senders are parsed
     positionally — the most robust path, so it wins outright.
  2. Otherwise the deterministic resolver (resolve.resolve_message):
     segment (numbers as anchors) -> classify each phrase against the exact
     dictionary (chant_mappings.json) then the compositional matcher
     (compose_rules.json) -> pair by the alternation rule -> header carry-forward.

An entry only reaches extracted.json when its message resolves unambiguously. A
message the resolver cannot pair (a dropped/new chant spelling leaves its number
dangling, a genuinely unattributable count, a template-shape mismatch) is written
to review.txt as a HARD item and its numbers NEVER enter the totals. That is the
LLM's remaining, narrow job: resolve each flagged item by extending
chant_mappings.json / compose_rules.json (its answer is cached, deterministic
forever after), then re-run until clean.

Output entries use the existing extractor format so reconcile.py / aggregate.py
consume them unchanged:
    {"date": "DD/MM/2026", "chant": "<canonical>", "count": <int>}

A message can also be excluded up front via known_duplicates.json: a durable,
content-addressed (sender, date, exact text) store of messages a human has
confirmed are a duplicate/incomplete resend already superseded by another
message in the same file (e.g. a sender resends the complete message moments
after an incomplete one). These are logged as [INFO], not [HARD] -- they do not
block the exit code, but stay in review.txt for audit. Only add an entry here
when a genuinely dangling count's numbers are ALSO fully covered by another,
clean message -- never to make an unresolved flag disappear.

Usage:
    python build_extracted.py <blocks.jsonl> <extracted.json> [review.txt]
Exit code: 1 if any HARD review item (needs human/LLM), else 0.
"""
import sys
import json

import duplicates         # confirmed duplicate/superseded sends (known_duplicates.json)
import reconcile           # reuse norm_date for DD/MM -> DD/MM/2026
import resolve            # the deterministic resolver core
import sender_templates   # per-sender positional templates (deterministic override)


def _load_blocks(blocks_path):
    with open(blocks_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build(blocks_path, extracted_out, review_out,
          known_duplicates_path=duplicates.DEFAULT_PATH):
    blocks = _load_blocks(blocks_path)
    decisions = resolve._load_decisions()
    known_duplicates = duplicates.load(known_duplicates_path, norm_date=reconcile.norm_date)
    entries, review = [], []

    for m in blocks:
        mid = m.get("id")
        sender = m.get("sender", "")
        date = reconcile.norm_date(m.get("envelope_date", ""))

        # 0. Confirmed duplicate/superseded send: excluded, logged, not a gate item.
        dup_reason = known_duplicates.get(duplicates.key(sender, date, m.get("text", "")))
        if dup_reason is not None:
            review.append(f"[INFO] known-duplicate: msg {mid}: excluded -- {dup_reason}")
            continue

        # 1. Deterministic per-sender template wins outright for rigid senders.
        if sender_templates.is_template_sender(sender):
            res = sender_templates.extract(sender, m.get("text", ""))
            if res is None:
                review.append(f"[HARD] template-shape: msg {mid}: sender {sender!r} "
                              "did not match its template shape -- needs human")
                continue
            for e in res:
                entries.append({"date": date, "chant": e["chant"], "count": e["count"]})
            continue

        # 2. Deterministic resolver for everyone else.
        es, flag, _dropped = resolve.resolve_message(m, decisions)
        if flag:
            review.append(f"[HARD] unresolved: msg {mid}: {flag}")
            continue
        entries.extend(es)

    with open(extracted_out, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    with open(review_out, "w", encoding="utf-8") as f:
        f.write("\n".join(review) + ("\n" if review else ""))

    hard_count = sum(1 for r in review if r.startswith("[HARD]"))
    return entries, review, hard_count


def main():
    try:  # review lines can quote Devanagari / Urdu labels
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) not in (3, 4):
        print("Usage: python build_extracted.py <blocks.jsonl> "
              "<extracted.json> [review.txt]")
        sys.exit(2)
    blocks, extracted = sys.argv[1], sys.argv[2]
    review_out = sys.argv[3] if len(sys.argv) > 3 else "review.txt"

    entries, review, hard = build(blocks, extracted, review_out)
    print(f"Wrote {len(entries)} entries to {extracted}")
    print(f"Review items: {len(review)} ({hard} hard) -> {review_out}")
    if hard:
        print("HARD review items present -- resolve them (extend chant_mappings.json "
              "/ compose_rules.json) and re-run before aggregating.")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
