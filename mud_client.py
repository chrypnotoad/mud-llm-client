import telnetlib
import requests
import time
import json
import os
import re
from dotenv import load_dotenv
from pymongo import MongoClient

# ------------------ ENV & MONGO SETUP ------------------
load_dotenv()

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
DB_NAME = os.getenv('MUD_DB_NAME', 'mud_llm')
COLL_NAME = os.getenv('MUD_HISTORY_COLL', 'chat_history')

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
coll = db[COLL_NAME]

USERNAME = os.getenv('MUD_USERNAME')
PASSWORD = os.getenv('MUD_PASSWORD')
MUD_HOST = os.getenv('MUD_HOST')
MUD_PORT = int(os.getenv('MUD_PORT'))
OLLAMA_API_URL = os.getenv('OLLAMA_API_URL', 'http://localhost:11434/v1/chat')
SESSION_ID = USERNAME  # You can make this smarter: f"{USERNAME}_{date}" for multi-session

JOURNAL_PATH = 'mud_journal.jsonl'
NUM_MEMORIES = 5  # Number of recent journal entries to inject

# --------------- JOURNAL UTILITIES ---------------
def append_journal_entry(journal_text, extra_fields=None):
    entry = {
        "timestamp": time.time(),
        "journal": journal_text
    }
    if extra_fields:
        entry.update(extra_fields)
    with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def get_recent_journal_entries(n=NUM_MEMORIES):
    if not os.path.exists(JOURNAL_PATH):
        return []
    try:
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(line)["journal"] for line in lines if line.strip()]
    except Exception:
        return []

def load_current_goal():
    try:
        with open('mud_journal.json', 'r', encoding='utf-8') as jf:
            journal = json.load(jf)
            return journal.get('goal', '')
    except Exception:
        return ''

# --------------- MONGO CHAT HISTORY UTILITIES ---------------
def save_message_to_db(message, session_id=SESSION_ID):
    coll.insert_one({
        "session_id": session_id,
        "timestamp": time.time(),
        "message": message
    })

def load_chat_history_from_db(session_id=SESSION_ID):
    msgs = list(coll.find({"session_id": session_id}).sort("timestamp", 1))
    return [m['message'] for m in msgs]

# --------------- PROMPT & LLM ---------------
base_system_prompt = """You are playing a live online MUD game. Respond ONLY with a JSON object containing **four fields**:
- 'journal': keep a running journal of what you are doing in game to help you remember,
- 'reasoning': your reasoning,
- 'decision': your conclusion about your next action,
- 'game_input': the command to send,


You are an EXPERT MUD player.

<commands>
List of commands:
Movement:	align, enter, exits, follow, go, map, meditate, minimap, mm, recall, rest, scan, sit, sleep, solitude, stand, tag, visible, wake, walk, where, area, \n
Config:	alias, autoassist, autocomplete, autodef, autoexit, autogold, autogrammar, autogrip, autohint, autolist, autoloot, autopeace, autosac, autosplit, background, brief, channels, color, compact, description, donate, nofollow, nomagic, nosummon, noyel, noyell, password, prompt, remove, scroll, sell, submit, target, unlock, untarget, wimpy \n
Objects:    appraise, bait, barter, bid, brandish, buy, cancel, close, collect, combine, drink, drop, eat, feed, fill, get, give, hire, hold, list, lock, market, open, pitch, possession, pour, pull, put, quaff, recite, reel, reload, reshape, retire, retrieve, return, sacrifice, take, unpitch, value, wear, wield, zap, \n
Communication:	application, apply, commend, complaint, deaf, emote, esay, esays, gossip, greet, gtell, honor, ignore, order, pmote, pose, pray, quiet, replay, reply, report, say, socials, tell, yell
Information	affects, applicants, areas, changes, commands, compare, consider, credits, equipment, event, examine, help, helpsearch, history, idea, info, inventory, issues, look, news, note, oversee, powers, preview, proficiency, read, score, score2, show, skills, songs, spells, time, timeconvert, typo, wealth, weather, whios, who, whois, wizlist, worth, x \n
Combat:	cast, channel, chant, commune, confinement, ecast, flee, focus, kill, murder, sing \n
Misc:	addquest, allow, bet, bug, check, delete, demote, dice, enlist, gain, gamble, gift, grip, group, hammer, heal, induct, outfit, pardon, pay, pet, practice, promote, puke, questor, questor2, quit, raise, refuse, rehearse, release, save, surrender, task, train, uninduct
</commands>\n
<tips>
- you can send multiple commands at the same time by separating them each with a pipe | 
- most characters are NPCs and don't respond to "say" or "tell" only specific commands such as "list" to see what they offer
</tips>
"""

def clean_llm_json(content):
    """
    Clean and parse LLM output into a Python dict, tolerating
    single/double quotes and extra/missing braces, even with whitespace between braces.
    """
    content = content.strip()
    # Remove code block markers if present
    content = re.sub(r'^```(?:json)?\n?', '', content)
    content = re.sub(r'\n?```$', '', content)
    content = content.strip()
    # Remove outer single/double quotes if present
    if (content.startswith("'") and content.endswith("'")) or (content.startswith('"') and content.endswith('"')):
        content = content[1:-1].strip()
    # Remove escaped single quotes (from Python stringification)
    content = content.replace("\\'", "'")
    # Iteratively strip extra curly braces (with optional whitespace) from the start/end until valid JSON or until no braces left
    for _ in range(8):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Remove all extra left curly at start (with whitespace)
            new_content = re.sub(r'^(\s*\{\s*){2,}', '{', content)
            if new_content != content:
                content = new_content
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    pass
            # Remove all extra right curly at end (with whitespace)
            new_content = re.sub(r'(\s*\}\s*){2,}$', '}', content)
            if new_content != content:
                content = new_content
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    pass
            # Remove extra left curly at start (unmatched)
            if content.startswith("{") and not content.rstrip().endswith("}"):
                content = re.sub(r'^\s*\{\s*', '', content)
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    continue
            # Remove extra right curly at end (unmatched)
            if content.rstrip().endswith("}") and not content.lstrip().startswith("{"):
                content = re.sub(r'\s*\}\s*$', '', content)
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    continue
            break
    # If we end up here, parsing failed
    raise


def get_ai_response(prompt, chat_history, current_goal):
    # Build the system prompt, adding recent memories
    recent_memories = get_recent_journal_entries(NUM_MEMORIES)
    memories_text = ""
    if recent_memories:
        memories_text = "Here are your recent journal entries:\n" + "\n".join(f"- {m}" for m in recent_memories) + "\n"
    system_prompt = (
        base_system_prompt
        + "\n\n"
        + memories_text
        + f"<goal>:\n{current_goal}\n</goal>\n"
        + "Remember to keep your 'journal' updated each turn."
    )

    # Ensure system prompt is always the first message
    if not chat_history or chat_history[0].get('role') != "system":
        chat_history.insert(0, {"role": "system", "content": system_prompt})
        save_message_to_db({"role": "system", "content": system_prompt})
    else:
        chat_history[0]["content"] = system_prompt
        # Update in DB: For simplicity, we leave previous system prompt as-is

    chat_history.append({"role": "user", "content": prompt})
    save_message_to_db({"role": "user", "content": prompt})

    # Optionally save context for web UI
    try:
        with open("ai_state.json", "w", encoding="utf-8") as f:
            json.dump({
                "prompt": prompt,
                "chat_history": chat_history,
                "ai_response": None
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Could not write ai_state.json: {e}")

    payload = {
        "model": "deepseek-r1:32b", 
        "messages": chat_history,
        "format": {
            "type": "object",
            "properties": {
                "journal": {"type": "string"},
                "reasoning": {"type": "string"},
                "decision": {"type": "string"},
                "game_input": {"type": "string"}
                
            },
            "required": ["reasoning", "decision", "game_input", "journal"]
        },
        "options": {"temperature": 1.0},
        "stream": False,
        "think": True
    }

    headers = {"Content-Type": "application/json"}
    url = OLLAMA_API_URL

    response = requests.post(
        url=url,
        headers=headers,
        data=json.dumps(payload)
    )
    response.raise_for_status()
    response_text = response.text.strip()
    try:
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not match:
            print("[ERROR] No JSON object found in response. Full response:", response_text)
            raise ValueError("No JSON object found in response.")

        outer = json.loads(match.group(0))
        inner_content = outer.get("message", {}).get("content", "")
        content = inner_content.strip()
        
        if not content:
            print("[ERROR] Ollama response content is empty. Full response:", response_text)
            return None

        parsed = clean_llm_json(content)
        chat_history.append({"role": "assistant", "content": content})
        save_message_to_db({"role": "assistant", "content": content})

        # Optionally save AI response for web UI
        try:
            with open("ai_state.json", "w", encoding="utf-8") as f:
                json.dump({
                    "prompt": prompt,
                    "chat_history": chat_history,
                    "ai_response": parsed
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] Could not write ai_state.json: {e}")

        # Write the journal entry to disk
        journal_entry = parsed.get('journal', '')
        append_journal_entry(
            journal_entry,
            {
                "reasoning": parsed.get('reasoning', ''),
                "decision": parsed.get('decision', ''),
                "input": parsed.get('game_input', '')
            }
        )

        return parsed

    except json.JSONDecodeError as e:
        print("JSON decode error:", e)
        raise

# ------------------- MAIN GAME LOOP -------------------
def main():
    tn = None
    log_file = None
    try:
        chat_history = load_chat_history_from_db()
        current_goal = load_current_goal()

        tn = telnetlib.Telnet(MUD_HOST, MUD_PORT)
        print(f"Connected to {MUD_HOST}:{MUD_PORT}")

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        log_filename = f'{USERNAME}_mud_log_{timestamp}.txt'
        log_file = open(log_filename, 'a', encoding='utf-8')

        # Log non-secret environment variables at the top of the log file
        env_vars_to_log = {
            'MUD_HOST': MUD_HOST,
            'MUD_PORT': MUD_PORT,
            'USERNAME': USERNAME,
            'SESSION_ID': SESSION_ID
        }
        log_file.write("# Environment variables (non-secret):\n")
        for k, v in env_vars_to_log.items():
            log_file.write(f"{k}={v}\n")
        log_file.write("\n")
        log_file.flush()

        buffer_window = []
        logged_in = False
        one_time_score_sent = False
        while True:
            # Send 'score' once after login when first prompt appears
            if not logged_in and not one_time_score_sent:
                tn.write(b'score\n')
                one_time_score_sent = True
                time.sleep(1)
            data = tn.read_very_eager().decode('utf-8', errors='ignore')

            # Auto-continue if prompt is present
            if '[Hit Return to continue]' in data:
                tn.write(b'\n')
                continue

            if data:
                print(data, end='')
                if 'By what name do you wish to be known' in data or 'By what name do you wish to be remembered' in data:
                    tn.write(USERNAME.encode('utf-8') + b'\n')
                    print(f"Sending username: {USERNAME}\n")
                    continue
                if 'Password:' in data:
                    tn.write(PASSWORD.encode('utf-8') + b'\n')
                    print(f"Sending password: ***\n")
                    logged_in = True
                    continue

                log_file.write(data)
                log_file.flush()
                buffer_window.append(data)
                if len(buffer_window) > 1:
                    buffer_window.pop(0)
                context = ''.join(buffer_window)
                if '>>' in data:
                    parsed = get_ai_response(context, chat_history, current_goal)
                    if parsed is None:
                        print("[INFO] AI response unavailable. Press Enter to retry or Ctrl+C to exit.")
                        input()
                        continue
                    reasoning = parsed.get('reasoning', '')
                    decision = parsed.get('decision', '')
                    game_input = parsed.get('game_input', '')
                    journal = parsed.get('journal', '')
                    print(f"\n\033[35m[AI reasoning]: {reasoning}\033[0m")
                    print(f"\033[32m[AI decision]: {decision}\033[0m")
                    print(f"\033[36m[AI input]: {game_input}\033[0m")
                    print(f"\033[34m[AI journal]: {journal}\033[0m")
                    log_file.write(f"AI reasoning: {reasoning}\n")
                    log_file.write(f"AI input: {game_input}\n")
                    log_file.write(f"AI journal: {journal}\n")
                    log_file.flush()
                    tn.write(((game_input if game_input else '') + '\n').encode('utf-8'))
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nGraceful shutdown requested. Closing connections...")
    finally:
        if tn and log_file:
            try:
                tn.write(b'score\n')
                score_output = tn.read_until(b'> ', timeout=5).decode('utf-8', errors='ignore')
                log_file.write(score_output)
                log_file.flush()
                print("[Shutdown] Sent 'score' and logged output.")
            except Exception as e:
                print(f"[Shutdown] Failed to send 'score' or log output: {e}")
        if tn:
            try:
                tn.close()
                print("Telnet connection closed.")
            except Exception:
                pass
        if log_file:
            try:
                log_file.close()
                print("Log file closed.")
            except Exception:
                pass

if __name__ == '__main__':
    main()
