"""Callback de auditoria: registra outbound enviado pela Mya no disparador-whatsapp.

Só dispara quando:
- DISPARADOR_AUDIT_URL e CHATBOT_FORWARD_SECRET estão setados
- Há tenant_id armazenado em Redis para (instance_id, phone) — gravado pelo
  /mya-disparo-meta-{instance_id} ao receber inbound do disparador.

Sem essas três condições, vira no-op silencioso (modo standalone preservado).
"""
import os
import requests
import redis as redis_module

from config.instances import redis_prefix

_DISPARADOR_AUDIT_URL = os.getenv("DISPARADOR_AUDIT_URL", "")
_CHATBOT_SECRET = os.getenv("CHATBOT_FORWARD_SECRET", "")
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    _redis = redis_module.Redis.from_url(_REDIS_URL, decode_responses=True)
    _redis.ping()
except Exception:
    _redis = None


def _normalize_phone(phone: str) -> str:
    return phone.split("@")[0].lstrip("+")


def remember_tenant(instance_id, phone: str, tenant_id: str, ttl_seconds: int = 86400) -> None:
    """Guarda o tenant_id associado a (instance, phone) por 24h. Chamado pelo webhook Meta."""
    if not _redis or not tenant_id:
        return
    p = _normalize_phone(phone)
    _redis.setex(f"{redis_prefix(instance_id)}:tenant:{p}", ttl_seconds, tenant_id)


def get_tenant(instance_id, phone: str) -> str:
    if not _redis:
        return ""
    p = _normalize_phone(phone)
    return _redis.get(f"{redis_prefix(instance_id)}:tenant:{p}") or ""


def notify_outbound(instance_id, phone: str, text: str, wamid: str, msg_type: str = "text") -> None:
    """Notifica o disparador (best-effort, não levanta exceção)."""
    if not _DISPARADOR_AUDIT_URL or not _CHATBOT_SECRET:
        return
    tenant_id = get_tenant(instance_id, phone)
    if not tenant_id:
        return
    try:
        requests.post(
            _DISPARADOR_AUDIT_URL,
            json={
                "tenantId": tenant_id,
                "to": _normalize_phone(phone),
                "text": text,
                "wamid": wamid,
                "type": msg_type,
            },
            headers={"x-chatbot-secret": _CHATBOT_SECRET, "Content-Type": "application/json"},
            timeout=5,
        )
    except Exception as e:
        print(f"[AUDIT] notify_outbound falhou (não crítico): {e}")
