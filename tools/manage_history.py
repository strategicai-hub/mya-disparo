import os
import redis
import json
from dotenv import load_dotenv

# Carrega as variáveis sensíveis do arquivo .env
load_dotenv()

# Configura a conexão com o Redis
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)

# Prefixo para isolar dados deste projeto no Redis compartilhado
KEY_PREFIX = "disparo"

def save_message(phone_number, role, content):
    """
    Salva uma mensagem no histórico do Redis em formato JSON.
    """
    message_data = {
        "type": role, # 'ai' ou 'human'
        "data": {
            "content": content
        }
    }

    json_message = json.dumps(message_data, ensure_ascii=False)

    key = f"{KEY_PREFIX}:history:{phone_number}"

    redis_client.rpush(key, json_message)
    return True

def get_history(phone_number):
    """
    Recupera o histórico completo e o converte em uma lista de objetos.
    """
    key = f"{KEY_PREFIX}:history:{phone_number}"

    raw_history = redis_client.lrange(key, 0, -1)

    history_objects = [json.loads(msg) for msg in raw_history]

    return history_objects

def clear_history(phone_number):
    """
    Remove o histórico de conversa de um número no Redis.
    """
    key = f"{KEY_PREFIX}:history:{phone_number}"
    result = redis_client.delete(key)
    return result > 0
