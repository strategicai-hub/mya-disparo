"""Mapeamento de instâncias WhatsApp por provider (UAZAPI ou Meta Cloud API oficial).

Cada instance_id ("1", "2", "3", "gustavo", ...) tem provider + credenciais próprias.
- UAZAPI: token + nome de instância. Webhook: /mya-disparo-{instance_id}
- Meta (Cloud API): phone_number_id + access_token. Webhook: /mya-disparo-meta-{instance_id}
"""
import os

UAZAPI_URL = os.getenv("UAZAPI_URL", "https://strategicai.uazapi.com")
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v22.0")

# Lista canônica de instâncias suportadas. Adicione novos ids aqui.
# "disparo" = instância Meta Cloud API alimentada pelo disparador-whatsapp
_INSTANCE_IDS = ["1", "2", "3", "gustavo", "disparo"]


def _build_instances() -> dict:
    instances = {}
    for instance_id in _INSTANCE_IDS:
        # Suporta tanto UAZAPI_TOKEN_1 quanto UAZAPI_TOKEN_GUSTAVO
        suffix = instance_id.upper()
        provider = (os.getenv(f"PROVIDER_{suffix}") or "uazapi").lower()
        instances[instance_id] = {
            "provider": provider,
            "token": os.getenv(f"UAZAPI_TOKEN_{suffix}", ""),
            "instance": os.getenv(f"UAZAPI_INSTANCE_{suffix}", ""),
            "meta_phone_number_id": os.getenv(f"META_PHONE_NUMBER_ID_{suffix}", ""),
            "meta_access_token": os.getenv(f"META_ACCESS_TOKEN_{suffix}", ""),
        }
    return instances


INSTANCES = _build_instances()

ALERT_GROUP_TOKEN = os.getenv("UAZAPI_ALERT_GROUP_TOKEN", "")
ALERT_GROUP_ID = os.getenv("UAZAPI_ALERT_GROUP_ID", "120363402718927307@g.us")

OWNER_NUMBER = os.getenv("OWNER_NUMBER", "5511989887525")


def get_provider(instance_id) -> str:
    cfg = INSTANCES.get(str(instance_id))
    return cfg["provider"] if cfg else "uazapi"


def get_token(instance_id) -> str:
    """Token UAZAPI da instância. Use somente para providers UAZAPI."""
    cfg = INSTANCES.get(str(instance_id))
    if not cfg or not cfg["token"]:
        raise ValueError(f"Instância '{instance_id}' não configurada (UAZAPI_TOKEN_{str(instance_id).upper()} ausente)")
    return cfg["token"]


def get_meta_config(instance_id) -> dict:
    """phone_number_id + access_token da Meta Cloud API. Use somente para providers Meta."""
    cfg = INSTANCES.get(str(instance_id))
    if not cfg or not cfg["meta_phone_number_id"] or not cfg["meta_access_token"]:
        raise ValueError(
            f"Instância '{instance_id}' sem credenciais Meta "
            f"(META_PHONE_NUMBER_ID_{str(instance_id).upper()} / META_ACCESS_TOKEN_{str(instance_id).upper()} ausentes)"
        )
    return {
        "phone_number_id": cfg["meta_phone_number_id"],
        "access_token": cfg["meta_access_token"],
    }


def get_instance_name(instance_id) -> str:
    cfg = INSTANCES.get(str(instance_id))
    return cfg["instance"] if cfg else ""


def redis_prefix(instance_id) -> str:
    return f"disparo:{instance_id}"


def valid_instance(instance_id) -> bool:
    cfg = INSTANCES.get(str(instance_id))
    if not cfg:
        return False
    if cfg["provider"] == "meta":
        return bool(cfg["meta_phone_number_id"] and cfg["meta_access_token"])
    return bool(cfg["token"])
