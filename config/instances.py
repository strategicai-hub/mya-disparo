"""Mapeamento de instâncias WhatsApp (token UAZAPI por instância) e destinos de alerta.

Cada instância tem seu próprio token UAZAPI e responde em um webhook distinto
(/mya-disparo-1, /mya-disparo-2, /mya-disparo-3). O instance_id ("1"|"2"|"3") é
extraído do path pelo api.py e propagado em todo o pipeline.
"""
import os

UAZAPI_URL = os.getenv("UAZAPI_URL", "https://strategicai.uazapi.com")

INSTANCES = {
    "1": {
        "token": os.getenv("UAZAPI_TOKEN_1", ""),
        "instance": os.getenv("UAZAPI_INSTANCE_1", ""),
    },
    "2": {
        "token": os.getenv("UAZAPI_TOKEN_2", ""),
        "instance": os.getenv("UAZAPI_INSTANCE_2", ""),
    },
    "3": {
        "token": os.getenv("UAZAPI_TOKEN_3", ""),
        "instance": os.getenv("UAZAPI_INSTANCE_3", ""),
    },
}

ALERT_GROUP_TOKEN = os.getenv("UAZAPI_ALERT_GROUP_TOKEN", "")
ALERT_GROUP_ID = os.getenv("UAZAPI_ALERT_GROUP_ID", "120363402718927307@g.us")

OWNER_NUMBER = os.getenv("OWNER_NUMBER", "5511989887525")


def get_token(instance_id) -> str:
    cfg = INSTANCES.get(str(instance_id))
    if not cfg or not cfg["token"]:
        raise ValueError(f"Instância '{instance_id}' não configurada (UAZAPI_TOKEN_{instance_id} ausente)")
    return cfg["token"]


def get_instance_name(instance_id) -> str:
    cfg = INSTANCES.get(str(instance_id))
    return cfg["instance"] if cfg else ""


def redis_prefix(instance_id) -> str:
    return f"disparo:{instance_id}"


def valid_instance(instance_id) -> bool:
    cfg = INSTANCES.get(str(instance_id))
    return bool(cfg and cfg["token"])
