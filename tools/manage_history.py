import os
import redis
import json
from dotenv import load_dotenv

from config.instances import redis_prefix

load_dotenv()

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)


def save_message(phone_number: str, role: str, content: str, instance_id) -> bool:
    """Salva uma mensagem no histórico do Redis em formato JSON."""
    message_data = {"type": role, "data": {"content": content}}
    json_message = json.dumps(message_data, ensure_ascii=False)
    key = f"{redis_prefix(instance_id)}:history:{phone_number}"
    redis_client.rpush(key, json_message)
    return True


def get_history(phone_number: str, instance_id) -> list:
    """Recupera o histórico completo e o converte em uma lista de objetos."""
    key = f"{redis_prefix(instance_id)}:history:{phone_number}"
    raw_history = redis_client.lrange(key, 0, -1)
    return [json.loads(msg) for msg in raw_history]


def clear_history(phone_number: str, instance_id) -> bool:
    """Remove o histórico de conversa de um número no Redis."""
    key = f"{redis_prefix(instance_id)}:history:{phone_number}"
    result = redis_client.delete(key)
    return result > 0
