import os
import sys
import pandas as pd
import json
import re
import asyncio
import datetime
import shutil

import google.genai as genai
from google.genai import types as genai_types

from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---

# This list defines the specific order and names of the chants we care about.
# If a chant is found, it will be mapped to one of these names.
# This ensures consistency in our final spreadsheet (CSV).
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
    "Surah Alif Laam"
]

# --- AGENT INSTRUCTIONS ---
# These are the "prompts" or instructions we give to the AI models.

# Instruction for the first AI (The Extractor):
# Its job is to read a message and pick out the chant names and their numbers.
EXTRACTOR_INSTRUCTION = f"""
You are a religious chant data extractor. Extract 'Chant Name' and 'Count' from WhatsApp messages.
1. Normalization: ALWAYS map variants to these canonical names: {", ".join(CHANT_ORDER)}.
   - Specifically, map 'Darud', 'Durood', 'दरूद', 'दरूद शरीफ', 'darood sharif', 'durood shareef' to 'DAROOD'.
   - Map 'Gayatri Mantra', 'श्री गायत्री मंत्र', 'गायत्री मंत्र' to 'Gayatri Mantra'.
2. CRITICAL: NEVER sum the counts yourself. If a message mentions '100 darood' and '500 darood', return TWO separate objects.
   - Example: [{{"chant": "DAROOD", "count": 100}}, {{"chant": "DAROOD", "count": 500}}]
3. Output: Return ONLY a JSON list of objects.
"""

# Instruction for the second AI (The Verifier):
# Its job is to double-check the first AI's work to make sure nothing was missed or misidentified.
VERIFIER_INSTRUCTION = f"""
You are a data auditor. You will be given a RAW message and a JSON list of extracted data.
Your job is to RECONCILE them.
1. Check for missed counts: Did the extractor miss any numbers mentioned?
2. Check for misidentification: Did it name a chant incorrectly?
3. Ensure the names match the canonical list: {", ".join(CHANT_ORDER)}.
4. CRITICAL: If multiple counts are mentioned for the same chant, ensure they are kept separate in the JSON list.
If everything is 100% correct, return {{"status": "MATCH", "corrected_data": null}}.
If there are discrepancies, return {{"status": "FIX", "corrected_data": [{{"chant": "Correct Name", "count": 123}}, ...], "reason": "Explanation"}}.
Return ONLY JSON.
"""

# --- REGEX (Text Pattern Finders) ---
# These are like search patterns to find dates and times in the messy WhatsApp text.

# DATE_PATTERN: Matches formats like 16.02.26, 16/02/2026, or 16-02-2026
DATE_PATTERN = r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'

# TIMESTAMP_REGEX: Matches the full WhatsApp timestamp like [12:00 pm, 16/02/26] Sender Name: Message
TIMESTAMP_REGEX = r'^\[?(\d{1,2}:\d{2}\s*[ap]m),\s+' + DATE_PATTERN + r'\]?\s+(?:-\s+)?([^:]+):\s+(.*)'

# SIMPLE_DATE_REGEX: A backup for lines that just start with a date
SIMPLE_DATE_REGEX = r'^' + DATE_PATTERN

# --- GEMINI CLIENT ---

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# --- THE CHANT SYSTEM ---
# This class acts as the "brain" of our application, managing the AI calls.
class ChantSystem:
    def __init__(self):
        # Cap concurrent API calls to avoid hitting Gemini rate limits
        self._semaphore = asyncio.Semaphore(5)

    async def get_agent_response(self, system_prompt, user_content, model):
        async with self._semaphore:
            response = await client.aio.models.generate_content(
                model=model,
                config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
                contents=user_content
            )
        return response.text

    # This function takes the AI's text response and tries to turn it into a Python list/dictionary
    def parse_json(self, text):
        cleaned = text.strip()
        # Clean up Markdown formatting like ```json ... ``` that AI often adds
        if "```json" in cleaned: cleaned = cleaned.split("```json")[-1].split("```")[0].strip()
        elif "```" in cleaned: cleaned = cleaned.split("```")[-1].split("```")[0].strip()

        # Search for [ before { — the extractor always returns an array
        start, end = cleaned.find('['), cleaned.rfind(']')
        if start == -1: start, end = cleaned.find('{'), cleaned.rfind('}')

        # Wrap a bare sequence of objects into a valid JSON array
        if cleaned.startswith('{') and '},{' in cleaned and not cleaned.endswith(']'):
            cleaned = f"[{cleaned}]"

        start, end = cleaned.find('['), cleaned.rfind(']')
        if start == -1: start, end = cleaned.find('{'), cleaned.rfind('}')

        if start != -1 and end != -1:
            json_candidate = cleaned[start:end+1]
            try:
                parsed_object = json.loads(json_candidate)
                if isinstance(parsed_object, list):
                    if all(isinstance(item, dict) for item in parsed_object):
                        return parsed_object
                    else:
                        print(f"Warning: Parsed JSON is a list but contains non-dictionary items. Full content: {cleaned}")
                        return None
                elif isinstance(parsed_object, dict):
                    return parsed_object
                else:
                    print(f"Warning: Parsed JSON is neither a list nor a dictionary. Full content: {cleaned}")
                    return None
            except json.JSONDecodeError:
                print(f"Warning: Failed to decode JSON from content: {cleaned[start:end+1]}")
                return None
        return None

    # This function processes a single "block" of message text for a specific date.
    # Returns (date, result_dict, original_block_if_nothing_was_extracted_else_None)
    async def process_block(self, date, block):
        if not block or not block.strip():
            return (date, {}, None)

        print(f"  Processing data for {date}...")

        # Step 1: Ask the Extractor to pull out the data (cheaper model for mechanical work)
        raw_extraction = await self.get_agent_response(EXTRACTOR_INSTRUCTION, block, model="gemini-2.0-flash")
        current_data = self.parse_json(raw_extraction) or []

        # Step 2: If nothing was extracted, skip verification and flag for manual review
        if not current_data:
            return (date, {}, block)

        # Step 3: Double-check with the Verifier (stronger model for reasoning)
        verify_prompt = f"RAW MESSAGE:\n{block}\n\nCURRENT EXTRACTION:\n{json.dumps(current_data)}"
        verification_raw = await self.get_agent_response(VERIFIER_INSTRUCTION, verify_prompt, model="gemini-2.5-flash")
        verification_result = self.parse_json(verification_raw)

        if verification_result and verification_result.get("status") == "FIX":
            current_data = verification_result.get("corrected_data") or current_data

        # Step 4: Build and return the result dictionary
        result = {}
        for item in current_data:
            if isinstance(item, dict):
                chant, count = item.get('chant'), item.get('count')
                if chant and count is not None:
                    try:
                        val = int(count)
                    except ValueError:
                        print(f"Warning: Could not convert count '{count}' to integer for chant '{chant}' in date '{date}'. Skipping item.")
                        continue
                    if chant not in result:
                        result[chant] = []
                    result[chant].append(val)
            else:
                print(f"Warning: Expected dictionary item but received {type(item)}: '{item}' for date '{date}'. Skipping item.")

        return (date, result, None)

    # This is the main function of the ChantSystem class
    async def run(self, raw_lines):
        current_date, current_block = "Unknown", ""
        blocks = []  # Collect all (date, block) pairs before processing

        # Go through the message file line by line to build blocks
        for line in raw_lines:
            line = line.strip()
            if not line: continue

            ts_match = re.match(TIMESTAMP_REGEX, line, re.IGNORECASE)
            simple_match = re.match(SIMPLE_DATE_REGEX, line)

            if ts_match or simple_match:
                # If we were already building a message block, save it before moving on
                if current_block.strip():
                    blocks.append((current_date, current_block))

                if ts_match:
                    _, date, _, text = ts_match.groups()
                    current_date, current_block = date, text
                else:
                    date = simple_match.group(1)
                    current_date, current_block = date, line
            else:
                current_block += "\n" + line

        # Save the very last block after the loop finishes
        if current_block.strip():
            blocks.append((current_date, current_block))

        # Process all blocks concurrently
        gathered = await asyncio.gather(*[self.process_block(date, block) for date, block in blocks])

        # Aggregate results from all coroutines
        results_by_date = {}
        unextracted_blocks = []
        for date, items, unextracted in gathered:
            if date not in results_by_date:
                results_by_date[date] = {}
            for chant, counts in items.items():
                if chant not in results_by_date[date]:
                    results_by_date[date][chant] = []
                results_by_date[date][chant].extend(counts)
            if unextracted:
                unextracted_blocks.append((date, unextracted))

        return results_by_date, unextracted_blocks


# --- OUTPUT GENERATION ---
# This function takes the collected data and saves it into files (TXT and CSV)
def generate_outputs(results_by_date, output_dir):
    # 1. Prepare Summary (Combine all dates into one big total)
    all_counts = {}
    for date, chants in results_by_date.items():
        for chant, counts in chants.items():
            if chant not in all_counts: all_counts[chant] = []
            all_counts[chant].extend(counts)

    if not all_counts:
        print("No data extracted.")
        return

    # 2. Write summary.txt
    # This file looks like: Chant Name = 100+200+300
    txt_path = os.path.join(output_dir, 'summary.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        for chant in CHANT_ORDER:
            counts = all_counts.get(chant, [])
            if counts:
                val_str = "+".join(map(str, counts))
                f.write(f"{chant} ={val_str}\n")
        for chant in all_counts:
            if chant not in CHANT_ORDER:
                val_str = "+".join(map(str, all_counts[chant]))
                f.write(f"{chant} ={val_str}\n")

    # 3. Write summary.csv
    # This is a spreadsheet row with the total sum for each chant
    csv_path = os.path.join(output_dir, 'summary.csv')
    summary_row = {}
    for chant in CHANT_ORDER:
        summary_row[chant] = sum(all_counts.get(chant, [0]))
    for chant in all_counts:
        if chant not in CHANT_ORDER:
            summary_row[chant] = sum(all_counts[chant])
    pd.DataFrame([summary_row]).to_csv(csv_path, index=False)

    # 4. Write daily_breakdown.csv
    # This creates a table where each row is a different date
    if len(results_by_date) > 1 or "Unknown" not in results_by_date:
        detailed_csv_path = os.path.join(output_dir, 'daily_breakdown.csv')
        detailed_rows = []
        dates = sorted(results_by_date.keys())
        for date in dates:
            chants = results_by_date[date]
            if not chants and date == "Unknown": continue
            row = {'Date': date}
            for chant in CHANT_ORDER:
                row[chant] = sum(chants.get(chant, [0]))
            for chant in chants:
                if chant not in CHANT_ORDER:
                    row[chant] = sum(chants[chant])
            detailed_rows.append(row)
        if detailed_rows:
            pd.DataFrame(detailed_rows).to_csv(detailed_csv_path, index=False)

    print(f"Results saved to {output_dir}")


# Simple function to show how to use the script from the command line
def print_usage():
    print("""
Darood Bank App - Usage Guide
=============================
python main.py              Process all new .txt files in 'inputs/'
python main.py <filename>   Process a specific file
""")


# --- MAIN EXECUTION ---
# This is the starting point of the script
async def main():
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h"]:
        print_usage()
        return

    input_files = []
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if os.path.exists(target):
            input_files = [target]
        else:
            processed_path = os.path.join('inputs', 'processed', os.path.basename(target))
            if os.path.exists(processed_path):
                input_files = [processed_path]
            else:
                print(f"File not found: {target}")
                return
    else:
        input_dir = 'inputs'
        if os.path.exists(input_dir):
            input_files = [os.path.join(input_dir, f) for f in os.listdir(input_dir)
                           if f.endswith('.txt') and os.path.isfile(os.path.join(input_dir, f))]

    if not input_files:
        print("No input files found.")
        return

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_output_dir = os.path.join('outputs', timestamp)
    os.makedirs(run_output_dir, exist_ok=True)

    system = ChantSystem()
    all_unextracted = []

    for file_path in input_files:
        print(f"\nProcessing: {file_path}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            extracted_data, unextracted_blocks = await system.run(lines)
            all_unextracted.extend(unextracted_blocks)

            file_basename = os.path.splitext(os.path.basename(file_path))[0]
            file_output_dir = os.path.join(run_output_dir, file_basename)
            os.makedirs(file_output_dir, exist_ok=True)

            generate_outputs(extracted_data, file_output_dir)

            norm_path = os.path.normpath(file_path)
            if 'inputs' in norm_path and 'processed' not in norm_path:
                processed_dir = os.path.join('inputs', 'processed')
                os.makedirs(processed_dir, exist_ok=True)
                dest = os.path.join(processed_dir, os.path.basename(file_path))
                if os.path.exists(dest):
                    base, ext = os.path.splitext(os.path.basename(file_path))
                    dest = os.path.join(processed_dir, f"{base}_{timestamp}{ext}")
                shutil.move(file_path, dest)
                print(f"Archived to {dest}")
        except Exception as e:
            print(f"Error: {e}")

    # Write any blocks that yielded no data so they can be manually reviewed
    if all_unextracted:
        unextracted_path = os.path.join(run_output_dir, 'unextracted.txt')
        with open(unextracted_path, 'w', encoding='utf-8') as f:
            for date, block in all_unextracted:
                f.write(f"=== Date: {date} ===\n{block}\n\n")
        print(f"Unextracted blocks written to {unextracted_path}")

    print(f"\nDone! Outputs: {run_output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
