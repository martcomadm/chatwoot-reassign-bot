import os
import time
import traceback
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CHATWOOT_BASE_URL", "").rstrip("/")
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "").strip()
TOKEN = os.getenv("CHATWOOT_API_TOKEN", "").strip()

INBOX_ID = int(os.getenv("TARGET_INBOX_ID", "0"))
LABEL = os.getenv("ASSIGNED_LABEL", "asignado").strip().lower()

WAIT_TIME = int(os.getenv("REASSIGN_AFTER_SECONDS", "180"))
INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

AGENTS = [int(x.strip()) for x in os.getenv("AGENT_IDS", "").split(",") if x.strip()]

ADMIN_AGENT_ID = int(os.getenv("ADMIN_AGENT_ID", "0"))
PREDICTIVE_LABEL = os.getenv("PREDICTIVE_LABEL", "predictivo").strip().lower()
OLD_CHAT_SECONDS = int(os.getenv("OLD_CHAT_HOURS", "48")) * 3600

HEADERS = {
    "api_access_token": TOKEN,
    "Accept": "application/json",
    "Content-Type": "application/json",
}


# ⏰ HORARIO CDMX
def is_within_schedule():
    tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    return 7 <= now.hour < 21


def validate_config():
    if not BASE_URL or not ACCOUNT_ID or not TOKEN:
        raise Exception("Faltan credenciales")
    if INBOX_ID <= 0:
        raise Exception("INBOX inválido")
    if not AGENTS:
        raise Exception("Faltan agentes")
    if ADMIN_AGENT_ID <= 0:
        raise Exception("Falta ADMIN_AGENT_ID")


def get_conversations():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json().get("data", {}).get("payload", [])


def get_online_agents():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/agents"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

    agents_data = data if isinstance(data, list) else data.get("payload", [])

    return [
        a["id"]
        for a in agents_data
        if isinstance(a, dict)
        and a.get("id") in AGENTS
        and str(a.get("availability_status", "")).lower() == "online"
    ]


def get_labels(cid):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/labels"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return [str(x).lower() for x in response.json().get("payload", [])]


def assign_conversation(cid, agent_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/assignments"
    requests.post(url, headers=HEADERS, json={"assignee_id": agent_id}, timeout=30).raise_for_status()


def add_label(cid, label):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30).raise_for_status()


def add_contact_label(contact_id, label):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30).raise_for_status()


def update_last_move(cid, ts):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/custom_attributes"
    requests.post(
        url,
        headers=HEADERS,
        json={"custom_attributes": {"last_move": ts}},
        timeout=30,
    ).raise_for_status()


def get_next_agent(current, online_agents):
    if not online_agents:
        return None
    if current in online_agents:
        i = online_agents.index(current)
        return online_agents[(i + 1) % len(online_agents)]
    return online_agents[0]


# 🔥 CHAT VIEJO (CORREGIDO CON FILTRO INBOX)
def process_old_assigned_conversation(c):
    cid = c["id"]

    # 🔴 CRÍTICO: solo inbox 6
    if c.get("inbox_id") != INBOX_ID:
        return False

    now = int(time.time())

    created_at = int(c.get("created_at", 0) or 0)
    if not created_at:
        return False

    age = now - created_at

    print(f"[DEBUG {cid}] age_h={round(age/3600,1)}", flush=True)

    if age < OLD_CHAT_SECONDS:
        return False

    labels = [l.lower() for l in get_labels(cid)]

    print(f"[DEBUG {cid}] labels={labels}", flush=True)

    # 🔥 validación FINAL
    if LABEL not in labels:
        return False

    if len(labels) != 1:
        return False

    if PREDICTIVE_LABEL in labels:
        return False

    meta = c.get("meta", {}) or {}
    contact_id = (meta.get("sender") or {}).get("id")

    print(f"[OLD {cid}] → ADMIN", flush=True)

    assign_conversation(cid, ADMIN_AGENT_ID)
    add_label(cid, PREDICTIVE_LABEL)

    if contact_id:
        add_contact_label(contact_id, PREDICTIVE_LABEL)

    return True


# 🔁 FLUJO NORMAL
def process_conversation(c, online_agents):
    cid = c["id"]
    inbox_id = c.get("inbox_id")

    # 🔴 SOLO inbox 6
    if inbox_id != INBOX_ID:
        return

    now = int(time.time())

    status = str(c.get("status", "")).lower()
    created_at = int(c.get("created_at", 0) or 0)

    if status in {"resolved", "snoozed"}:
        return
    if not created_at:
        return

    age = now - created_at

    meta = c.get("meta", {}) or {}
    assignee = (meta.get("assignee") or {}).get("id")

    # ❌ no tocar admin
    if assignee == ADMIN_AGENT_ID:
        return

    attrs = c.get("custom_attributes") or {}
    last_move = int(attrs.get("last_move", 0) or 0)

    labels = get_labels(cid)

    if age < WAIT_TIME:
        return

    if last_move and (now - last_move < WAIT_TIME):
        return

    next_agent = get_next_agent(assignee, online_agents)

    if not next_agent or next_agent == assignee:
        return

    print(f"[MOVE {cid}] {assignee} → {next_agent}", flush=True)

    assign_conversation(cid, next_agent)
    update_last_move(cid, now)


def run():
    validate_config()

    print("🔥 BOT FINAL CORREGIDO ACTIVO", flush=True)

    while True:
        try:
            if not is_within_schedule():
                print("⏰ fuera de horario", flush=True)
                time.sleep(INTERVAL)
                continue

            conversations = get_conversations()
            online_agents = get_online_agents()

            if not online_agents:
                print("⚠️ sin agentes online", flush=True)
                time.sleep(INTERVAL)
                continue

            for c in conversations:
                was_old = process_old_assigned_conversation(c)

                if was_old:
                    continue

                process_conversation(c, online_agents)

        except Exception as e:
            print("ERROR:", e, flush=True)
            traceback.print_exc()

        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
