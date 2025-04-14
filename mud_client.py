import telnetlib 
import requests
import time
import json
import os
import re
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

MUD_HOST = os.getenv('MUD_HOST')
MUD_PORT = int(os.getenv('MUD_PORT'))
username = os.getenv('USERNAME')
password = os.getenv('PASSWORD')

# OpenRouter API configuration
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_API_URL = os.getenv('OPENROUTER_API_URL')
OLLAMA_API_URL = os.getenv('OLLAMA_API_URL')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL')
PROVIDER = 'openrouter'

chat_history = []
score = ''
look = ''
base_system_prompt = """You are playing a live online MUD game. Respond ONLY with a JSON object containing 3 fields: 'reasoning' (your reasoning) and 'decision' (your conclusion about your next action) 'game_input' (the command to send). You are an EXPERT MUD player.\n\n
    <commands>
    Movement:	align enter exits follow go map meditate minimap mm recall rest scan sit sleep solitude stand tag visible wake walk where area \n
Config:	alias autoassist autocomplete autodef autoexit autogold autogrammar autogrip autohint autolist autoloot autopeace autosac autosplit background brief channels color compact description donate nofollow nomagic nosummon noyel noyell password prompt remove scroll sell submit target unlock untarget wimpy \n
Objects:	appraise bait barter bid brandish buy cancel close collect combine drink drop eat feed fill get give hire hold list lock market open pitch possession pour pull put quaff recite reel reload reshape retire retrieve return sacrifice take unpitch value wear wield zap \n
Communication:	application apply commend complaint deaf emote esay esays gossip greet gtell honor ignore order pmote pose pray quiet replay reply report say socials tell yell
Information	affects applicants areas changes commands compare consider credits equipment event examine help helpsearch history idea info inventory issues look news note oversee powers preview proficiency read score score2 show skills songs spells time timeconvert typo wealth weather whios who whois wizlist worth x \n
Combat:	cast channel chant commune confinement ecast flee focus kill murder sing \n
Misc:A	addquest allow bet bug check delete demote dice enlist gain gamble gift grip group hammer heal induct outfit pardon pay pet practice promote puke questor questor2 quit raise refuse rehearse release save surrender task train uninduct
</commands>"""
system_prompt = base_system_prompt + "\n\n" + score + "\n\n" + look

def update_system_prompt(base, score, look):
    global system_prompt
    system_prompt = base + "\n\n" + score + "\n\n" + look

def get_ai_response(prompt):
    # Insert or update system prompt at the top
    if not chat_history or chat_history[0].get('content') != system_prompt:
        chat_history.insert(0, {"role": "system", "content": system_prompt})
    else:
        chat_history[0]["content"] = system_prompt
    # Append user message
    chat_history.append({"role": "system", "content": prompt})

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
            payload["model"] = "llama3.1"
            payload['format'] = {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string"
                    },
                    "game_input": {
                        "type": "string"
                    }
                },
                "required": [
                    "thinking",
                    "input"
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

        try:
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not match:
                raise ValueError("No JSON object found in response.")
            
            outer = json.loads(match.group(0))  # Step 1: parse outer object
            
            if PROVIDER == 'ollama':
                # Step 2: parse stringified JSON in content
                inner_content = outer.get("message", {}).get("content", "")
                content = inner_content.strip()
                parsed = json.loads(content)  # Step 3: final object
            else:
                # OpenRouter path
                content = outer.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                parsed = json.loads(content)

            chat_history.append({"role": "assistant", "content": content})
            return parsed  # Already parsed dict

        except json.JSONDecodeError as e:
            print("JSON decode error:", e)
            raise

def main():
    global last_stats_check, waiting_for_stats
    tn = None
    log_file = None
    try:
        tn = telnetlib.Telnet(MUD_HOST, MUD_PORT)
        print(f"Connected to {MUD_HOST}:{MUD_PORT}")

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        log_filename = f'{username}_mud_log_{timestamp}.txt'
        log_file = open(log_filename, 'a', encoding='utf-8')
        buffer_window = []
        while True:
            data = tn.read_very_eager().decode('utf-8', errors='ignore')
            
            if data:
                print(data, end='')
                # hard coding login for abandonedrealms.com
                if 'By what name do you wish to be known' in data:
                    tn.write(username.encode('utf-8') + b'\n')
                    print(f"Sending username: {username}\n")
                    continue
                if 'Password:' in data:
                    tn.write(password.encode('utf-8') + b'\n')
                    print(f"Sending password: ***\n")
                    continue
                if 'That character is already playing.' in data:
                    tn.write(b'y\n')
                    continue
                # get score
                tn.write(b'score\n')
                time.sleep(1)
                global score
                score = tn.read_very_eager().decode('utf-8', errors='ignore')
                
                tn.write(b'look\n')
                time.sleep(1)
                #get look
                global look
                look = tn.read_very_eager().decode('utf-8', errors='ignore')
                update_system_prompt(base_system_prompt, score, look)

                log_file.write(data)
                log_file.flush()       
                buffer_window.append(data)
                if len(buffer_window) > 1:
                    buffer_window.pop(0)
                    
                context = ''.join(buffer_window)
                if '\n' in data:
                    parsed = get_ai_response(context)
                    reasoning = parsed.get('reasoning', '')
                    decision = parsed.get('decision', '')
                    game_input = parsed.get('game_input', '')
                    print(f"\n\033[35m[AI reasoning]: {reasoning}\033[0m")  
                    print(f"\033[32m[AI decision]: {decision}\033[0m")     
                    print(f"\033[36m[AI input]: {game_input}\033[0m")      
                    log_file.write(f"AI reasoning: {reasoning}\n")
                    
                    log_file.write(f"AI input: {game_input}\n")
                    log_file.flush()
                    # Always send at least a newline to avoid hanging
                    tn.write(((game_input if game_input else '') + '\n').encode('utf-8'))
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nGraceful shutdown requested. Closing connections...")
    finally:
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
