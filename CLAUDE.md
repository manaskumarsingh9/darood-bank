# Darood Bank — deterministic extraction workflow

This project counts religious chants from WhatsApp chat exports. The old
`main.py` did this by calling the Gemini API twice per message (an "extractor"
model and a "verifier" model), which is slow and token-expensive.

**The pipeline is now fully deterministic, and the model does ONE narrow job:
resolve what the deterministic code cannot.** A deterministic resolver reads each
raw message, segments it using numbers as anchors, identifies chants by exact
dictionary lookup (`chant_mappings.json`) plus a compositional token-class matcher
(`compose_rules.json`), pairs chant→count by the alternation rule, applies the
send date, and sums — all with no AI and no randomness, so re-running the same
input gives byte-identical results. When the resolver hits something it cannot
resolve (a chant spelling it has never seen, a genuinely dangling count), it does
**not** guess: it emits a HARD review flag and the message's numbers never enter
the totals. Resolving those flags — deciding what a new spelling means and caching
it, or judging a genuinely ambiguous count — is the model's only remaining job.
Every such decision is written back into the durable stores so it is deterministic
forever after.

This is the design goal: *no matter how many times a fresh instance runs the same
file, it produces the same result.* Follow this procedure exactly.

## Pipeline

```
inputs/*.txt
  --> split_blocks.py     --> blocks.jsonl                 (deterministic)
  --> build_extracted.py  --> extracted.json + review.txt  (deterministic resolver; HARD gate)
        resolver = sender_templates -> segment -> (chant_mappings.json, compose_rules.json) -> pair
        (HARD flags -> the model extends chant_mappings.json / compose_rules.json, re-run)
  --> reconcile.py         (per-date number backstop; deterministic)
  --> aggregate.py        --> outputs/                     (deterministic)
```

## Procedure — when asked to "process <file>"

1. **Split (deterministic).**
   `python split_blocks.py inputs/<file>.txt blocks.jsonl`
   This yields one JSON message per line: `{id, envelope_date, sender, text}`.

2. **Build (deterministic — HARD gate).**
   `python build_extracted.py blocks.jsonl extracted.json review.txt`
   For each message this runs, in order: (a) a per-sender positional template
   (`sender_templates.py`) for rigid-format senders; otherwise (b) the resolver —
   `segment.py` splits on numbers, each phrase is matched against
   `chant_mappings.json` (exact) then `compose.py`/`compose_rules.json`
   (compositional), and `pair.py` pairs by alternation. The **send date**
   (`envelope_date`) is always applied; body dates never become the record date.
   It writes only entries it fully trusts; everything else goes to `review.txt`,
   and it **exits 1** on any HARD item. There is no message this step guesses.

3. **Resolve the HARD flags (this is your only job as the agent).**
   Each HARD flag is one of:
   - **`unresolved` (non-pairable stream)** — usually a chant spelling the resolver
     doesn't know: the unknown phrase was dropped, leaving its number dangling, so
     alternation failed. Look at the message (`blocks.jsonl`), identify the chant,
     and **add the new spelling** to `chant_mappings.json` under its canonical name
     — or, if it's just a new spelling of a recurring *word* (a `surah`/`darood`
     variant), add that word to the right token class in `compose_rules.json`.
     Re-run step 2. *This is where your normalization judgment is captured — once,
     durably, instead of on every run.* If instead the flag is a **genuinely
     dangling count** (a number with no chant anywhere in the message, e.g. a
     trailing `121 martba` with no name), that is a real data ambiguity — do
     **not** invent a chant; leave it for a human decision.
   - **`template-shape`** — a template sender's message didn't match its fixed
     shape. Inspect and either fix `sender_templates.py` or handle as above.
   Never guess a chant identity. If you cannot tell what a phrase is, it stays
   flagged. Re-run step 2 until it exits 0.

4. **Reconcile (deterministic per-date number backstop).**
   `python reconcile.py blocks.jsonl extracted.json`
   `build_extracted` already guarantees per-message number integrity; this is the
   same per-date multiset gate that `aggregate` reruns. It should be clean once all
   HARD flags are resolved. It is a **hard gate** (exits 1 on a significant
   discrepancy ≥ 100, override with env `RECONCILE_MIN`). Note this gate is blind
   to chant *misidentification* — but pairing comes from deterministic segment
   structure, not a free-form guess, so that class is prevented upstream.

5. **Aggregate (deterministic — reruns the gate and archives the run).**
   `python aggregate.py extracted.json outputs/<timestamp>/<file> blocks.jsonl`
   Use a timestamp like `2026-07-08_14-30-00`. **Always pass `blocks.jsonl`.**
   Aggregate re-runs the reconcile gate first and, on a significant failure,
   writes **nothing**. On pass it writes `summary.txt`, `summary.csv`,
   `daily_breakdown.csv` and archives `blocks.jsonl`, `extracted.json`, and
   `reconcile_report.txt`. Then also copy `review.txt` into the same run directory
   so the flag-resolution layer is part of the audit trail.

6. **Archive.** Move the processed input to `inputs/processed/`.

7. **Report.** Tell the user the output path, how many messages produced no
   entries, and any flags you resolved (and how) or left for a human.

## Processing many files (parallel)

For a batch, spawn **one subagent per file** (the Agent tool, `general-purpose`
type). Give each subagent this same CLAUDE.md procedure and one filename. Each
runs `split_blocks.py` → `build_extracted.py`, resolves its own flags, then
`reconcile.py` → `aggregate.py`. This keeps the main context small and runs files
concurrently. Do NOT read every file into the main conversation at once.

## How chant identification works (so you can extend it)

The resolver identifies a phrase as a chant in two layers, both deterministic and
both keyed by the shared script-aware `normalize.simplify()` (NFC, drop combining
marks — Devanagari matras + Arabic harakat — unify Arabic/Urdu letter forms):

- **Exact dictionary** — `chant_mappings.json` maps each canonical chant to its
  full-phrase spelling variants (Latin, Devanagari, Urdu). Add a full spelling here
  when it's specific to one chant.
- **Compositional matcher** — `compose_rules.json` lists the spelling variants of
  each recurring *word* once (`SURAH`, `DAROOD`, honorific `SHARIF`, connective
  `e`) under `tokens`, and describes each chant as an ordered pattern of those
  classes under `chants` (`?` = optional slot). So `sure kauser`, `surh kausar`,
  `surah koshar` all resolve from `SURAH`×`KAUSER` without ever being enumerated —
  store additively, cover multiplicatively. It runs only after the exact
  dictionary misses, matches only when *every* word is a known token and *exactly
  one* chant pattern matches (else it defers), so it never invents a match.

Both are the durable stores. A brand-new spelling of a *distinctive* word still
misses (both layers) and surfaces as a HARD flag for you to resolve once; your fix
lands in one of these files and is deterministic thereafter.

The canonical names and known mappings below **seed** those stores. When you
resolve a flag, add the variant to `chant_mappings.json` / `compose_rules.json`,
not here.

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

### Per-sender templates and corrections (now deterministic)
Some senders post an identical, rigidly-structured daily message. These are
handled by `sender_templates.py`, which parses the counts **by position** (more
robust than parsing corrupted chant text) and applies scoped corrections — no
model judgment involved. Corrections are scoped to one sender, NOT general rules.
- **Sender `+91 99264 85966`:** posts a fixed template
  (`DAROOD / DAROOD / SURAH IKHLAS / KALMA SHARIF`) and sometimes writes the
  SURAH IKHLAS `500` as `50p`/`50`. `SENDER_COUNT_FIX` in `sender_templates.py`
  maps `("SURAH IKHLAS", 50) → 500` for this sender only. This is now applied
  deterministically during build — it is no longer a manual post-edit.
- To add a new template sender, add its ordered chant list to `SENDER_TEMPLATES`
  (and any scoped fix to `SENDER_COUNT_FIX`). If a message doesn't match the
  template shape, the sender falls through to the normal resolver / review.

## Notes
- `chant_mappings.json` (full-phrase variants) and `compose_rules.json` (word-class
  tokens + chant grammar) are the durable normalization stores, seeded from the
  canonical list + known mappings above. Extend *them* when you learn a new
  variant; never hand-edit outputs to fix a spelling.
- Keep `CHANT_ORDER` in `aggregate.py` identical to the canonical list above.
- The deterministic backbone (`segment.py`, `compose.py`, `resolve.py`, `pair.py`,
  `normalize.py`, `sender_templates.py`, `build_extracted.py`) is covered by
  `test_pipeline.py` — run `python test_pipeline.py` after changing any of them.
- `classify_verify.py` and `CLASSIFICATION.md` are **legacy** from the earlier
  LLM-labeling design (the model tiled each message into `classified.jsonl`). They
  are kept for reference and still tested, but are no longer part of the workflow
  above. `main.py` is the original Gemini script, also legacy.
- `.env` / `GOOGLE_API_KEY` are no longer needed for this workflow.
