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
EXCLUDED_AGENTS = list(map(int, os.getenv("EXCLUDED_AGENTS", "").split(","))) if os.getenv("EXCLUDED_AGENTS") else []

ADMIN_AGENT_ID = int(os.getenv("ADMIN_AGENT_ID"))

# Etiqueta que el AGENTE HUMANO pone manualmente para detener la reasignación
LABEL = os.getenv("LABEL", "asignado") 
PREDICTIVE_LABEL = os.getenv("PREDICTIVE_LABEL", "predictivo")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))
ASSIGN_INTERVAL = int(os.getenv("ASSIGN_INTERVAL", 300))

# ⏰ TIEMPO DE REASIGNACIÓN (en minutos)
# Si el agente no pone la etiqueta "asignado" en este tiempo, se mueve al siguiente.
REASSIGN_TIMEOUT_MINUTES = int(os.getenv("REASSIGN_TIMEOUT_MINUTES", 15))

START_HOUR = int(os.getenv("START_HOUR", 9))
END_HOUR = int(os.getenv("END_HOUR", 20))
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
    all_conversations = []
    page = 1

    while True:
        url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations"
        params = {
            "status": "open",
            "inbox_id": INBOX_ID,
            "page": page
        }

        res = requests.get(url, headers=HEADERS, params=params, timeout=30)
        res.raise_for_status()

        data = res.json()
        payload = data.get("data", {}).get("payload", [])

        if not payload:
            break

        print(f"📄 Página {page}: {len(payload)} conversaciones")

        all_conversations.extend(payload)
        page += 1

    print(f"📥 TOTAL conversaciones: {len(all_conversations)}")
    return all_conversations


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
        and a.get("id") not in EXCLUDED_AGENTS
    ]

    print(f"👥 Agentes online (filtrados): {online}")
    return online


def assign(conversation_id, agent_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/assignments"
    requests.post(url, headers=HEADERS, json={"assignee_id": agent_id}, timeout=30)


def add_label(conversation_id, label):
    # Obtenemos actuales para no borrarlas
    current_labels = get_labels(conversation_id)
    if label not in current_labels:
        current_labels.append(label)
        url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
        requests.post(url, headers=HEADERS, json={"labels": current_labels}, timeout=30)


def add_contact_label(contact_id, label):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}/labels"
    requests.post(url, headers=HEADERS, json={"labels": [label]}, timeout=30)


def get_age_minutes(conversation):
    """Calcula minutos desde la última actividad"""
    ts = (
        conversation.get("last_activity_at")
        or conversation.get("updated_at")
        or conversation.get("created_at")
    )
    if not ts:
        return 0
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 60

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
    return (now - dt).total_seconds() / 3600


# ================= FLOW 1: NUEVOS (Solo asigna si NO tiene agente) =================

def assign_new_conversations(conversations):
    global agent_index

    print("\n🆕 ASIGNACIÓN DE NUEVOS CHATS")

    online_agents = get_online_agents()
    if not online_agents:
        print("⛔ No hay agentes online")
        return

    for c in conversations:
        cid = c["id"]

        if c.get("inbox_id") != INBOX_ID:
            continue

        # 🔒 CORRECCIÓN CRÍTICA: Solo entrar si NO tiene agente asignado.
        # Si ya tiene agente, este chat es candidato para Flow 2 (Reasignación), no para nuevo.
        current_assignee = c.get("meta", {}).get("assignee", {}).get("id")
        if current_assignee:
            continue

        # 🔒 NO TOCAR chats de agentes excluidos
        if current_assignee in EXCLUDED_AGENTS:
            continue

        # Verificamos etiquetas solo para filtrar los que ya fueron procesados manualmente
        labels = get_labels(cid)
        if LABEL in labels:
            continue

        # Asignación Round Robin
        agent_id = online_agents[agent_index % len(online_agents)]
        agent_index += 1

        print(f"[NEW {cid}] → Asignando a agente {agent_id}")

        assign(cid, agent_id)
        
        # ❌ IMPORTANTE: NO ponemos la etiqueta 'asignado' aquí.
        # El agente humano debe ponerla para confirmar atención.


# ================= FLOW 2: REASIGNACIÓN (Si no ponen etiqueta a tiempo) =================

def reassign_unanswered_chats(conversations):
    print(f"\n🔄 REASIGNACIÓN AUTOMÁTICA (Sin etiqueta '{LABEL}' > {REASSIGN_TIMEOUT_MINUTES} min)")

    online_agents = get_online_agents()
    if not online_agents:
        return

    for c in conversations:
        cid = c["id"]
        
        # 1. Debe tener un agente asignado actualmente
        current_assignee = c.get("meta", {}).get("assignee", {}).get("id")
        if not current_assignee:
            continue # Sin asignar, lo ignora el Flow 1
            
        if current_assignee in EXCLUDED_AGENTS:
            continue

        # 2. Verificar si tiene la etiqueta "asignado"
        labels = get_labels(cid)
        
        # ✅ CONDICIÓN DE PARO: Si tiene la etiqueta, el agente lo atendió. No tocar.
        if LABEL in labels:
            continue
            
        # 3. Verificar tiempo transcurrido
        age_min = get_age_minutes(c)
        
        # Si ha pasado el tiempo límite
        if age_min >= REASSIGN_TIMEOUT_MINUTES:
            print(f"[REASIGN {cid}] Inactivo {round(age_min, 1)} min sin etiqueta. Moviendo...")
            
            # Buscar un agente diferente al actual
            available = [a for a in online_agents if a != current_assignee]
            
            if not available:
                print(f"⛔ No hay otros agentes online para reasignar el chat {cid}")
                continue
                
            # Seleccionar el siguiente agente (round robin simple)
            new_agent = available[0] 
            
            assign(cid, new_agent)
            # Nota: Al reasignar, Chatwoot suele actualizar 'last_activity_at', 
            # por lo que el contador de tiempo se reinicia para el nuevo agente.
            print(f"[REASIGN {cid}] → Movido de {current_assignee} a {new_agent}")


# ================= FLOW 3: LIMPIEZA 48H =================

def process_old_conversations(conversations):
    print("\n🧹 LIMPIEZA + PREDICTIVO (>48h)")

    count_candidates = 0

    for c in conversations:
        cid = c["id"]

        if c.get("inbox_id") != INBOX_ID:
            continue

        current_assignee = c.get("meta", {}).get("assignee", {}).get("id")
        if current_assignee in EXCLUDED_AGENTS:
            continue

        age_h = get_age_hours(c)
        
        if age_h < 48:
            continue

        count_candidates += 1

        labels = get_labels(cid)
        
        # Si ya está en predictivo, saltamos
        if PREDICTIVE_LABEL in labels:
            continue

        # Si tiene la etiqueta de asignado o está abierto hace mucho, lo mandamos a admin
        print(f"[OLD {cid}] → ADMIN (Predictivo)")
        assign(cid, ADMIN_AGENT_ID)
        add_label(cid, PREDICTIVE_LABEL)

        contact_id = c["meta"]["sender"]["id"]
        add_contact_label(contact_id, PREDICTIVE_LABEL)

    print(f"📊 Chats >48h detectados: {count_candidates}")


# ================= LOOP =================

def run():
    global last_assign_time

    print("🔥 BOT ACTIVO - MODO REASIGNACIÓN POR ETIQUETA MANUAL")

    while True:
        try:
            if not is_within_schedule():
                print("🌙 Fuera de horario")
                time.sleep(60)
                continue

            conversations = get_conversations()
            now = time.time()

            # 1. ASIGNACIÓN NUEVA
            if now - last_assign_time >= ASSIGN_INTERVAL:
                assign_new_conversations(conversations)
                last_assign_time = now

            # 2. REASIGNACIÓN POR FALTA DE ETIQUETA
            # Se ejecuta siempre para vigilar los tiempos
            reassign_unanswered_chats(conversations)

            # 3. LIMPIEZA 48H
            process_old_conversations(conversations)

        except Exception as e:
            print(f"❌ ERROR: {e}")

        time.sleep(CHECK_INTERVAL)


# ================= START =================

if __name__ == "__main__":
    run()
