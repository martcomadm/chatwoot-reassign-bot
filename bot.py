import os
import time
import traceback
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ================= CONFIG =================
BASE_URL = os.getenv("CHATWOOT_BASE_URL", "").rstrip("/")
ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "").strip()
TOKEN = os.getenv("CHATWOOT_API_TOKEN", "").strip()

INBOX_ID = int(os.getenv("TARGET_INBOX_ID", "0"))
LABEL = os.getenv("ASSIGNED_LABEL", "asignado").strip().lower()

WAIT_TIME = int(os.getenv("REASSIGN_AFTER_SECONDS", "180"))
INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

AGENTS = [int(x.strip()) for x in os.getenv("AGENT_IDS", "").split(",") if x.strip()]

# 🔥 NUEVO
PROVEEDOR_AGENT_ID = int(os.getenv("PROVEEDOR_AGENT_ID", "0"))
PROVEEDOR_LABEL = os.getenv("PROVEEDOR_LABEL", "proveedor")

PROVEEDOR_KEYWORDS = [
    x.strip().lower()
    for x in os.getenv("PROVEEDOR_KEYWORDS", "").split(",")
    if x.strip()
]

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

HEADERS = {
    "api_access_token": TOKEN,
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ================= UTILS =================

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
    return response.json().get("data", {}).get("payload", [])

def get_labels(conversation_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return [str(x).lower() for x in response.json().get("payload", [])]

def add_label(conversation_id, label):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30)

def get_last_message(conversation_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/messages"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    payload = response.json().get("payload", [])

    for msg in reversed(payload):
        if msg.get("message_type") == 0:
            return msg.get("content", "")

    return payload[-1].get("content", "") if payload else ""

def es_proveedor_keywords(texto):
    if not texto:
        return False
    texto = texto.lower()
    return any(k in texto for k in PROVEEDOR_KEYWORDS)

# ================= IA =================

def clasificar_mensaje_ia(mensaje):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"""
Clasifica este mensaje en:
cliente, proveedor u otro.

Responde solo una palabra.

Mensaje:
{mensaje}
"""
            }],
            temperature=0
        )

        result = response.choices[0].message.content.strip().lower()

        if result not in ["cliente", "proveedor", "otro"]:
            return "otro"

        return result

    except Exception as e:
        print("Error IA:", e)
        return "otro"

# ================= CHATWOOT =================

def assign_conversation(conversation_id, agent_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/assignments"
    requests.post(url, headers=HEADERS, json={"assignee_id": agent_id}, timeout=30)

def update_custom_attributes(conversation_id, attrs):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/custom_attributes"
    requests.post(url, headers=HEADERS, json={"custom_attributes": attrs}, timeout=30)

def get_online_agents():
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/agents"
    data = requests.get(url, headers=HEADERS, timeout=30).json()

    agents_data = data if isinstance(data, list) else data.get("payload", [])

    return [
        a["id"]
        for a in agents_data
        if a.get("id") in AGENTS and str(a.get("availability_status", "")).lower() == "online"
    ]

def get_next_agent(current):
    online = get_online_agents()
    if not online:
        return None

    if current in online:
        return online[(online.index(current) + 1) % len(online)]

    return online[0]

# ================= CORE =================

def detectar_tipo(conversation, mensaje):
    attrs = conversation.get("custom_attributes") or {}
    tipo_guardado = attrs.get("tipo_lead")

    if tipo_guardado:
        return tipo_guardado

    # 🔥 1. keywords primero (gratis)
    if es_proveedor_keywords(mensaje):
        tipo = "proveedor"
    else:
        # 🔥 2. IA si no detecta
        tipo = clasificar_mensaje_ia(mensaje)

    update_custom_attributes(conversation["id"], {"tipo_lead": tipo})
    return tipo

def process_conversation(conversation):
    cid = conversation["id"]
    now = int(time.time())

    meta = conversation.get("meta", {}) or {}
    assignee = (meta.get("assignee") or {}).get("id")

    # 🔥 obtener mensaje
    try:
        mensaje = get_last_message(cid)
        tipo = detectar_tipo(conversation, mensaje)

        if tipo == "proveedor":
            print(f"[CID {cid}] 🚨 PROVEEDOR detectado", flush=True)

            if assignee != PROVEEDOR_AGENT_ID:
                assign_conversation(cid, PROVEEDOR_AGENT_ID)
                add_label(cid, PROVEEDOR_LABEL)
                update_custom_attributes(cid, {"last_move": now})

            return

    except Exception as e:
        print(f"[CID {cid}] error IA: {e}", flush=True)

    # ===== TU LÓGICA ORIGINAL =====
    attrs = conversation.get("custom_attributes") or {}
    last_move = int(attrs.get("last_move", 0) or 0)

    if last_move and (now - last_move < WAIT_TIME):
        return

    next_agent = get_next_agent(assignee)

    if not next_agent or next_agent == assignee:
        return

    assign_conversation(cid, next_agent)
    update_custom_attributes(cid, {"last_move": now})

    print(f"[CID {cid}] reasignada → {next_agent}", flush=True)

# ================= RUN =================

def run():
    validate_config()
    print("🔥 BOT IA ACTIVO", flush=True)

    while True:
        try:
            for c in get_conversations():
                process_conversation(c)

        except Exception as e:
            print("ERROR:", e)
            traceback.print_exc()

        time.sleep(INTERVAL)

if __name__ == "__main__":
    run()
