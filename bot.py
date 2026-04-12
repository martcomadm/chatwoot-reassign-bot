import os
import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ================= CONFIG =================

BASE_URL = os.getenv("BASE_URL")
API_TOKEN = os.getenv("API_TOKEN")
ACCOUNT_ID = int(os.getenv("ACCOUNT_ID"))
INBOX_ID = int(os.getenv("INBOX_ID"))

AGENTS = list(map(int, os.getenv("AGENTS").split(",")))
ADMIN_AGENT_ID = int(os.getenv("ADMIN_AGENT_ID"))

LABEL = os.getenv("LABEL", "asignado")
PREDICTIVE_LABEL = os.getenv("PREDICTIVE_LABEL", "predictivo")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))
ASSIGN_INTERVAL = int(os.getenv("ASSIGN_INTERVAL", 180))

START_HOUR = int(os.getenv("START_HOUR", 7))
END_HOUR = int(os.getenv("END_HOUR", 21))
TIMEZONE = os.getenv("TIMEZONE", "America/Mexico_City")

tz = ZoneInfo(TIMEZONE)

HEADERS = {
    "api_access_token": API_TOKEN,
    "Content-Type": "application/json",
}

last_assign_time = 0
agent_index = 0

# ================= HELPERS =================

def safe_list(data, key):
    if isinstance(data, list):
        return data
    return data.get(key, [])


def is_within_schedule():
    now = datetime.now(tz)
    print(f"\n⏰ Hora actual: {now}")
    return START_HOUR <= now.hour < END_HOUR


def get_conversations():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations?status=open&inbox_id={INBOX_ID}"
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()

    data = res.json()
    payload = data.get("data", {}).get("payload", [])

    print(f"📥 Conversaciones obtenidas: {len(payload)}")
    return payload


def get_labels(conversation_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()

    return res.json().get("payload", [])


def get_online_agents():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/agents"
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()

    data = res.json()
    agents = safe_list(data, "data")

    online = [
        a["id"]
        for a in agents
        if a.get("availability_status") == "online"
        and a.get("id") in AGENTS
    ]

    print(f"👥 Agentes online: {online}")
    return online


def assign(conversation_id, agent_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/assignments"
    requests.post(url, headers=HEADERS, json={"assignee_id": agent_id}, timeout=30)


def add_label(conversation_id, label):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30)


def add_contact_label(contact_id, label):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30)


def get_age_hours(conversation):
    ts = (
        conversation.get("last_activity_at")
        or conversation.get("updated_at")
        or conversation.get("created_at")
    )

    if not ts:
        return 0

    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    now = datetime.now(timezone.utc)

    age = (now - dt).total_seconds() / 3600
    return age


# ================= FLOW 1 =================

def assign_new_conversations(conversations):
    global agent_index

    print("\n🔁 ASIGNACIÓN CONTROLADA")

    online_agents = get_online_agents()

    if not online_agents:
        print("⛔ No hay agentes online, no se asigna nada")
        return

    for c in conversations:
        cid = c["id"]

        # 🔒 SOLO inbox correcto
        if c.get("inbox_id") != INBOX_ID:
            continue

        labels = get_labels(cid)
        print(f"[ASSIGN {cid}] labels={labels}")

        # 🔒 SOLO chats SIN labels (nuevos)
        if len(labels) != 0:
            continue

        agent_id = online_agents[agent_index % len(online_agents)]
        agent_index += 1

        print(f"[ASSIGN {cid}] → agente {agent_id}")

        assign(cid, agent_id)
        add_label(cid, LABEL)


# ================= FLOW 2 =================

def process_old_conversations(conversations):
    print("\n🧠 LIMPIEZA + PREDICTIVO")

    for c in conversations:
        cid = c["id"]

        if c.get("inbox_id") != INBOX_ID:
            continue

        age_h = get_age_hours(c)
        print(f"[CHECK {cid}] age_h={round(age_h,2)}")

        # 🔒 SOLO > 48 horas
        if age_h < 48:
            continue

        labels = get_labels(cid)
        print(f"[OLD {cid}] labels={labels}")

        # 🔒 SOLO si tiene EXACTAMENTE ['asignado']
        if labels != [LABEL]:
            continue

        print(f"[OLD {cid}] → ADMIN")

        assign(cid, ADMIN_AGENT_ID)
        add_label(cid, PREDICTIVE_LABEL)

        contact_id = c["meta"]["sender"]["id"]
        add_contact_label(contact_id, PREDICTIVE_LABEL)


# ================= LOOP =================

def run():
    global last_assign_time

    print("🔥 BOT FINAL PRODUCCIÓN ACTIVO")

    while True:
        try:
            if not is_within_schedule():
                print("🌙 Fuera de horario")
                time.sleep(60)
                continue

            conversations = get_conversations()
            now = time.time()

            # 🔁 ASIGNACIÓN CONTROLADA
            if now - last_assign_time >= ASSIGN_INTERVAL:
                assign_new_conversations(conversations)
                last_assign_time = now

            # 🧠 LIMPIEZA 48H
            process_old_conversations(conversations)

        except Exception as e:
            print(f"❌ ERROR: {e}")

        time.sleep(CHECK_INTERVAL)


# ================= START =================

if __name__ == "__main__":
    run()
