import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

print("INICIANDO BOT...", flush=True)

BASE_URL = os.getenv("CHATWOOT_BASE_URL")
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
TOKEN = os.getenv("CHATWOOT_API_TOKEN")

INBOX_ID = int(os.getenv("TARGET_INBOX_ID"))
LABEL = os.getenv("ASSIGNED_LABEL")

WAIT_TIME = int(os.getenv("REASSIGN_AFTER_SECONDS"))
INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS"))

AGENTS = [int(x) for x in os.getenv("AGENT_IDS").split(",")]

HEADERS = {
    "api_access_token": TOKEN,
    "Content-Type": "application/json"
}

def get_conversations():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    r = requests.get(url, headers=HEADERS)
    return r.json()["data"]["payload"]

def get_labels(cid):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/labels"
    r = requests.get(url, headers=HEADERS)
    return r.json()["payload"]

def assign(cid, agent):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/assignments"
    requests.post(url, headers=HEADERS, json={"assignee_id": agent})

def update_meta(cid, data):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/custom_attributes"
    requests.post(url, headers=HEADERS, json={"custom_attributes": data})

def run():
    print("🔥 Bot activo", flush=True)

    while True:
        try:
            conversations = get_conversations()

            for c in conversations:
                if c["inbox_id"] != INBOX_ID:
                    continue

                cid = c["id"]
                created = c["created_at"]
                now = int(time.time())

                if now - created < WAIT_TIME:
                    continue

                labels = get_labels(cid)

                if LABEL in labels:
                    continue

                meta = c.get("meta", {})
                assignee = meta.get("assignee", {}).get("id")

                attrs = c.get("custom_attributes") or {}
                last_move = attrs.get("last_move", 0)

                if now - last_move < WAIT_TIME:
                    continue

                next_agent = AGENTS[0]

                if assignee in AGENTS:
                    i = AGENTS.index(assignee)
                    next_agent = AGENTS[(i + 1) % len(AGENTS)]

                print(f"➡️ Conversación {cid} → agente {next_agent}")

                assign(cid, next_agent)

                update_meta(cid, {
                    "last_move": now
                })

        except Exception as e:
            print("ERROR:", e, flush=True)

        time.sleep(INTERVAL)
        
run()
