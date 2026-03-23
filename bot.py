import os
import time
import traceback
import requests
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

HEADERS = {
    "api_access_token": TOKEN,
    "Accept": "application/json",
    "Content-Type": "application/json",
}


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


def get_online_agents():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/agents"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    payload = response.json().get("payload", [])
    online_agents = []

    for agent in payload:
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
    return response


def update_custom_attributes(conversation_id: int, custom_attributes: dict):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/custom_attributes"
    response = requests.post(
        url,
        headers=HEADERS,
        json={"custom_attributes": custom_attributes},
        timeout=30,
    )
    response.raise_for_status()
    return response


def get_next_agent(current_assignee):
    online_agents = get_online_agents()

    if not online_agents:
        print("⚠️ No hay agentes online disponibles", flush=True)
        return None

    if current_assignee in online_agents:
        current_index = online_agents.index(current_assignee)
        next_index = (current_index + 1) % len(online_agents)
        return online_agents[next_index]

    return online_agents[0]


def should_skip_conversation(conversation: dict, now_ts: int):
    conversation_id = conversation.get("id")
    inbox_id = conversation.get("inbox_id")
    status = str(conversation.get("status", "")).lower()
    created_at = int(conversation.get("created_at", 0) or 0)

    if inbox_id != INBOX_ID:
        return True, f"[CID {conversation_id}] omitida: inbox distinto"

    if status in {"resolved", "snoozed"}:
        return True, f"[CID {conversation_id}] omitida: status {status}"

    if created_at == 0:
        return True, f"[CID {conversation_id}] omitida: sin created_at"

    age = now_ts - created_at
    if age < WAIT_TIME:
        return True, f"[CID {conversation_id}] omitida: aún no cumple {WAIT_TIME}s"

    return False, ""


def process_conversation(conversation: dict):
    now_ts = int(time.time())
    conversation_id = conversation["id"]
    meta = conversation.get("meta", {}) or {}
    assignee = (meta.get("assignee") or {}).get("id")
    attrs = conversation.get("custom_attributes") or {}
    last_move = int(attrs.get("last_move", 0) or 0)

    skip, reason = should_skip_conversation(conversation, now_ts)
    if skip:
        print(reason, flush=True)
        return

    labels = get_labels(conversation_id)
    if LABEL in labels:
        print(f"[CID {conversation_id}] omitida: ya tiene etiqueta '{LABEL}'", flush=True)
        return

    if last_move and (now_ts - last_move < WAIT_TIME):
        print(f"[CID {conversation_id}] omitida: reasignada hace poco", flush=True)
        return

    next_agent = get_next_agent(assignee)
    if not next_agent:
        print(f"[CID {conversation_id}] omitida: sin agentes online", flush=True)
        return

    if assignee == next_agent:
        print(f"[CID {conversation_id}] omitida: el siguiente agente online sigue siendo el mismo ({assignee})", flush=True)
        return

    print(
        f"[CID {conversation_id}] reasignando de agente {assignee} a agente {next_agent}",
        flush=True
    )

    assign_conversation(conversation_id, next_agent)
    update_custom_attributes(
        conversation_id,
        {
            "last_move": now_ts
        }
    )

    print(f"[CID {conversation_id}] reasignada correctamente", flush=True)


def run():
    validate_config()

    print("INICIANDO BOT...", flush=True)
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
            for conversation in conversations:
                process_conversation(conversation)
        except Exception as e:
            print(f"ERROR GENERAL: {e}", flush=True)
            traceback.print_exc()

        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
