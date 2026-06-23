# Import necessary tools (libraries) that the script needs to run
import os          # Provides ways to interact with the operating system (like finding files)
import sys         # Lets us access things related to the Python interpreter (like command line arguments)
import pandas as pd # A powerful tool for handling data in tables (like Excel spreadsheets)
import json        # Helps us work with JSON data format (often used by AI for structured output)
import re          # Stands for "Regular Expressions" - used to find specific patterns in text
import uuid        # Generates unique IDs (used here for AI chat sessions)
import asyncio     # Allows the script to do multiple things at once (asynchronous programming)
import datetime    # Helps us work with dates and times
import shutil      # Used for high-level file operations (like moving files between folders)

# Import special tools for interacting with Google's AI agents
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

# Import a tool to load secret settings (like API keys) from a file named '.env'
from dotenv import load_dotenv

# Load the secret settings from the '.env' file so they are available to the script
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

# --- THE CHANT SYSTEM ---
# This class acts as the "brain" of our application, managing the AI agents.
class ChantSystem:
    def __init__(self):
        # Set up the Extractor AI agent
        self.extractor = Agent(name="Extractor", model="gemini-2.5-flash", instruction=EXTRACTOR_INSTRUCTION)
        # Set up the Verifier AI agent
        self.verifier = Agent(name="Verifier", model="gemini-2.5-flash", instruction=VERIFIER_INSTRUCTION)
        # The runner helps execute these agents
        self.runner = InMemoryRunner(agent=self.extractor, app_name="DaroodBankApp")
        # This dictionary will store our final results organized by date
        self.results_by_date = {}

    # This function talks to an AI agent and gets a response back
    async def get_agent_response(self, agent, prompt):
        user_id = "reconciliation_user"
        session_id = str(uuid.uuid4()) # Generate a unique session ID
        # Create a new session for this interaction
        await self.runner.session_service.create_session(app_name="DaroodBankApp", user_id=user_id, session_id=session_id)
        
        # Tell the runner which agent we want to use (Extractor or Verifier)
        self.runner.agent = agent
        
        # Prepare the message for the AI
        new_msg = types.Content(parts=[types.Part(text=prompt)])
        full_text = ""
        
        # Stream the response from the AI and collect it into full_text
        async for event in self.runner.run_async(user_id=user_id, session_id=session_id, new_message=new_msg):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if part.text: full_text += part.text
        return full_text

    # This function takes the AI's text response and tries to turn it into a Python list/dictionary
    def parse_json(self, text):
        cleaned = text.strip()
        # Clean up Markdown formatting like ```json ... ``` that AI often adds
        if "```json" in cleaned: cleaned = cleaned.split("```json")[-1].split("```")[0].strip()
        elif "```" in cleaned: cleaned = cleaned.split("```")[-1].split("```")[0].strip()
        
        # Find the actual JSON content within the text
        start, end = cleaned.find('{'), cleaned.rfind('}')
        if start == -1: start, end = cleaned.find('['), cleaned.rfind(']')
        
        # Attempt to make the string valid JSON if it's a sequence of objects
        if cleaned.startswith('{') and '},{' in cleaned and not cleaned.endswith(']'):
            cleaned = f"[{cleaned}]"

        start, end = cleaned.find('{'), cleaned.rfind('}')
        if start == -1: start, end = cleaned.find('['), cleaned.rfind(']')

        if start != -1 and end != -1:
            json_candidate = cleaned[start:end+1]
            try:
                parsed_object = json.loads(json_candidate)
                # Add validation: if it's a list, ensure all elements are dictionaries
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
            except json.JSONDecodeError: # Catch specific JSON decoding errors
                print(f"Warning: Failed to decode JSON from content: {cleaned[start:end+1]}")
                return None
        return None

    # This function processes a single "block" of message text for a specific date
    async def process_block(self, date, block):
        if not block or not block.strip(): return
        
        print(f"  Processing data for {date}...")
        
        # Step 1: Ask the Extractor to pull out the data
        raw_extraction = await self.get_agent_response(self.extractor, block)
        current_data = self.parse_json(raw_extraction) or []
        
        # Step 2: Double-check with the Verifier (up to 2 attempts)
        for attempt in range(2): 
            verify_prompt = f"RAW MESSAGE:\n{block}\n\nCURRENT EXTRACTION:\n{json.dumps(current_data)}"
            verification_raw = await self.get_agent_response(self.verifier, verify_prompt)
            verification_result = self.parse_json(verification_raw)
            
            # If the Verifier says everything matches, we are done
            if not verification_result or verification_result.get("status") == "MATCH": 
                break
            
            # If the Verifier found a mistake, use its corrected data instead
            current_data = verification_result.get("corrected_data") or current_data
        
        # Step 3: Store the extracted counts in our dictionary
        if date not in self.results_by_date: 
            self.results_by_date[date] = {}
        
        for item in current_data:
            if isinstance(item, dict): # Ensure item is a dictionary
                chant, count = item.get('chant'), item.get('count')
                if chant and count is not None:
                    try:
                        # Ensure the count is a whole number (integer)
                        val = int(count)
                    except ValueError: # Catch specific error for invalid conversions
                        print(f"Warning: Could not convert count '{count}' to integer for chant '{chant}' in date '{date}'. Skipping item.")
                        continue

                    # Add this count to the list for this specific chant on this date
                    if chant not in self.results_by_date[date]:
                        self.results_by_date[date][chant] = []
                    self.results_by_date[date][chant].append(val)
            else:
                print(f"Warning: Expected dictionary item but received {type(item)}: '{item}' for date '{date}'. Skipping item.")

    # This is the main function of the ChantSystem class
    async def run(self, raw_lines):
        self.results_by_date = {} # Clear any old results
        current_date, current_block = "Unknown", ""
        
        # Go through the message file line by line
        for line in raw_lines:
            line = line.strip()
            if not line: continue
            
            # Use our search patterns (regex) to see if this line starts a new message
            ts_match = re.match(TIMESTAMP_REGEX, line, re.IGNORECASE)
            simple_match = re.match(SIMPLE_DATE_REGEX, line)
            
            if ts_match or simple_match:
                # If we were already building a message block, process it before moving to the next one
                if current_block.strip():
                    await self.process_block(current_date, current_block)
                
                # Extract the date and start a new block
                if ts_match:
                    _, date, _, text = ts_match.groups()
                    current_date, current_block = date, text
                else:
                    date = simple_match.group(1)
                    current_date, current_block = date, line
            else:
                # If this line isn't a new message, it must be a continuation of the current message
                current_block += "\n" + line
        
        # Process the very last block after the loop finishes
        if current_block.strip():
            await self.process_block(current_date, current_block)
            
        return self.results_by_date

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
        # Loop through our preferred chant order
        for chant in CHANT_ORDER:
            counts = all_counts.get(chant, [])
            if counts:
                val_str = "+".join(map(str, counts)) # Join numbers with '+' sign
                f.write(f"{chant} ={val_str}\n")
        
        # Also include any chants found that weren't in our list
        for chant in all_counts:
            if chant not in CHANT_ORDER:
                val_str = "+".join(map(str, all_counts[chant]))
                f.write(f"{chant} ={val_str}\n")

    # 3. Write summary.csv 
    # This is a spreadsheet row with the total sum for each chant
    csv_path = os.path.join(output_dir, 'summary.csv')
    summary_row = {}
    for chant in CHANT_ORDER:
        # Sum up all the individual numbers for this chant
        summary_row[chant] = sum(all_counts.get(chant, [0]))
    
    # Add any extra chants found
    for chant in all_counts:
        if chant not in CHANT_ORDER:
            summary_row[chant] = sum(all_counts[chant])
            
    # Save the data to a CSV file using the pandas library
    pd.DataFrame([summary_row]).to_csv(csv_path, index=False)

    # 4. Write daily_breakdown.csv
    # This creates a table where each row is a different date
    if len(results_by_date) > 1 or "Unknown" not in results_by_date:
        detailed_csv_path = os.path.join(output_dir, 'daily_breakdown.csv')
        detailed_rows = []
        # Sort the dates so the table is in order
        dates = sorted(results_by_date.keys())
        for date in dates:
            chants = results_by_date[date]
            if not chants and date == "Unknown": continue
            row = {'Date': date}
            for chant in CHANT_ORDER:
                row[chant] = sum(chants.get(chant, [0]))
            
            # Add extras
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
    # Check if the user asked for help
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h"]:
        print_usage()
        return

    # Decide which files to process
    input_files = []
    if len(sys.argv) > 1:
        # User provided a specific file name as an argument
        target = sys.argv[1]
        if os.path.exists(target): 
            input_files = [target]
        else:
            # Check if the file is in the 'processed' folder
            processed_path = os.path.join('inputs', 'processed', os.path.basename(target))
            if os.path.exists(processed_path): 
                input_files = [processed_path]
            else:
                print(f"File not found: {target}")
                return
    else:
        # No argument provided, so look for all .txt files in the 'inputs' folder
        input_dir = 'inputs'
        if os.path.exists(input_dir):
            input_files = [os.path.join(input_dir, f) for f in os.listdir(input_dir) 
                           if f.endswith('.txt') and os.path.isfile(os.path.join(input_dir, f))]
    
    if not input_files:
        print("No input files found.")
        return

    # Create a unique folder for this run based on the current date and time
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_output_dir = os.path.join('outputs', timestamp)
    os.makedirs(run_output_dir, exist_ok=True)

    # Initialize the chant system
    system = ChantSystem()
    
    # Process each file one by one
    for file_path in input_files:
        print(f"\nProcessing: {file_path}")
        try:
            # Read all lines from the text file
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Run the AI extraction on these lines
            extracted_data = await system.run(lines)
            
            # Create a folder for this specific file's results
            file_basename = os.path.splitext(os.path.basename(file_path))[0]
            file_output_dir = os.path.join(run_output_dir, file_basename)
            os.makedirs(file_output_dir, exist_ok=True)
            
            # Generate the text and CSV reports
            generate_outputs(extracted_data, file_output_dir)
            
            # Archive the input file by moving it to the 'inputs/processed' folder
            norm_path = os.path.normpath(file_path)
            if 'inputs' in norm_path and 'processed' not in norm_path:
                processed_dir = os.path.join('inputs', 'processed')
                os.makedirs(processed_dir, exist_ok=True)
                dest = os.path.join(processed_dir, os.path.basename(file_path))
                
                # If a file with the same name already exists in 'processed', add a timestamp to it
                if os.path.exists(dest):
                    base, ext = os.path.splitext(os.path.basename(file_path))
                    dest = os.path.join(processed_dir, f"{base}_{timestamp}{ext}")
                
                shutil.move(file_path, dest)
                print(f"Archived to {dest}")
        except Exception as e:
            # If something goes wrong, print the error message
            print(f"Error: {e}")

    print(f"\nDone! Outputs: {run_output_dir}")

# If this script is run directly (not imported), start the main function
if __name__ == "__main__":
    asyncio.run(main())
