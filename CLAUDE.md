# Darood Bank — Claude-driven extraction workflow

This project counts religious chants from WhatsApp chat exports. The old
`main.py` did this by calling the Gemini API twice per message (an "extractor"
model and a "verifier" model), which is slow and token-expensive.

**In this workflow, Claude Code IS the extractor+verifier agent.** The
deterministic work (splitting messages, summing counts, writing CSVs) stays in
Python and costs nothing. Only the extraction step uses the model — once, not
twice. Follow this procedure exactly; do not improvise the format.

## Pipeline

```
inputs/*.txt --> split_blocks.py --> blocks.jsonl --> [Claude extracts] --> extracted.json --> aggregate.py --> outputs/
```

## Procedure — when asked to "process <file>"

1. **Split (deterministic).**
   `python split_blocks.py inputs/<file>.txt blocks.jsonl`
   This yields one JSON message per line: `{id, envelope_date, sender, text}`.

2. **Extract (this is your job as the agent).**
   Read `blocks.jsonl`. For every message, output zero or more entries following
   the Extraction Rules below. Collect them into a single JSON array and write it
   to `extracted.json`. Each entry is:
   `{"date": "DD/MM/YYYY", "chant": "<canonical name>", "count": <integer>}`

3. **Reconcile (deterministic accuracy gate — always run before aggregating).**
   `python reconcile.py blocks.jsonl extracted.json`
   This checks that every number in the messages is accounted for and that every
   extracted count exists in the source (per date). If it prints anything other
   than `CLEAN`, investigate the flagged counts before continuing — a large/real
   number in the "MISSED" or "TYPO/EXTRA" list means the extraction is wrong.
   Small stray day/month fragments are expected noise. NOTE: this gate catches
   dropped/invented/misdated counts, but NOT chant *misidentification* (a real
   number paired with the wrong chant). For that, use a verifier subagent.

4. **Aggregate (deterministic).**
   `python aggregate.py extracted.json outputs/<timestamp>/<file>`
   Use a timestamp like `2026-07-08_14-30-00`. This writes `summary.txt`,
   `summary.csv`, and `daily_breakdown.csv`.

5. **Archive.** Move the processed input to `inputs/processed/`.

6. **Report.** Tell the user the output path and how many messages produced no
   entries (candidates for manual review).

## Processing many files (parallel)

For a batch, spawn **one subagent per file** (the Agent tool, `general-purpose`
type). Give each subagent this same CLAUDE.md procedure and one filename. Each
returns its own `extracted.json`; run `aggregate.py` per file. This keeps the
main context small and runs files concurrently. Do NOT read every file into the
main conversation at once.

## Extraction Rules (formerly the Gemini prompts)

You are extracting `chant` + `count` pairs from short, messy, multilingual
(Hindi/Urdu/English, often transliterated) messages.

- **Never sum counts yourself.** `100 darood ... 500 darood` = two separate
  entries. `aggregate.py` does all summing.
- **One entry per (person, chant, count).** A message listing several people each
  produces its own entries; the `count` is per person as written.
- **Date:** always use the message's **send date** = `envelope_date` from
  `blocks.jsonl`, normalized to `DD/MM/2026`. Ignore any date written inside the
  message body — a person may report yesterday's count, but it is recorded on the
  day they sent it. A single message may still list several people/counts; they
  all share that one send date.
- **Skip** greetings, blessings, and lines with no count (e.g. `assalam
  walekum`, `🙏`, `shukriya`). A message that yields nothing is fine — just emit
  no entries for it.
- **Normalize every chant to a canonical name** from the list below.

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
- If a chant clearly isn't in the list, keep its cleaned name as-is; it becomes
  an extra column. Prefer a canonical match when the meaning is unambiguous.

### Per-person typo corrections
These are corrections scoped to a single sender, NOT general rules. Only apply
them when the message is from that exact sender. If a different person makes the
same mistake, do not auto-correct — flag it and ask before generalizing.
- **Sender `+91 99264 85966`:** reads `50p` (or a bare `50` in the count slot) on
  their SURAH IKHLAS line as `500`. This sender posts an identical daily template
  (300 / 500 / 500 / 500), so a `50p` there is an unmistakable corrupted `500`.
  Applies to this sender only.

## Notes
- Keep `CHANT_ORDER` in `aggregate.py` identical to the canonical list above.
- `.env` / `GOOGLE_API_KEY` are no longer needed for this workflow.
