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


def is_within_schedule():
    tz = ZoneInfo("America/Mexico_City")
    now = datetime.now(tz)
    print(f"⏰ Hora actual: {now}", flush=True)
    return 7 <= now.hour < 21


def validate_config():
    print("🔧 Validando configuración...", flush=True)
    print(f"BASE_URL={BASE_URL}", flush=True)
    print(f"ACCOUNT_ID={ACCOUNT_ID}", flush=True)
    print(f"INBOX_ID={INBOX_ID}", flush=True)
    print(f"AGENTS={AGENTS}", flush=True)
    print(f"ADMIN_AGENT_ID={ADMIN_AGENT_ID}", flush=True)


def get_conversations():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations?status=open&inbox_id={INBOX_ID}"
    print(f"🌐 GET {url}", flush=True)

    response = requests.get(url, headers=HEADERS, timeout=30)
    print(f"📡 Status: {response.status_code}", flush=True)

    response.raise_for_status()
    data = response.json()

    payload = data.get("data", {}).get("payload", [])

    print(f"📥 Conversaciones recibidas: {len(payload)}", flush=True)

    return payload


def get_online_agents():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/agents"
    print(f"🌐 GET {url}", flush=True)

    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    data = response.json()
    agents_data = data if isinstance(data, list) else data.get("payload", [])

    online = [
        a["id"]
        for a in agents_data
        if isinstance(a, dict)
        and a.get("id") in AGENTS
        and str(a.get("availability_status", "")).lower() == "online"
    ]

    print(f"👥 Agentes online: {online}", flush=True)

    return online


def get_labels(cid):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/labels"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    labels = [str(x).lower() for x in response.json().get("payload", [])]
    print(f"[LABELS {cid}] {labels}", flush=True)
    return labels


def assign_conversation(cid, agent_id):
    print(f"➡️ Asignando {cid} a {agent_id}", flush=True)
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/assignments"
    requests.post(url, headers=HEADERS, json={"assignee_id": agent_id}, timeout=30).raise_for_status()


def add_label(cid, label):
    print(f"🏷️ Agregando label '{label}' a {cid}", flush=True)
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{cid}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30).raise_for_status()


def add_contact_label(contact_id, label):
    print(f"👤🏷️ Label '{label}' a contacto {contact_id}", flush=True)
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30).raise_for_status()


def update_last_move(cid, ts):
    print(f"🕓 Actualizando last_move de {cid}", flush=True)
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


def process_old_assigned_conversation(c):
    cid = c["id"]

    print(f"[OLD-CHECK {cid}] evaluando...", flush=True)

    if c.get("inbox_id") != INBOX_ID:
        print(f"[OLD {cid}] ❌ inbox distinto", flush=True)
        return False

    now = int(time.time())
    created_at = int(c.get("created_at", 0) or 0)

    if not created_at:
        print(f"[OLD {cid}] ❌ sin created_at", flush=True)
        return False

    age = now - created_at
    print(f"[OLD {cid}] edad_h={round(age/3600,2)}", flush=True)

    if age < OLD_CHAT_SECONDS:
        print(f"[OLD {cid}] ❌ menor a 48h", flush=True)
        return False

    labels = get_labels(cid)

    if LABEL not in labels:
        print(f"[OLD {cid}] ❌ no tiene 'asignado'", flush=True)
        return False

    if LABEL not in labels:
        print(f"[OLD {cid}] ❌ tiene más labels", flush=True)
        return False

    if PREDICTIVE_LABEL in labels:
        print(f"[OLD {cid}] ❌ ya es predictivo", flush=True)
        return False

    meta = c.get("meta", {}) or {}
    contact_id = (meta.get("sender") or {}).get("id")

    print(f"[OLD {cid}] ✅ ENVIANDO A ADMIN", flush=True)

    assign_conversation(cid, ADMIN_AGENT_ID)
    add_label(cid, PREDICTIVE_LABEL)

    if contact_id:
        add_contact_label(contact_id, PREDICTIVE_LABEL)

    return True


def process_conversation(c, online_agents):
    cid = c["id"]

    print(f"[FLOW {cid}] evaluando...", flush=True)

    if c.get("inbox_id") != INBOX_ID:
        print(f"[FLOW {cid}] ❌ inbox distinto", flush=True)
        return

    now = int(time.time())

    created_at = int(c.get("created_at", 0) or 0)
    if not created_at:
        print(f"[FLOW {cid}] ❌ sin created_at", flush=True)
        return

    age = now - created_at

    labels = get_labels(cid)

    if LABEL in labels:
        print(f"[FLOW {cid}] 🔒 tiene 'asignado', no se mueve", flush=True)
        return

    if age < WAIT_TIME:
        print(f"[FLOW {cid}] ⏳ muy nuevo", flush=True)
        return

    meta = c.get("meta", {}) or {}
    assignee = (meta.get("assignee") or {}).get("id")

    if assignee == ADMIN_AGENT_ID:
        print(f"[FLOW {cid}] ❌ es admin", flush=True)
        return

    next_agent = get_next_agent(assignee, online_agents)

    if not next_agent or next_agent == assignee:
        print(f"[FLOW {cid}] ❌ sin siguiente agente", flush=True)
        return

    print(f"[MOVE {cid}] {assignee} → {next_agent}", flush=True)

    assign_conversation(cid, next_agent)
    update_last_move(cid, now)


def run():
    validate_config()

    print("🔥 BOT DEBUG ACTIVO", flush=True)

    while True:
        try:
            if not is_within_schedule():
                print("⏰ fuera de horario", flush=True)
                time.sleep(INTERVAL)
                continue

            conversations = get_conversations()
            online_agents = get_online_agents()

            for c in conversations:
                cid = c["id"]
                print(f"\n🔎 ANALIZANDO {cid}", flush=True)

                was_old = process_old_assigned_conversation(c)

                if was_old:
                    continue

                process_conversation(c, online_agents)

        except Exception as e:
            print("💥 ERROR:", e, flush=True)
            traceback.print_exc()

        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
