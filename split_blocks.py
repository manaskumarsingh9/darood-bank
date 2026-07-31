"""
Step 1 of the token-free pipeline: split a WhatsApp export into one JSON object
per message. This is fully deterministic (no AI, no API calls).

Usage:
    python split_blocks.py inputs/24-to-28-Feb-2026_counts.txt blocks.jsonl

Output: JSON Lines, one message per line:
    {"id": 0, "envelope_date": "24/02", "sender": "Bashir Patel", "text": "..."}

Note: `envelope_date` is only the WhatsApp *posting* date (day/month, no year).
The real chant date is decided during extraction, because many messages report a
different day inside the body. Continuation lines are folded into their message.
"""
import sys
import json
import re

# Supports both [date, time] and [time, date] formats
# [24/02, 4:41 am] or [5:50 am, 06/07/2026] followed by sender: message
def parse_message_header_full(line):
    """Parse WhatsApp message header; returns (time, date, sender, text) or None.

    Same matching as parse_message_header, but also hands back the timestamp,
    which ingest.py needs to order messages pasted out of sequence.
    """
    # Try [time, date] format first (common from mobile)
    m = re.match(
        r'^\[?\s*'
        r'(\d{1,2}:\d{2}(?::\d{2})?\s*[ap]\.?m\.?)'  # time
        r',\s*'
        r'(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)'   # envelope date
        r'\]?\s*'
        r'(?:-\s*)?'
        r'([^:]+?):\s*'                              # sender
        r'(.*)$',                                     # message text
        line.strip(),
        re.IGNORECASE,
    )
    if m:
        time, date, sender, text = m.groups()
        return time.strip(), date, sender.strip(), text.strip()

    # Try [date, time] format (common from laptop)
    m = re.match(
        r'^\[?\s*'
        r'(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)'   # envelope date
        r',\s*'
        r'(\d{1,2}:\d{2}(?::\d{2})?\s*[ap]\.?m\.?)'  # time
        r'\]?\s*'
        r'(?:-\s*)?'
        r'([^:]+?):\s*'                              # sender
        r'(.*)$',                                     # message text
        line.strip(),
        re.IGNORECASE,
    )
    if m:
        date, time, sender, text = m.groups()
        return time.strip(), date, sender.strip(), text.strip()

    return None


def parse_message_header(line):
    """Parse WhatsApp message header; returns (date, sender, text) or None."""
    parsed = parse_message_header_full(line)
    if parsed is None:
        return None
    _time, date, sender, text = parsed
    return date, sender, text


def split_messages(lines):
    messages = []
    current = None
    for raw in lines:
        line = raw.rstrip("\n")
        header = parse_message_header(line)
        if header:
            if current is not None:
                messages.append(current)
            date, sender, text = header
            current = {
                "id": len(messages),
                "envelope_date": date,
                "sender": sender,
                "text": text,
            }
        elif current is not None:
            # continuation line of the current message
            current["text"] += "\n" + line
        # a stray line before any header is dropped (WhatsApp system lines)
    if current is not None:
        messages.append(current)
    return messages


def main():
    if len(sys.argv) != 3:
        print("Usage: python split_blocks.py <input.txt> <output.jsonl>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        messages = split_messages(f.readlines())

    with open(sys.argv[2], "w", encoding="utf-8") as f:
        for msg in messages:
            msg["text"] = msg["text"].strip()
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    print(f"Wrote {len(messages)} messages to {sys.argv[2]}")


if __name__ == "__main__":
    main()
