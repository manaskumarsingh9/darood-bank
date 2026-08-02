# Darood Bank App

A Python application for extracting and counting religious chants from
WhatsApp chat exports.

The pipeline is **fully deterministic** — no AI calls, no randomness. Given
the same input file, it produces byte-identical output every time. The full
procedure (and the reasoning behind it) is documented in
[`CLAUDE.md`](CLAUDE.md); this file is just a quick-start.

> Legacy note: `main.py` and `classify_verify.py` are an earlier design that
> called the Gemini API per message. They're kept for reference and are
> still covered by the test suite, but are **not** part of the current
> workflow and need no API key.

## Prerequisites

- **Python 3.9+**
- **`pandas`** — the only third-party dependency the current pipeline needs
  (used by `aggregate.py` to write CSVs). Everything else is standard
  library. (`requirements.txt` also lists `google-adk`, `google-genai`, and
  `python-dotenv` for the legacy scripts above — skip those unless you're
  touching `main.py`.)

## Installation

```bash
git clone <repo-url>
cd darood-bank
pip install pandas
```

No `.env` or API key is needed.

## Usage

### Step 0 (optional) — building input files from WhatsApp

If you already have files in `inputs/`, skip this. Otherwise: the group has
"Export chat" disabled, so messages are copied out by hand — select
messages → Copy → paste into a new `.txt` under `raw/inbox/`. Then:

```bash
python ingest.py       # merge every raw/inbox/*.txt into raw/chatlog.txt (dedupes, handles out-of-order pastes)
python split_weeks.py  # slice raw/chatlog.txt into inputs/<week>.txt (Mon-Sun)
```

### Processing a file

```bash
python split_blocks.py inputs/<file>.txt blocks.jsonl
python build_extracted.py blocks.jsonl extracted.json review.txt
python reconcile.py blocks.jsonl extracted.json
python aggregate.py extracted.json outputs/<timestamp>/<file> blocks.jsonl
```

`build_extracted.py` exits non-zero if it hits a chant spelling or count it
can't resolve confidently — see `CLAUDE.md` for how to resolve those flags
(they get fixed once, in a durable lookup file, and are then handled
automatically forever after).

## Outputs

Results are saved in `outputs/<timestamp>/`:
- **`summary.txt`** — a simple list of totals (e.g., `DAROOD = 500+100`)
- **`summary.csv`** — one row of total counts per chant
- **`daily_breakdown.csv`** — a day-by-day table of counts
- plus an archived copy of `blocks.jsonl`, `extracted.json`,
  `reconcile_report.txt`, and `review.txt` for that run

## Troubleshooting

- **Garbled Hindi/Urdu text on Windows**: set `PYTHONIOENCODING=utf-8`
  before running (the checked-in `.claude/settings.json` sets this
  automatically if you're using Claude Code).
- **`build_extracted.py` exits 1**: expected — it means there are HARD flags
  in `review.txt` to resolve. See the "Resolve the HARD flags" section of
  `CLAUDE.md`.
- **`reconcile.py` exits 1**: a per-date count mismatch ≥ 100; see
  `reconcile_report.txt`.
