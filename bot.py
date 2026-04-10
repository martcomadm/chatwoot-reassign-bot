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

# 🔥 NUEVAS CONFIG
ADMIN_AGENT_ID = int(os.getenv("ADMIN_AGENT_ID", "0"))
PREDICTIVE_LABEL = os.getenv("PREDICTIVE_LABEL", "predictivo").strip().lower()
OLD_CHAT_SECONDS = int(os.getenv("OLD_CHAT_HOURS", "48")) * 3600

HEADERS = {
    "api_access_token": TOKEN,
    "Accept": "application/json",
    "Content-Type": "application/json",
}


# 🔥 HORARIO CDMX
def is_within_schedule():
    tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    return 7 <= now.hour < 21


def validate_config():
    if not BASE_URL:
        raise Exception("Falta CHATWOOT_BASE_URL")
    if not ACCOUNT_ID:
        raise Exception("Falta CHATWOOT_ACCOUNT_ID")
    if not TOKEN:
        raise Exception("Falta CHATWOOT_API_TOKEN")
    if INBOX_ID <= 0:
        raise Exception("TARGET_INBOX_ID inválido")
    if not AGENTS:
        raise Exception("Falta AGENT_IDS")
    if ADMIN_AGENT_ID <= 0:
        raise Exception("Falta ADMIN_AGENT_ID")


def get_conversations():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("data", {}).get("payload", [])


def get_labels(conversation_id: int):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json().get("payload", [])
    return [str(x).strip().lower() for x in payload]


def add_label(conversation_id: int, label: str):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
    response = requests.post(
        url,
        headers=HEADERS,
        json={"labels": [label]},
        timeout=30,
    )
    response.raise_for_status()


def add_contact_label(contact_id: int, label: str):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}/labels"
    response = requests.post(
        url,
        headers=HEADERS,
        json={"labels": [label]},
        timeout=30,
    )
    response.raise_for_status()


def get_online_agents():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/agents"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, list):
        agents_data = data
    elif isinstance(data, dict):
        agents_data = data.get("payload", [])
    else:
        agents_data = []

    online_agents = []
    for agent in agents_data:
        if not isinstance(agent, dict):
            continue

        agent_id = agent.get("id")
        availability = str(agent.get("availability_status", "")).lower()

        if agent_id in AGENTS and availability == "online":
            online_agents.append(agent_id)

    return online_agents


def assign_conversation(conversation_id: int, agent_id: int):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/assignments"
    response = requests.post(
        url,
        headers=HEADERS,
        json={"assignee_id": agent_id},
        timeout=30,
    )
    response.raise_for_status()


def update_custom_attributes(conversation_id: int, custom_attributes: dict):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/custom_attributes"
    response = requests.post(
        url,
        headers=HEADERS,
        json={"custom_attributes": custom_attributes},
        timeout=30,
    )
    response.raise_for_status()


def get_next_agent(current_assignee):
    online_agents = get_online_agents()

    if not online_agents:
        print("⚠️ No hay agentes online disponibles", flush=True)
        return None

    if current_assignee in online_agents:
        index = online_agents.index(current_assignee)
        return online_agents[(index + 1) % len(online_agents)]

    return online_agents[0]


# 🔥 NUEVA LÓGICA: chats viejos asignados
def process_old_assigned_conversation(conversation: dict):
    now_ts = int(time.time())
    cid = conversation["id"]

    created_at = int(conversation.get("created_at", 0) or 0)
    age = now_ts - created_at

    if created_at == 0 or age < OLD_CHAT_SECONDS:
        return

    labels = get_labels(cid)

    # solo "asignado"
    if labels != [LABEL]:
        return

    # evitar reprocesar
    if PREDICTIVE_LABEL in labels:
        return

    meta = conversation.get("meta", {}) or {}
    contact = meta.get("sender") or {}
    contact_id = contact.get("id")

    print(f"[CID {cid}] chat viejo → ADMIN", flush=True)

    assign_conversation(cid, ADMIN_AGENT_ID)
    add_label(cid, PREDICTIVE_LABEL)

    if contact_id:
        add_contact_label(contact_id, PREDICTIVE_LABEL)

    print(f"[CID {cid}] enviado a ADMIN + predictivo ✔", flush=True)


def should_skip_conversation(conversation: dict, now_ts: int):
    cid = conversation.get("id")
    inbox_id = conversation.get("inbox_id")
    status = str(conversation.get("status", "")).lower()
    created_at = int(conversation.get("created_at", 0) or 0)

    if inbox_id != INBOX_ID:
        return True, f"[CID {cid}] omitida: inbox distinto"

    if status in {"resolved", "snoozed"}:
        return True, f"[CID {cid}] omitida: status {status}"

    if created_at == 0:
        return True, f"[CID {cid}] omitida: sin created_at"

    age = now_ts - created_at
    if age < WAIT_TIME:
        return True, f"[CID {cid}] omitida: aún no cumple {WAIT_TIME}s"

    return False, ""


def process_conversation(conversation: dict):
    now_ts = int(time.time())
    cid = conversation["id"]

    meta = conversation.get("meta", {}) or {}
    assignee = (meta.get("assignee") or {}).get("id")

    attrs = conversation.get("custom_attributes") or {}
    last_move = int(attrs.get("last_move", 0) or 0)

    skip, reason = should_skip_conversation(conversation, now_ts)
    if skip:
        print(reason, flush=True)
        return

    labels = get_labels(cid)

    if LABEL in labels:
        return

    if last_move and (now_ts - last_move < WAIT_TIME):
        return

    next_agent = get_next_agent(assignee)

    if not next_agent or next_agent == assignee:
        return

    print(f"[CID {cid}] moviendo {assignee} → {next_agent}", flush=True)

    assign_conversation(cid, next_agent)
    update_custom_attributes(cid, {"last_move": now_ts})

    print(f"[CID {cid}] reasignada ✔", flush=True)


def run():
    validate_config()

    print("🔥 BOT INICIADO", flush=True)

    while True:
        try:
            if not is_within_schedule():
                print("⏰ Fuera de horario...", flush=True)
                time.sleep(INTERVAL)
                continue

            conversations = get_conversations()

            for c in conversations:
                # 🔥 prioridad alta
                process_old_assigned_conversation(c)

                # 🔁 flujo normal
                process_conversation(c)

        except Exception as e:
            print("ERROR:", e, flush=True)
            traceback.print_exc()

        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
