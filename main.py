import os
import sys
import pandas as pd
import json
import re
import uuid
import asyncio
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Define the Agent's Instruction for parsing and normalization
SYSTEM_INSTRUCTION = """
You are a religious chant data extractor. Extract 'Chant Name' and 'Count' from WhatsApp messages.
1. Languages: Messages can be in English script or Devanagari (Hindi).
2. Normalization Rules (Map variants to these Canonical Names):
   - 'Durood Shareef': दरूद शरीफ, durud sarif, Durood, darood shrif, 108 darood
   - 'Gayatri Mantra': गायत्री मंत्र, gayatri mantra, shri gayatri
   - 'Kalma': कलमा शरीफ, kalma sarif, kalaam paak, kalma
   - 'Shizra Sharif': शीजरा शरीफ, shizra sharif
   - 'Surah Ikhlas': सुरह इखलास, surah ikhlas
   - 'Surah Fatiha': सुरह फातिया, surah fatiha, fatiha
3. Extraction: Look for numeric counts. Sometimes the name comes first, sometimes the count.
   - Example: "151 Durood" -> {"chant": "Durood Shareef", "count": 151}
   - Example: "गायत्री मंत्र 216" -> {"chant": "Gayatri Mantra", "count": 216}
4. Multi-item: If a message has multiple chants, extract them ALL.
5. Output: Return ONLY a JSON list of objects: [{"chant": "Canonical Name", "count": 123}, ...]
   - If nothing found, return [].
"""

TIMESTAMP_REGEX = r'^\[?(\d{1,2}:\d{2}\s*[ap]m),\s+(\d{1,2}/\d{2}/\d{2,4})\]?\s+(?:-\s+)?([^:]+):\s+(.*)'

async def process_messages_async(raw_lines):
    agent = Agent(
        name="ChantExtractor",
        model="gemini-2.5-flash",
        instruction=SYSTEM_INSTRUCTION
    )
    runner = InMemoryRunner(agent=agent, app_name="ChantExtractorApp")
    
    results_by_date = {}
    current_date = "Unknown"
    current_block = ""
    
    async def flush_block(date, block):
        if not block: return
        
        user_id = "default_user"
        session_id = str(uuid.uuid4())
        
        # Create session via session_service
        await runner.session_service.create_session(
            app_name="ChantExtractorApp",
            user_id=user_id,
            session_id=session_id
        )
        
        new_msg = types.Content(parts=[types.Part(text=block)])
        
        try:
            full_text = ""
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=new_msg
            ):
                if hasattr(event, 'content') and event.content:
                    for part in event.content.parts:
                        if part.text:
                            full_text += part.text
            
            if not full_text:
                return

            # Robust JSON extraction
            # Remove markdown blocks and surrounding whitespace
            cleaned_resp = full_text.strip()
            if "```json" in cleaned_resp:
                cleaned_resp = cleaned_resp.split("```json")[-1].split("```")[0].strip()
            elif "```" in cleaned_resp:
                cleaned_resp = cleaned_resp.split("```")[-1].split("```")[0].strip()

            # Find the actual JSON list boundaries
            start_idx = cleaned_resp.find('[')
            end_idx = cleaned_resp.rfind(']')
            if start_idx != -1 and end_idx != -1:
                json_str = cleaned_resp[start_idx:end_idx+1]
                try:
                    extracted_list = json.loads(json_str)
                except json.JSONDecodeError as je:
                    print(f"JSON Decode Error: {je}\nRaw Text: {full_text}")
                    return
            else:
                print(f"No JSON list found in response.\nRaw Text: {full_text}")
                return
            
            if date not in results_by_date:
                results_by_date[date] = {}
            for item in extracted_list:
                chant = item.get('chant')
                count = item.get('count')
                if chant and count:
                    if chant not in results_by_date[date]:
                        results_by_date[date][chant] = []
                    results_by_date[date][chant].append(count)
        except Exception as e:
            print(f"Error processing block: {e}")

    for line in raw_lines:
        match = re.match(TIMESTAMP_REGEX, line, re.IGNORECASE)
        if match:
            await flush_block(current_date, current_block)
            time, date, sender, text = match.groups()
            current_date = date
            current_block = text
        else:
            current_block += "\n" + line
            
    await flush_block(current_date, current_block)
    return results_by_date

def generate_outputs(results_by_date):
    all_counts = {}
    for date, chants in results_by_date.items():
        for chant, counts in chants.items():
            if chant not in all_counts:
                all_counts[chant] = []
            all_counts[chant].extend(counts)
            
    if not all_counts:
        print("No chants extracted. Please check the input format and API key.")
        return

    with open('output.txt', 'w', encoding='utf-8') as f:
        for chant, counts in all_counts.items():
            counts_str = "+".join(map(str, counts))
            f.write(f"{chant} = {counts_str}\n")
    
    summary_data = {chant: [sum(counts)] for chant, counts in all_counts.items()}
    df = pd.DataFrame(summary_data)
    df.to_csv('output.csv', index=False)
    
    print("Outputs generated: output.txt and output.csv")

async def main():
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        else:
            lines = [filepath]
    else:
        print("Usage: python main.py <file_path> OR python main.py 'your message here'")
        sys.exit(1)
        
    extracted_data = await process_messages_async(lines)
    generate_outputs(extracted_data)

if __name__ == "__main__":
    asyncio.run(main())
