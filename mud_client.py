import telnetlib 
import requests
import time
import json
import os
import re
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Load current goal from journal
JOURNAL_PATH = 'mud_journal.json'
def load_current_goal():
    try:
        with open(JOURNAL_PATH, 'r', encoding='utf-8') as jf:
            journal = json.load(jf)
            return journal.get('goal', '')
    except Exception:
        return ''

current_goal = load_current_goal()

MUD_HOST = os.getenv('MUD_HOST')
MUD_PORT = int(os.getenv('MUD_PORT'))
USERNAME = os.getenv('MUD_USERNAME')
PASSWORD = os.getenv('MUD_PASSWORD')

# OpenRouter API configuration
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_API_URL = os.getenv('OPENROUTER_API_URL')
OLLAMA_API_URL = os.getenv('OLLAMA_API_URL')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL')
PROVIDER = os.getenv('PROVIDER')

chat_history = []
score = ''
look = ''
base_system_prompt = """You are playing a live online MUD game. Respond ONLY with a JSON object containing 3 fields: 'reasoning' (your reasoning) and 'decision' (your conclusion about your next action) 'game_input' (the command to send). You are an EXPERT MUD player.\n\n
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
<tip>
you can send multiple commands at the same time by separating them each with a pipe | 
</tip>
"""
system_prompt = base_system_prompt + "\n\n" + f'<goal>:\n{current_goal}\n</goal>'

def get_ai_response(prompt):
    # Insert or update system prompt at the top
    if not chat_history or chat_history[0].get('role') != "system":
        chat_history.insert(0, {"role": "system", "content": system_prompt})
    else:
        chat_history[0]["content"] = system_prompt
    # Append user message
    chat_history.append({"role": "user", "content": prompt})

    # Write prompt/context to ai_state.json for web UI
    try:
        with open("ai_state.json", "w", encoding="utf-8") as f:
            json.dump({
                "prompt": prompt,
                "chat_history": chat_history,
                "ai_response": None
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Could not write ai_state.json: {e}")

    max_retries = 5
    backoff = 2
    retries = 0
    while True:
        payload = {
            "messages": chat_history
        }
        headers = {
            "Content-Type": "application/json",
            # Optionally add Referer and X-Title headers here
        }
        if PROVIDER == 'openrouter':
            # OpenRouter API request
            payload["model"] = OPENROUTER_MODEL
            payload["type"] = "json_object"
            payload["temperature"] = 1.1
            headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
        elif PROVIDER == 'ollama':
            # Ollama API request
            payload["model"] = "gemma3"
            payload['format'] = {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string"
                    },
                    "decision": {
                        "type": "string"
                    },
                    "game_input": {
                        "type": "string"
                    }
                },
                "required": [
                    "reasoning",
                    "decision",
                    "game_input"
                ]
            }
            payload['options'] = {
                "temperature": 1.0
            }
            payload['stream'] = False

        url = OLLAMA_API_URL if PROVIDER == 'ollama' else OPENROUTER_API_URL
        response = requests.post(
            url=url,
            headers=headers,
            data=json.dumps(payload)
        )
        response.raise_for_status()

        response_text = response.text.strip()

        # Try to parse as JSON and check for 429/quota error
        is_quota_error = False
        try:
            resp_json = json.loads(response_text)
            if 'error' in resp_json:
                err = resp_json['error']
                code = err.get('code')
                status = err.get('status', '')
                if code == 429 or status == 'RESOURCE_EXHAUSTED':
                    is_quota_error = True
                # Some providers (like Google) may wrap the real error in metadata['raw']
                if 'metadata' in err and 'raw' in err['metadata']:
                    try:
                        raw_err = json.loads(err['metadata']['raw'])
                        raw_code = raw_err.get('error', {}).get('code')
                        raw_status = raw_err.get('error', {}).get('status', '')
                        if raw_code == 429 or raw_status == 'RESOURCE_EXHAUSTED':
                            is_quota_error = True
                    except Exception:
                        pass
        except Exception:
            pass
        if is_quota_error:
            if retries < max_retries:
                print(f"[ERROR] Quota exceeded or rate limited (429). Retrying in {backoff} seconds...")
                time.sleep(backoff)
                retries += 1
                backoff *= 2
                continue
            else:
                print("[ERROR] Maximum retries reached. Please check your API quota or try again later.")
                return None

        try:
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not match:
                raise ValueError("No JSON object found in response.")
            
            outer = json.loads(match.group(0))  # Step 1: parse outer object
            
            if PROVIDER == 'ollama':
                # Step 2: parse stringified JSON in content
                inner_content = outer.get("message", {}).get("content", "")
                content = inner_content.strip()
                # Remove markdown code block if present
                if content.startswith('```'):
                    lines = content.splitlines()
                    # Remove first and last line (code block markers)
                    if lines[0].startswith('```') and lines[-1].startswith('```'):
                        content = '\n'.join(lines[1:-1]).strip()
                parsed = json.loads(content)  # Step 3: final object
            else:
                # OpenRouter path
                content = outer.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                # Remove markdown code block if present
                if content.startswith('```'):
                    lines = content.splitlines()
                    # Remove first and last line (code block markers)
                    if lines[0].startswith('```') and lines[-1].startswith('```'):
                        content = '\n'.join(lines[1:-1]).strip()
                parsed = json.loads(content)

            chat_history.append({"role": "assistant", "content": content})
            # Write AI response to ai_state.json for web UI
            try:
                with open("ai_state.json", "w", encoding="utf-8") as f:
                    json.dump({
                        "prompt": prompt,
                        "chat_history": chat_history,
                        "ai_response": parsed
                    }, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[WARN] Could not write ai_state.json: {e}")
            return parsed  # Already parsed dict

        except json.JSONDecodeError as e:
            print("JSON decode error:", e)
            raise

def send_command_and_get_response(tn, command):
    tn.write(command.encode('utf-8') + b'\n')
    response = tn.read_until(b'>> ', timeout=5)
    return response.decode('utf-8', errors='ignore')

def main():
    global last_stats_check, waiting_for_stats
    tn = None
    log_file = None
    try:
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
            'OPENROUTER_MODEL': OPENROUTER_MODEL,
            'PROVIDER': PROVIDER
        }
        log_file.write("# Environment variables (non-secret):\n")
        for k, v in env_vars_to_log.items():
            log_file.write(f"{k}={v}\n")
        log_file.write("\n")
        log_file.flush()

        buffer_window = []
        logged_in = False  # Add this flag
        one_time_score_sent = False
        while True:
            # Send 'score' once after login when first prompt appears
            if not logged_in and not one_time_score_sent:
                tn.write(b'score\n')
                one_time_score_sent = True
                time.sleep(1)  # Wait for a moment to let the score be processed
            data = tn.read_very_eager().decode('utf-8', errors='ignore')
            # data = tn.read_until(b'> ', timeout=5).decode('utf-8', errors='ignore')
            
            # Auto-continue if prompt is present
            if '[Hit Return to continue]' in data:
                tn.write(b'\n')
                continue
            
            if data:
                print(data, end='')
                # hard coding login for abandonedrealms.com
                if 'By what name do you wish to be known' in data or 'By what name do you wish to be remembered' in data:
                    tn.write(USERNAME.encode('utf-8') + b'\n')
                    print(f"Sending username: {USERNAME}\n")
                    continue
                if 'Password:' in data:
                    tn.write(PASSWORD.encode('utf-8') + b'\n')
                    print(f"Sending password: ***\n")
                    logged_in = True
                    continue
                # if 'That character is already playing.' in data:
                #     tn.write(b'y\n')
                #     continue
                

                log_file.write(data)
                log_file.flush()       
                buffer_window.append(data)
                if len(buffer_window) > 1:
                    buffer_window.pop(0)
                context = ''.join(buffer_window)
                if '>>' in data:
                    parsed = get_ai_response(context)
                    if parsed is None:
                        print("[INFO] AI response unavailable due to quota/rate limit. Press Enter to retry or Ctrl+C to exit.")
                        input()
                        continue
                    reasoning = parsed.get('reasoning', '')
                    decision = parsed.get('decision', '')
                    game_input = parsed.get('game_input', '')
                    print(f"\n\033[35m[AI reasoning]: {reasoning}\033[0m")
                    print(f"\033[32m[AI decision]: {decision}\033[0m")
                    print(f"\033[36m[AI input]: {game_input}\033[0m")
                    log_file.write(f"AI reasoning: {reasoning}\n")
                    log_file.write(f"AI input: {game_input}\n")
                    log_file.flush()
                    tn.write(((game_input if game_input else '') + '\n').encode('utf-8'))
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nGraceful shutdown requested. Closing connections...")
    finally:
        # On shutdown, try to send 'score' and log the output
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
