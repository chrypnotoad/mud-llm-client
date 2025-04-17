import os
import json
import re
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_API_URL = os.getenv('OPENROUTER_API_URL')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL')

LOG_DIR = '.'  # Current directory
LOG_PATTERN = re.compile(r'.*_mud_log_.*\\.txt$|.*_mud_log_.*\.txt$', re.IGNORECASE)

SUMMARY_PROMPT = """
You are Arvandor a human warrior. write your journal entry based on the following log. stay in character be detailed and concise."
"""

def summarize_log(log_text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "type": "text",
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": log_text}
        ]
    }
    response = requests.post(OPENROUTER_API_URL, headers=headers, data=json.dumps(payload))
    response.raise_for_status()
    # Just return the text content
    resp_json = response.json()
    if 'choices' in resp_json:
        return resp_json['choices'][0]['message']['content'].strip()
    return response.text.strip()

def get_new_goal(summaries, current_goal=None):
    prompt = """
You are Arvandor, a human warrior in Abandoned Realms. Based on the following journal summaries of your recent MUD sessions, and your current goal (if any), describe your new main goal for the next session. If there is no current goal, infer one from the summaries. Be specific and actionable Both short and long term goal. Speak in first person as you are Arvandor. This is an in game goal. Speak in first person and in character Focus on the most recent journal entry.
"""
    if current_goal:
        prompt += f"\nCurrent goal: {current_goal}\n"
    prompt += "\nJournal summaries:\n"
    for entry in summaries:
        prompt += f"- {entry['journal_entry']}\n"
    prompt += "\nNew goal:"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "type": "text",
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": prompt}
        ]
    }
    response = requests.post(OPENROUTER_API_URL, headers=headers, data=json.dumps(payload))
    response.raise_for_status()
    resp_json = response.json()
    if 'choices' in resp_json:
        return resp_json['choices'][0]['message']['content'].strip()
    return response.text.strip()

def main():
    journal_path = 'mud_journal.json'
    # Load existing journal if it exists
    if os.path.exists(journal_path):
        with open(journal_path, 'r', encoding='utf-8') as jf:
            journal = json.load(jf)
        current_goal = journal.get('goal')
        all_entries = journal.get('entries', [])
    else:
        current_goal = None
        all_entries = []
    # Get all log files, sorted by creation time (oldest first)
    log_files = [fname for fname in os.listdir(LOG_DIR) if LOG_PATTERN.match(fname)]
    log_files = sorted(log_files, key=lambda f: os.path.getctime(os.path.join(LOG_DIR, f)))
    for fname in log_files:
        # Find existing entry for this log file, if any
        entry_idx = next((i for i, entry in enumerate(all_entries) if entry.get('log_file') == fname), None)
        existing_entry = all_entries[entry_idx] if entry_idx is not None else None
        # Skip if already summarized and not empty
        if existing_entry and existing_entry.get('journal_entry'):
            print(f"Skipping {fname} (already summarized and complete)...")
            continue
        # Check line count before processing
        with open(fname, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        if len(lines) < 100:
            print(f"Deleting {fname} (less than 100 lines)...")
            os.remove(fname)
            # Remove entry if it exists
            if entry_idx is not None:
                all_entries.pop(entry_idx)
            continue
        log_text = ''.join(lines)
        print(f"Summarizing {fname}...")
        try:
            summary = summarize_log(log_text)
            match = re.search(r'(\d{8})-(\d{6})', fname)
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                time = f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
            else:
                date = ''
                time = ''
            journal_entry = {
                "date": date,
                "time": time,
                "log_file": fname,
                "journal_entry": summary
            }
            if existing_entry:
                all_entries[entry_idx] = journal_entry
            else:
                all_entries.append(journal_entry)
        except Exception as e:
            print(f"Failed to summarize {fname}: {e}")
    # Update the goal using the LLM
    if all_entries:
        new_goal = get_new_goal(all_entries, current_goal)
    else:
        new_goal = current_goal
    # Write all entries and the goal to a single file
    with open(journal_path, 'w', encoding='utf-8') as out_f:
        json.dump({"goal": new_goal, "entries": all_entries}, out_f, indent=2)
    print("Wrote all journal entries and updated goal to mud_journal.json")

if __name__ == '__main__':
    main()
