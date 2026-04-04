import os
import time
import json
import redis
from dotenv import load_dotenv

load_dotenv()

# Usa o mesmo Redis DB, isolamento via prefixo nas chaves
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
except Exception as e:
    print(f"Erro ao conectar ao Redis para Follow-ups: {e}")
    redis_client = None

OWNER_NUMBER = "5511989887525"
FOLLOWUP_IMAGE_URL = "https://webhook-whatsapp.strategicai.com.br/mya-disparo/resultado"

# Prefixo para isolar dados deste projeto no Redis compartilhado
KEY_PREFIX = "disparo"

# Intervalos em segundos
INTERVALS_NORMAL = [86400, 259200, 604800, 1296000]   # 1d, 3d, 7d, 15d
INTERVALS_OWNER  = [3600, 7200, 10800, 14400]          # 1h, 2h, 3h, 4h


def reset_followup_timer(phone_number: str):
    """Reseta o timer dos follow-ups — cancela os antigos e reagenda do zero."""
    if not redis_client:
        return
    if has_active_followups(phone_number):
        cancel_followups(phone_number)
        print(f"[FOLLOWUP] Timer resetado para {phone_number} (lead respondeu)")


def schedule_followups(phone_number: str, nome: str = "", nicho: str = "", resumo: str = ""):
    """Agenda 4 follow-ups no Redis (sorted set)."""
    if not redis_client:
        return

    # Cancela anteriores e reagenda do zero
    if has_active_followups(phone_number):
        cancel_followups(phone_number)

    saudacao = f"Oi {nome}, " if nome else "Oi, "
    nome_virgula = f"{nome}, " if nome else ""

    intervals = INTERVALS_OWNER if phone_number == OWNER_NUMBER else INTERVALS_NORMAL
    now = time.time()

    messages = [
        {
            "phone": phone_number,
            "step": 1,
            "type": "text",
            "message": f"{saudacao}imagino que seu dia esteja corrido. Conseguiu dar aquela olhada no que te mandei ontem?"
        },
        {
            "phone": phone_number,
            "step": 2,
            "type": "text",
            "message": f"{nome_virgula}esqueci de mencionar: no mês passado ajudamos uma empresa do mesmo setor que a sua a aumentar o faturamento em 40% com automação por IA. Achei que gostaria de saber."
        },
        {
            "phone": phone_number,
            "step": 3,
            "type": "image",
            "image_url": FOLLOWUP_IMAGE_URL,
            "message": ""
        },
        {
            "phone": phone_number,
            "step": 4,
            "type": "text",
            "message": f"{saudacao}como não tivemos retorno, entendo que este não é o melhor momento para falarmos sobre IA para o seu negócio. Vou tirar seu contato da minha lista de prioridades por enquanto. Se precisar de algo no futuro, estou por aqui!"
        }
    ]

    followups_key = f"{KEY_PREFIX}:followups"
    for i, msg in enumerate(messages):
        timestamp = now + intervals[i]
        raw = json.dumps(msg, ensure_ascii=False)
        redis_client.zadd(followups_key, {raw: timestamp})
        redis_client.sadd(f"{KEY_PREFIX}:followup:members:{phone_number}", raw)

    redis_client.set(f"{KEY_PREFIX}:followup:active:{phone_number}", "1")
    print(f"[FOLLOWUP] 4 follow-ups agendados para {phone_number}")


def cancel_followups(phone_number: str):
    """Remove todos os follow-ups pendentes de um lead."""
    if not redis_client:
        return

    members_key = f"{KEY_PREFIX}:followup:members:{phone_number}"
    members = redis_client.smembers(members_key)

    followups_key = f"{KEY_PREFIX}:followups"
    if members:
        for raw in members:
            redis_client.zrem(followups_key, raw)
        redis_client.delete(members_key)

    redis_client.delete(f"{KEY_PREFIX}:followup:active:{phone_number}")
    print(f"[FOLLOWUP] Follow-ups cancelados para {phone_number}")


def has_active_followups(phone_number: str) -> bool:
    """Verifica se o lead tem follow-ups pendentes (O(1))."""
    if not redis_client:
        return False
    return redis_client.exists(f"{KEY_PREFIX}:followup:active:{phone_number}") == 1


def get_due_followups(now_timestamp: float) -> list:
    """Retorna follow-ups com timestamp <= now (prontos para envio)."""
    if not redis_client:
        return []

    followups_key = f"{KEY_PREFIX}:followups"
    raw_items = redis_client.zrangebyscore(followups_key, 0, now_timestamp)
    results = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
            item["_raw"] = raw  # Guarda o JSON original para remoção
            results.append(item)
        except json.JSONDecodeError:
            redis_client.zrem(followups_key, raw)
    return results


def remove_followup(raw_json: str, phone_number: str):
    """Remove um follow-up específico após envio."""
    if not redis_client:
        return
    followups_key = f"{KEY_PREFIX}:followups"
    redis_client.zrem(followups_key, raw_json)
    redis_client.srem(f"{KEY_PREFIX}:followup:members:{phone_number}", raw_json)

    # Se não restam mais membros, limpa o flag active
    remaining = redis_client.scard(f"{KEY_PREFIX}:followup:members:{phone_number}")
    if remaining == 0:
        redis_client.delete(f"{KEY_PREFIX}:followup:active:{phone_number}")


def reschedule_followup(raw_json: str, new_timestamp: float):
    """Reagenda um follow-up para novo horário (ex: horário comercial)."""
    if not redis_client:
        return
    followups_key = f"{KEY_PREFIX}:followups"
    redis_client.zadd(followups_key, {raw_json: new_timestamp})
