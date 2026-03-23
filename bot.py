import os
import time
import traceback
import requests
from dotenv import load_dotenv

load_dotenv()

print("INICIANDO BOT...", flush=True)

BASE_URL = os.getenv("CHATWOOT_BASE_URL")
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
TOKEN = os.getenv("CHATWOOT_API_TOKEN")

INBOX_ID = int(os.getenv("TARGET_INBOX_ID", "0"))
LABEL = os.getenv("ASSIGNED_LABEL", "asignado").strip().lower()

WAIT_TIME = int(os.getenv("REASSIGN_AFTER_SECONDS", "180"))
INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

AGENTS = [int(x.strip()) for x in os.getenv("AGENT_IDS", "").split(",") if x.strip()]

HEADERS = {
    "api_access_token": TOKEN,
    "Accept": "application/json",
    "Content-Type": "application/json",
}

if not BASE_URL:
    raise Exception("Falta CHATWOOT_BASE_URL")
if not ACCOUNT_ID:
    raise Exception("Falta CHATWOOT_ACCOUNT_ID")
if not TOKEN:
    raise Exception("Falta CHATWOOT_API_TOKEN")
if INBOX_ID == 0:
    raise Exception("Falta TARGET_INBOX_ID")
if not AGENTS:
    raise Exception("Falta AGENT_IDS")


def get_conversations():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    r = requests.get(url, headers=HEADERS, timeout=30)
    print(f"GET {url} STATUS {r.status_code}", flush=True)
    r.raise_for_status()
    return r.json()["data"]["payload"]


def get_labels(cid):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/labels"
    r = requests.get(url, headers=HEADERS, timeout=30)
    print(f"GET {url} STATUS {r.status_code}", flush=True)
    r.raise_for_status()
    payload = r.json().get("payload", [])
    return [str(x).strip().lower() for x in payload]


def assign(cid, agent):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/assignments"
    r = requests.post(url, headers=HEADERS, json={"assignee_id": agent}, timeout=30)
    print(f"POST {url} STATUS {r.status_code} -> agente {agent}", flush=True)
    print(f"RESPUESTA ASSIGN: {r.text[:500]}", flush=True)
    r.raise_for_status()


def update_meta(cid, data):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/custom_attributes"
    r = requests.post(url, headers=HEADERS, json={"custom_attributes": data}, timeout=30)
    print(f"POST {url} STATUS {r.status_code}", flush=True)
    print(f"RESPUESTA META: {r.text[:500]}", flush=True)
    r.raise_for_status()


def run():
    print("🔥 Bot activo", flush=True)
    print(f"BASE_URL={BASE_URL}", flush=True)
    print(f"ACCOUNT_ID={ACCOUNT_ID}", flush=True)
    print(f"INBOX_ID={INBOX_ID}", flush=True)
    print(f"LABEL={LABEL}", flush=True)
    print(f"WAIT_TIME={WAIT_TIME}", flush=True)
    print(f"INTERVAL={INTERVAL}", flush=True)
    print(f"AGENTS={AGENTS}", flush=True)

    while True:
        try:
            conversations = get_conversations()
            print(f"Conversaciones recibidas: {len(conversations)}", flush=True)

            for c in conversations:
                cid = c["id"]
                inbox_id = c.get("inbox_id")
                created = int(c.get("created_at", 0))
                now = int(time.time())
                age = now - created

                meta = c.get("meta", {}) or {}
                assignee = (meta.get("assignee") or {}).get("id")
                status = c.get("status")
                attrs = c.get("custom_attributes") or {}
                last_move = int(attrs.get("last_move", 0) or 0)

                print(
                    f"[CID {cid}] inbox={inbox_id} status={status} assignee={assignee} age={age}s last_move={last_move}",
                    flush=True
                )

                if inbox_id != INBOX_ID:
                    print(f"[CID {cid}] omitida: inbox distinto", flush=True)
                    continue

                if age < WAIT_TIME:
                    print(f"[CID {cid}] omitida: aún no cumple {WAIT_TIME}s", flush=True)
                    continue

                labels = get_labels(cid)
                print(f"[CID {cid}] labels={labels}", flush=True)

                if LABEL in labels:
                    print(f"[CID {cid}] omitida: ya tiene etiqueta '{LABEL}'", flush=True)
                    continue

                if last_move and (now - last_move < WAIT_TIME):
                    print(f"[CID {cid}] omitida: reasignada hace poco", flush=True)
                    continue

                next_agent = AGENTS[0]
                if assignee in AGENTS:
                    i = AGENTS.index(assignee)
                    next_agent = AGENTS[(i + 1) % len(AGENTS)]

                print(f"[CID {cid}] REASIGNANDO -> agente {next_agent}", flush=True)
                assign(cid, next_agent)
                update_meta(cid, {"last_move": now})

        except Exception as e:
            print("ERROR:", e, flush=True)
            traceback.print_exc()

        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
