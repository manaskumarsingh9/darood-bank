"""
Step 3 of the token-free pipeline: turn the extracted entries into the same
reports the old app produced. Fully deterministic (no AI, no API calls).

Input: a JSON file that is a list of entries, each:
    {"date": "24/02/2026", "chant": "DAROOD", "count": 100}

Usage:
    python aggregate.py extracted.json outputs/<run>/<file-name>

Writes summary.txt, summary.csv and (when more than one date is present)
daily_breakdown.csv into the output directory.
"""
import os
import sys
import json
import pandas as pd

# Canonical order for reports. Keep this in sync with the list in CLAUDE.md.
CHANT_ORDER = [
    "DAROOD", "Gayatri Mantra", "KALMA SHARIF", "SURAH IKHLAS", "SURAH FATIHA",
    "DAROOD TAJ", "SIJRA SHARIF", "AAYTUL KURSI", "PARA", "QURAN",
    "AAYTE KARIMA", "DAROOD IBRAHIM", "SURAH KAUSER", "BISMILLAH SHARIF",
    "surah mujammil", "surah takasur", "surah kaaffiroon", "surah juma",
    "surah falak", "surah naas", "YASEEN SHARIF", "Astagfar", "Naad-e-Ali",
    "Ehednama", "Surah Mulk", "Duwaye Kunut", "Maja Mrityunjay mantra",
    "Surah Rahman", "Surah Fajr", "Dua e Noor", "Darood Mahi", "Surah Yaseen",
    "Sur e Kahf", "Surah Bakr", "Alhamdu Shareef", "Raksha Strota", "Aman Rasul",
    "Kulho wallah Sharif", "Sure Juma", "kul sharif", "Surah atah takasur",
    "Surah Alif Laam",
]


def load_entries(path):
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    results_by_date = {}
    for e in entries:
        date = str(e.get("date", "Unknown")).strip() or "Unknown"
        chant = e.get("chant")
        count = e.get("count")
        if not chant or count is None:
            continue
        try:
            count = int(count)
        except (TypeError, ValueError):
            print(f"Skipping non-integer count {count!r} for {chant!r}")
            continue
        results_by_date.setdefault(date, {}).setdefault(chant, []).append(count)
    return results_by_date


def ordered_chants(present):
    """Canonical chants first (in order), then any extras in first-seen order."""
    extras = [c for c in present if c not in CHANT_ORDER]
    return [c for c in CHANT_ORDER if c in present] + extras


def generate_outputs(results_by_date, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    all_counts = {}
    for chants in results_by_date.values():
        for chant, counts in chants.items():
            all_counts.setdefault(chant, []).extend(counts)

    if not all_counts:
        print("No data extracted.")
        return

    columns = ordered_chants(all_counts)

    # summary.txt  ->  DAROOD =100+500+...
    with open(os.path.join(output_dir, "summary.txt"), "w", encoding="utf-8") as f:
        for chant in columns:
            f.write(f"{chant} ={'+'.join(map(str, all_counts[chant]))}\n")

    # summary.csv  ->  one row of totals
    summary_row = {c: sum(all_counts[c]) for c in columns}
    pd.DataFrame([summary_row]).to_csv(
        os.path.join(output_dir, "summary.csv"), index=False
    )

    # daily_breakdown.csv  ->  one row per date
    real_dates = [d for d in results_by_date if d != "Unknown"]
    if real_dates:
        rows = []
        for date in sorted(real_dates):
            chants = results_by_date[date]
            row = {"Date": date}
            row.update({c: sum(chants.get(c, [])) for c in columns})
            rows.append(row)
        pd.DataFrame(rows).to_csv(
            os.path.join(output_dir, "daily_breakdown.csv"), index=False
        )

    print(f"Results saved to {output_dir}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python aggregate.py <extracted.json> <output_dir>")
        sys.exit(1)
    generate_outputs(load_entries(sys.argv[1]), sys.argv[2])


if __name__ == "__main__":
    main()
