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
EXCLUDED_AGENTS = (
    list(map(int, os.getenv("EXCLUDED_AGENTS", "").split(",")))
    if os.getenv("EXCLUDED_AGENTS")
    else []
)

ADMIN_AGENT_ID = int(os.getenv("ADMIN_AGENT_ID"))

# Etiqueta que el agente pone manualmente para detener el bot
LABEL = os.getenv("LABEL", "asignado")
PREDICTIVE_LABEL = os.getenv("PREDICTIVE_LABEL", "predictivo")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))
ASSIGN_INTERVAL = int(os.getenv("ASSIGN_INTERVAL", 300))

# ⏰ TIEMPO DE REASIGNACIÓN EN MINUTOS
# Si el agente no pone ninguna etiqueta en este tiempo, el bot mueve el chat al siguiente agente.
REASSIGN_TIMEOUT_MINUTES = int(os.getenv("REASSIGN_TIMEOUT_MINUTES", 4))

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

# Guarda la última vez que cada conversación fue reasignada.
# Esto evita que el mismo chat se mueva sin parar en cada ciclo.
last_reassign_by_conversation = {}

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

        try:
            res = requests.get(url, headers=HEADERS, params=params, timeout=30)
            res.raise_for_status()
        except Exception as e:
            print(f"❌ Error obteniendo conversaciones: {e}")
            return []

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
    try:
        url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"
        res = requests.get(url, headers=HEADERS, timeout=30)
        res.raise_for_status()
        return res.json().get("payload", [])
    except Exception as e:
        print(f"❌ Error obteniendo etiquetas de conversación {conversation_id}: {e}")
        return []


def get_online_agents():
    try:
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

        print(f"👥 Agentes online filtrados: {online}")
        return online

    except Exception as e:
        print(f"❌ Error obteniendo agentes: {e}")
        return []


def assign(conversation_id, agent_id):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/assignments"

    try:
        res = requests.post(
            url,
            headers=HEADERS,
            json={"assignee_id": agent_id},
            timeout=30
        )
        res.raise_for_status()
        return True

    except Exception as e:
        print(f"❌ Error asignando conversación {conversation_id} a agente {agent_id}: {e}")
        return False


def add_label(conversation_id, label):
    # Obtenemos etiquetas actuales para no borrarlas.
    current_labels = get_labels(conversation_id)

    if label not in current_labels:
        current_labels.append(label)

        url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conversation_id}/labels"

        try:
            res = requests.post(
                url,
                headers=HEADERS,
                json={"labels": current_labels},
                timeout=30
            )
            res.raise_for_status()

        except Exception as e:
            print(f"❌ Error agregando etiqueta '{label}' a conversación {conversation_id}: {e}")


def add_contact_label(contact_id, label):
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{contact_id}/labels"

    try:
        res = requests.post(
            url,
            headers=HEADERS,
            json={"labels": [label]},
            timeout=30
        )
        res.raise_for_status()

    except Exception as e:
        print(f"❌ Error agregando etiqueta '{label}' al contacto {contact_id}: {e}")


def get_age_minutes(conversation):
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


def get_next_agent(online_agents, current_assignee):
    """
    Devuelve el siguiente agente en la lista de agentes online.

    Ejemplo:
    online_agents = [33, 25, 20, 23]

    Si current_assignee = 33, devuelve 25.
    Si current_assignee = 25, devuelve 20.
    Si current_assignee = 23, devuelve 33.
    """

    if not online_agents:
        return None

    if len(online_agents) == 1:
        return None

    if current_assignee in online_agents:
        current_index = online_agents.index(current_assignee)
        next_index = (current_index + 1) % len(online_agents)
        return online_agents[next_index]

    # Si el agente actual no está online, mandamos al primer agente online filtrado.
    return online_agents[0]


# ================= FLOW 1: ASIGNACIÓN NUEVA =================

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

        # Solo entrar si NO tiene agente asignado.
        current_assignee = c.get("meta", {}).get("assignee", {}).get("id")

        if current_assignee:
            continue

        # Si ya tiene etiquetas, no es un chat nuevo limpio.
        labels = get_labels(cid)

        if len(labels) > 0:
            continue

        agent_id = online_agents[agent_index % len(online_agents)]
        agent_index += 1

        print(f"[NEW {cid}] → Asignando a agente {agent_id}")

        ok = assign(cid, agent_id)

        if ok:
            print(f"[NEW {cid}] ✅ Asignado correctamente a {agent_id}")

        # No ponemos etiqueta aquí.
        # El agente debe poner manualmente la etiqueta para detener el bot.


# ================= FLOW 2: REASIGNACIÓN POR INACTIVIDAD =================

def reassign_unanswered_chats(conversations):
    global last_reassign_by_conversation

    print(f"\n🔄 REASIGNACIÓN (Sin etiquetas y > {REASSIGN_TIMEOUT_MINUTES} min)")

    online_agents = get_online_agents()

    if not online_agents:
        print("⛔ No hay agentes online para reasignar")
        return

    for c in conversations:
        cid = c["id"]

        if c.get("inbox_id") != INBOX_ID:
            continue

        # 1. Debe tener agente asignado actualmente.
        current_assignee = c.get("meta", {}).get("assignee", {}).get("id")

        if not current_assignee:
            continue

        if current_assignee in EXCLUDED_AGENTS:
            continue

        # 2. Revisión de etiquetas.
        labels = get_labels(cid)

        # REGLA:
        # Si tiene cualquier etiqueta, no se mueve.
        # Solo movemos chats sin etiquetas.
        if len(labels) > 0:
            continue

        # 3. Revisar tiempo de inactividad.
        age_min = get_age_minutes(c)

        if age_min < REASSIGN_TIMEOUT_MINUTES:
            continue

        # 4. Cooldown para evitar que el mismo chat se mueva sin parar.
        now = time.time()
        last_reassign = last_reassign_by_conversation.get(cid, 0)
        cooldown_seconds = REASSIGN_TIMEOUT_MINUTES * 60

        if now - last_reassign < cooldown_seconds:
            remaining = round((cooldown_seconds - (now - last_reassign)) / 60, 1)
            print(f"[REASIGN {cid}] ⏳ Ya fue reasignado recientemente. Esperando {remaining} min.")
            continue

        print(f"[REASIGN {cid}] Inactivo {round(age_min, 1)} min SIN etiquetas. Evaluando movimiento...")

        # 5. Obtener siguiente agente en orden.
        new_agent = get_next_agent(online_agents, current_assignee)

        if not new_agent:
            print(f"⛔ No hay otro agente online para reasignar el chat {cid}")
            continue

        if new_agent == current_assignee:
            print(f"⛔ El nuevo agente es igual al actual para el chat {cid}")
            continue

        # 6. Reasignar.
        ok = assign(cid, new_agent)

        if ok:
            last_reassign_by_conversation[cid] = now
            print(f"[REASIGN {cid}] ✅ Movido de {current_assignee} a {new_agent}")
        else:
            print(f"[REASIGN {cid}] ❌ No se pudo mover de {current_assignee} a {new_agent}")


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

        # 1. Si ya está en predictivo, saltamos.
        if PREDICTIVE_LABEL in labels:
            continue

        # 2. Solo movemos a Admin si tiene exactamente la etiqueta LABEL.
        # Ejemplo:
        # labels = ["asignado"] sí se mueve.
        # labels = ["seguimiento"] no se mueve.
        # labels = ["asignado", "seguimiento"] no se mueve.
        if not (len(labels) == 1 and LABEL in labels):
            continue

        print(f"[OLD {cid}] → ADMIN ({PREDICTIVE_LABEL})")

        ok = assign(cid, ADMIN_AGENT_ID)

        if not ok:
            print(f"[OLD {cid}] ❌ No se pudo mover a ADMIN")
            continue

        add_label(cid, PREDICTIVE_LABEL)

        contact_id = c.get("meta", {}).get("sender", {}).get("id")

        if contact_id:
            add_contact_label(contact_id, PREDICTIVE_LABEL)

        print(f"[OLD {cid}] ✅ Movido a ADMIN y marcado como {PREDICTIVE_LABEL}")

    print(f"📊 Chats >48h detectados: {count_candidates}")


# ================= LOOP =================

def run():
    global last_assign_time

    print("🔥 BOT ACTIVO - MODO REASIGNACIÓN CORREGIDA")
    print("✅ Reasignación ahora avanza al siguiente agente online")
    print("✅ Cooldown activado para evitar bucles infinitos")
    print(f"✅ Timeout de reasignación: {REASSIGN_TIMEOUT_MINUTES} min")
    print(f"✅ Horario: {START_HOUR}:00 a {END_HOUR}:00")
    print(f"✅ Zona horaria: {TIMEZONE}")

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

            # 2. REASIGNACIÓN
            reassign_unanswered_chats(conversations)

            # 3. LIMPIEZA
            process_old_conversations(conversations)

        except Exception as e:
            print(f"❌ ERROR GLOBAL: {e}")

        time.sleep(CHECK_INTERVAL)


# ================= START =================

if __name__ == "__main__":
    run()
