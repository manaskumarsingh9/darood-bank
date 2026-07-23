# Darood Bank — Claude-driven extraction workflow

This project counts religious chants from WhatsApp chat exports. The old
`main.py` did this by calling the Gemini API twice per message (an "extractor"
model and a "verifier" model), which is slow and token-expensive.

**In this workflow the model does ONE narrow job: labeling.** Everything numeric —
pairing chant to count, applying the date, summing, normalizing spelling, writing
CSVs — is deterministic Python and costs nothing. The model reads each message and
partitions it into labeled segments (what is a count? a chant name? a date? a
person's name? filler?). Deterministic code then verifies that labeling rebuilds
the original exactly, pairs labels to counts, and aggregates. This keeps the
fuzzy, error-prone judgment (multilingual spellings, names-vs-chants) with the
model and makes the arithmetic reproducible and auditable. Follow this procedure
exactly; do not improvise the format. See `CLASSIFICATION.md` for the schema.

## Pipeline

```
inputs/*.txt
  --> split_blocks.py     --> blocks.jsonl                    (deterministic)
  --> [Claude labels]     --> classified.jsonl                (the model's ONLY job)
  --> build_extracted.py  --> extracted.json + review.txt     (verify+pair+normalize; HARD gate)
  --> reconcile.py         (per-date number backstop; deterministic)
  --> aggregate.py        --> outputs/                        (deterministic)
```

## Procedure — when asked to "process <file>"

1. **Split (deterministic).**
   `python split_blocks.py inputs/<file>.txt blocks.jsonl`
   This yields one JSON message per line: `{id, envelope_date, sender, text}`.

2. **Label (this is your only job as the agent).**
   Read `blocks.jsonl`. For every message write one record to `classified.jsonl`
   that partitions the message `text` into ordered, single-role segments that
   **tile it exactly** (see `CLASSIFICATION.md` for the schema and roles). You do
   **not** pair, sum, date, or normalize — deterministic code does all of that.
   You only decide, per span, *what it is*: `chant-label`, `count`, `date`,
   `name`, `greeting`, `filler`, `list-marker`, `phone`, `other`, or `uncertain`.
   Follow the Labeling Rules below.

3. **Build (deterministic — HARD gate: verify + pair + normalize).**
   `python build_extracted.py classified.jsonl extracted.json review.txt`
   This reconstructs each message (nothing added/dropped), reconciles its numbers,
   pairs `chant-label`→`count` by the alternation rule, applies the **send date**
   automatically, and normalizes each label via `chant_mappings.json`. It writes
   only entries it can fully trust; everything else goes to `review.txt`. It
   **exits 1** on any HARD review item. Resolve each and re-run until clean:
   - `reconstruction` / `census` / `count-shape` → fix the segmentation.
   - `non-alternating` → fix the segmentation (a chant with no count, a count with
     no chant, or a mis-split run-on line).
   - `uncertain` → decide chant vs name (research if needed), update the record.
   - `unknown-label` → add the new spelling to `chant_mappings.json` under its
     canonical name, then re-run. *This is where your normalization judgment is
     captured — once, durably, instead of on every run.*
   SOFT items (a count that looks like a year/date) are worth a glance but do not
   block.

4. **Reconcile (deterministic per-date number backstop).**
   `python reconcile.py blocks.jsonl extracted.json`
   `build_extracted` already guarantees per-message number integrity; this is the
   same per-date multiset gate that `aggregate` reruns. It should be clean. It is
   still a **hard gate** (exits 1 on a significant discrepancy ≥ 100, override
   with env `RECONCILE_MIN`). Note this gate is blind to chant *misidentification*
   — but pairing now comes from deterministic segment structure, not a free-form
   guess, so that class is prevented upstream rather than caught here.

5. **Aggregate (deterministic — reruns the gate and archives the run).**
   `python aggregate.py extracted.json outputs/<timestamp>/<file> blocks.jsonl`
   Use a timestamp like `2026-07-08_14-30-00`. **Always pass `blocks.jsonl`.**
   Aggregate re-runs the reconcile gate first and, on a significant failure,
   writes **nothing**. On pass it writes `summary.txt`, `summary.csv`,
   `daily_breakdown.csv` and archives `blocks.jsonl`, `extracted.json`, and
   `reconcile_report.txt`. Then also copy `classified.jsonl` and `review.txt` into
   the same run directory so the labeling layer is part of the audit trail.

6. **Archive.** Move the processed input to `inputs/processed/`.

7. **Report.** Tell the user the output path, how many messages produced no
   entries, and any SOFT review items still worth a human glance.

## Processing many files (parallel)

For a batch, spawn **one subagent per file** (the Agent tool, `general-purpose`
type). Give each subagent this same CLAUDE.md procedure and one filename. Each
returns its own `classified.jsonl`; run `build_extracted.py` then `aggregate.py`
per file. This keeps the main context small and runs files concurrently. Do NOT
read every file into the main conversation at once.

## Labeling Rules (what the model does)

You are **labeling, not extracting**. For each message you partition its `text`
into ordered, single-role segments that tile it exactly, so deterministic code can
pair, date, sum, and normalize. Your judgment is needed only to decide what each
span *is*.

- **`count`** — a chant count number; **exactly one number per count segment**.
  Never combine or sum: `100 darood ... 500 darood` = two separate `count`
  segments. `aggregate.py` does all summing.
- **`chant-label`** — a chant name, copied **verbatim** in whatever script/spelling
  it appears. Do **not** normalize here; the dictionary does that. A never-seen
  spelling surfaces as an `unknown-label` review item you then add to
  `chant_mappings.json`.
- **`date`** — a date written in the body. You do **not** date the entries; the
  **send date** (`envelope_date`) is applied automatically, and body dates never
  become the record date. If a number could be a date or a count, check the send
  date/time: a number close to the send date is almost always a date — label it
  `date`, not `count`.
- **`name` / `greeting` / `filler` / `list-marker` / `phone` / `other`** — every
  non-count, non-chant span. Units and connectives (`martba`, `बार`, `times`),
  separators, and blessings (`assalam walekum`, `🙏`, `shukriya`) are `filler` or
  `greeting`. A message listing several people: label each person's name `name`
  and each of their chant-labels/counts in order — pairing then yields one entry
  per (chant, count) automatically.
- **`uncertain`** — when you genuinely cannot tell whether a span is a chant name
  or a person's name, label it `uncertain`. **Never guess**; it routes to a human.
- A message with no counts is fine — label its spans as noise roles; it yields no
  entries.

The canonical names and known mappings below **seed `chant_mappings.json`**, which
is the durable normalization store. When you resolve an `unknown-label`, add the
variant *there*, not here.

### Canonical chant names
DAROOD, Gayatri Mantra, KALMA SHARIF, SURAH IKHLAS, SURAH FATIHA, DAROOD TAJ,
SIJRA SHARIF, AAYTUL KURSI, PARA, QURAN, AAYTE KARIMA, DAROOD IBRAHIM,
SURAH KAUSER, BISMILLAH SHARIF, surah mujammil, surah takasur, surah kaaffiroon,
surah juma, surah falak, surah naas, YASEEN SHARIF, Astagfar, Naad-e-Ali,
Ehednama, Surah Mulk, Duwaye Kunut, Maja Mrityunjay mantra, Surah Rahman,
Surah Fajr, Dua e Noor, Darood Mahi, Surah Yaseen, Sur e Kahf, Surah Bakr,
Alhamdu Shareef, Raksha Strota, Aman Rasul, Kulho wallah Sharif, Sure Juma,
kul sharif, Surah atah takasur, Surah Alif Laam

### Known mappings (extend as you see new variants)
- `Darud, Durood, Duood, दरूद, दरुद, दरूद शरीफ, durood/darud/daroid sharif` → **DAROOD**
- `duoord taj, दरूद ताज` → **DAROOD TAJ**
- `श्री गायत्री मंत्र, गायत्री मंत्र, Shri Gaytri Mantra` → **Gayatri Mantra**
- `गुरु मंत्र, guru mantra, श्री गुरु मंत्र` → **Guru Mantra** (extra; keep as-is)
- `kalma sharif, कलमा शरीफ` → **KALMA SHARIF**
- `sura iklas, surah ekhlas, सूरह इखलास` → **SURAH IKHLAS**
- `surh fateha, सुरह फातिया, सूरह फातिहा` → **SURAH FATIHA**
- `Aaytalkurshi, आयतुल कुर्सी` → **AAYTUL KURSI**
- `sijra/shijra/शीजरा/शिज़रा sharif` → **SIJRA SHARIF**
- `astagfirullah, toba astagfirullah, अस्तगफार` → **Astagfar**
- `surh koshar, sure koshar` → **SURAH KAUSER**
- `surh nash` → **surah naas**
- `Kulu/Kulho/kulhu Allah sharif, kullu allah` → **Kulho wallah Sharif**
- `surh Bakr` → **Surah Bakr**
- If a chant clearly isn't in the list, add it to `chant_mappings.json` as its
  own new canonical name (it becomes an extra column). Prefer an existing canonical
  match when the meaning is unambiguous.

### Per-person typo corrections
These are corrections scoped to a single sender, NOT general rules. Only apply
them when the message is from that exact sender. If a different person makes the
same mistake, do not auto-correct — flag it and ask before generalizing.
- **Sender `+91 99264 85966`:** reads `50p` (or a bare `50` in the count slot) on
  their SURAH IKHLAS line as `500`. This sender posts an identical daily template
  (300 / 500 / 500 / 500), so a `50p` there is an unmistakable corrupted `500`.
  Applies to this sender only.
  - *How to apply under labeling:* the tiling rule forbids editing the source
    text, so you cannot relabel `50` as `500`. Instead label that span `uncertain`
    so build_extracted routes it to `review.txt`, and apply the `→500` correction
    to `extracted.json` before reconciling. (A future per-sender override could do
    this deterministically; for now it is a scoped manual fix.)

## Notes
- `chant_mappings.json` is the durable normalization store, seeded from the
  canonical list + known mappings above. Extend *it* when you learn a new variant;
  never hand-edit outputs to fix a spelling.
- Keep `CHANT_ORDER` in `aggregate.py` identical to the canonical list above.
- The deterministic backbone (`classify_verify.py`, `pair.py`, `normalize.py`,
  `build_extracted.py`) is covered by `test_pipeline.py` — run `python
  test_pipeline.py` after changing any of them.
- `.env` / `GOOGLE_API_KEY` are no longer needed for this workflow.
