"""
Deterministic driver: classified.jsonl -> extracted.json (+ review.txt). No AI.

Ties the deterministic backbone together:
    verify (HARD gate) -> pair (alternation) -> normalize (dictionary lookup)

An entry only reaches extracted.json when its message reconstructs cleanly, its
numbers reconcile, its label/count stream pairs unambiguously, and its label is
in the mapping table. Anything else is written to review.txt for a human/LLM --
never guessed into the totals.

Output entries use the existing extractor format so reconcile.py / aggregate.py
consume them unchanged:
    {"date": "DD/MM/2026", "chant": "<canonical>", "count": <int>}

Usage:
    python build_extracted.py <classified.jsonl> <extracted.json> [review.txt]
Exit code: 1 if any HARD review item (needs human), else 0.
"""
import sys
import json

import classify_verify
import pair
import normalize
import reconcile  # reuse norm_date for DD/MM -> DD/MM/2026


def build(classified_path, extracted_out, review_out, blocks_path=None):
    recs = classify_verify.load(classified_path)
    review = []
    entries = []

    # Deterministic per-sender templates (require blocks.jsonl for sender+text).
    # These override any LLM labeling for those messages — the fixed-shape senders
    # are parsed positionally, not guessed.
    template_ids = set()
    if blocks_path:
        import sender_templates
        with open(blocks_path, encoding="utf-8") as f:
            blocks = {json.loads(l)["id"]: json.loads(l) for l in f if l.strip()}
        for mid, m in blocks.items():
            if not sender_templates.is_template_sender(m.get("sender", "")):
                continue
            template_ids.add(mid)  # handled deterministically; ignore its LLM record
            res = sender_templates.extract(m["sender"], m["text"])
            if res is None:
                review.append(f"[HARD] template-shape: msg {mid}: sender "
                              f"{m.get('sender')!r} did not match its template "
                              "shape -- needs human")
                continue
            date = reconcile.norm_date(m.get("envelope_date", ""))
            for e in res:
                entries.append({"date": date, "chant": e["chant"], "count": e["count"]})

    for rec in recs:
        mid = rec.get("id")
        if mid in template_ids:
            continue  # deterministic template wins over the LLM labeling
        flags = classify_verify.verify_record(rec)
        review.extend(str(f) for f in flags)
        if any(f.level == "hard" for f in flags):
            continue  # untrustworthy message; already flagged above

        stream = pair.stream_from_segments(rec.get("segments", []))
        pairs, err = pair.pair_stream(stream)
        if err:
            review.append(f"[HARD] non-alternating: msg {mid}: {err}")
            continue

        date = reconcile.norm_date(rec.get("envelope_date", ""))
        for label, count in pairs:
            canonical = normalize.normalize(label)
            if canonical is None:
                review.append(f"[HARD] unknown-label: msg {mid}: {label!r} "
                              f"(count {count}) not in mapping table -- needs LLM/human")
                continue
            entries.append({"date": date, "chant": canonical, "count": count})

    with open(extracted_out, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    with open(review_out, "w", encoding="utf-8") as f:
        f.write("\n".join(review) + ("\n" if review else ""))

    hard_count = sum(1 for r in review if r.startswith("[HARD]"))
    return entries, review, hard_count


def main():
    try:  # review lines can quote Devanagari labels
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) not in (3, 4, 5):
        print("Usage: python build_extracted.py <classified.jsonl> "
              "<extracted.json> [review.txt] [blocks.jsonl]")
        sys.exit(2)
    classified, extracted = sys.argv[1], sys.argv[2]
    review_out = sys.argv[3] if len(sys.argv) > 3 else "review.txt"
    blocks = sys.argv[4] if len(sys.argv) > 4 else None

    entries, review, hard = build(classified, extracted, review_out, blocks)
    print(f"Wrote {len(entries)} entries to {extracted}")
    print(f"Review items: {len(review)} ({hard} hard) -> {review_out}")
    if hard:
        print("HARD review items present -- resolve them before aggregating.")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
