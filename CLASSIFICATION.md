# The `classified.jsonl` labeling layer

This is the sidecar the LLM produces instead of a "cleaned" text file. It **labels
the original without altering it**, so deterministic code can prove nothing was
added, dropped, or mispaired. The original input and `blocks.jsonl` remain the
source of truth.

## Pipeline position

```
inputs/*.txt --> split_blocks.py --> blocks.jsonl
    --> [LLM labels each message] --> classified.jsonl
    --> classify_verify.py   (reconstruction + census + date checks; HARD gate)
    --> pair.py              (alternation/elimination pairing; deterministic)
    --> normalize.py         (dictionary lookup; miss => LLM for that label only)
    --> build_extracted.py   (driver: ties the above into extracted.json + review.txt)
    --> reconcile.py / aggregate.py  (existing number gate + reports)
```

## Record shape

One JSON object per line, one per message. The message text is a **partition of
the original into ordered, single-role segments that tile it exactly**:

```json
{
  "id": 82,
  "envelope_date": "13/03/2026",
  "text": "गोरखपुर से 13-03-2026\nवशीउल्लाह\nदरूद शरीफ- 2000 बार",
  "segments": [
    {"t": "गोरखपुर से ",   "role": "filler"},
    {"t": "13-03-2026",    "role": "date"},
    {"t": "\nवशीउल्लाह\n",  "role": "name"},
    {"t": "दरूद शरीफ- ",    "role": "chant-label"},
    {"t": "2000",          "role": "count"},
    {"t": " बार",           "role": "filler"}
  ]
}
```

## Roles

| Role | Meaning | Consumed by |
|---|---|---|
| `chant-label` | the name of a chant, as written (any script/spelling) | pairing + normalization |
| `count` | a number that is a chant count; **exactly one digit-run** | pairing |
| `date` | a date the person typed (send date is authoritative, not this) | dropped |
| `name` | a person's / place's name | dropped |
| `filler` | connective words, units (`martba`, `बार`, `times`), separators | dropped |
| `greeting` | salaam / pranam / shukriya etc. | dropped |
| `phone` | a phone number | dropped |
| `list-marker` | enumeration like `1.` `03.` `4)` | dropped |
| `other` | any other non-count text | dropped |
| `uncertain` | the model is not sure (e.g. chant-name vs person-name) | **routed to human** |

**Rules the LLM must obey**
1. Segments concatenated in order reproduce the original text (whitespace aside).
2. Every number in the message lands inside exactly one segment.
3. A `count` segment holds exactly one number and nothing else numeric.
4. Split filler from labels — one role per segment.
5. When genuinely unsure whether something is a chant or a name, use `uncertain`;
   never guess.

## Deterministic guarantees checked afterwards

- **Reconstruction** (`classify_verify.py`): tiling must rebuild the original —
  catches invented/dropped/altered text. HARD.
- **Number census**: multiset of numbers in the segments must equal the multiset
  in the original — no count can silently vanish. HARD.
- **`count` shape**: one number per count segment. HARD.
- **`uncertain` present**: routed to human. HARD.
- **Date plausibility** (soft): a `count` that looks like a year, or a `date`
  segment holding a non-date-looking number, is flagged for review. SOFT.
- **Pairing** (`pair.py`): the `chant-label`/`count` subsequence must be strictly
  alternating and balanced; then it pairs deterministically (direction from the
  first element — the elimination rule). Otherwise the message is routed to human.
- **Normalization** (`normalize.py`): dictionary lookup only. A miss is the
  signal to ask the LLM for that one label, then add it to the table.

Nothing wrong reaches the total without being flagged first.
