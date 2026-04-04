import os
import json
import redis
from dotenv import load_dotenv

load_dotenv()

# Usa o mesmo Redis DB (sem trocar DB), isolamento via prefixo nas chaves
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
except Exception as e:
    print(f"Erro ao conectar ao Redis para CRM: {e}")
    redis_client = None

# Prefixo para isolar dados deste projeto no Redis compartilhado
KEY_PREFIX = "disparo"

def save_lead_info(phone_number: str, data: dict):
    if not redis_client:
        return

    key = f"{KEY_PREFIX}:lead:{phone_number}"
    existing = redis_client.get(key)

    if existing:
        info = json.loads(existing)
    else:
        info = {}

    info.update(data)
    redis_client.set(key, json.dumps(info, ensure_ascii=False))
    print(f"[CRM] Atualizado {key} com: {data}")

def get_lead_info(phone_number: str) -> dict:
    if not redis_client:
        return {}

    key = f"{KEY_PREFIX}:lead:{phone_number}"
    existing = redis_client.get(key)
    if existing:
        return json.loads(existing)
    return {}

def clear_lead_info(phone_number: str):
    """"Zera o contexto inteiro do Lead para debug."""
    if redis_client:
        redis_client.delete(f"{KEY_PREFIX}:lead:{phone_number}")
        print(f"[CRM] Apagada a memória de CRM do lead {phone_number}")
