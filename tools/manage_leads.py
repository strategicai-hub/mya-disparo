import os
import json
import time
import redis
from dotenv import load_dotenv

from config.instances import redis_prefix, OWNER_NUMBER

load_dotenv()

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
except Exception as e:
    print(f"Erro ao conectar ao Redis para CRM: {e}")
    redis_client = None


def save_lead_info(phone_number: str, data: dict, instance_id):
    if not redis_client:
        return

    key = f"{redis_prefix(instance_id)}:lead:{phone_number}"
    existing = redis_client.get(key)
    info = json.loads(existing) if existing else {}
    info.update(data)
    redis_client.set(key, json.dumps(info, ensure_ascii=False))
    print(f"[CRM] Atualizado {key} com: {data}")


def get_lead_info(phone_number: str, instance_id) -> dict:
    if not redis_client:
        return {}

    key = f"{redis_prefix(instance_id)}:lead:{phone_number}"
    existing = redis_client.get(key)
    if existing:
        return json.loads(existing)
    return {}


def clear_lead_info(phone_number: str, instance_id):
    """Zera o contexto inteiro do Lead para debug."""
    if not redis_client:
        print(f"[CRM] ❌ Redis não conectado, não foi possível limpar CRM")
        return
    key = f"{redis_prefix(instance_id)}:lead:{phone_number}"
    result = redis_client.delete(key)
    if result > 0:
        print(f"[CRM] ✅ Apagada a memória de CRM do lead {phone_number}")
    else:
        print(f"[CRM] ⚠️ Nenhuma chave encontrada para deletar: {key}")


# TTL do bloqueio por suspeita de IA: 1 ano
AI_BLOCK_TTL_SECONDS = 60 * 60 * 24 * 365

# TTL do timestamp "ultima msg da Mya": 24h (heurística de bot por tempo de resposta)
LAST_AI_SENT_TTL_SECONDS = 60 * 60 * 24


def mark_ai_sent_now(phone_number: str, instance_id) -> None:
    """Marca agora como instante em que a Mya enviou mensagem ao lead."""
    if not redis_client:
        return
    key = f"{redis_prefix(instance_id)}:last_ai_sent:{phone_number}"
    redis_client.setex(key, LAST_AI_SENT_TTL_SECONDS, str(time.time()))


def seconds_since_last_ai_msg(phone_number: str, instance_id):
    """Retorna segundos desde a ultima msg da Mya, ou None se nao houver registro."""
    if not redis_client:
        return None
    key = f"{redis_prefix(instance_id)}:last_ai_sent:{phone_number}"
    raw = redis_client.get(key)
    if not raw:
        return None
    try:
        return time.time() - float(raw)
    except (TypeError, ValueError):
        return None


def block_lead_as_ai(phone_number: str, motivo: str, instance_id) -> bool:
    """
    Bloqueia um numero por suspeita de ser IA: marca ai_blocked (1 ano),
    cancela follow-ups, deleta evento do Calendar (se houver) e avisa a equipe.
    Nao envia resposta ao lead.
    """
    if not redis_client:
        return False

    from tools.manage_followups import cancel_followups

    prefix = redis_prefix(instance_id)

    # 1) Flag ai_blocked reaproveita o bloqueio ja respeitado pelo webhook
    redis_client.setex(
        f"{prefix}:ai_blocked:{phone_number}",
        AI_BLOCK_TTL_SECONDS,
        f"ia_detectada:{motivo[:180]}",
    )

    # 2) Follow-ups fora
    cancel_followups(phone_number, instance_id)

    # 3) Buffer de rajada limpo (evita disparar task pendente)
    redis_client.delete(f"{prefix}:burst:{phone_number}")
    redis_client.delete(f"{prefix}:burst_time:{phone_number}")

    # 4) Evento do Calendar (se havia)
    lead = get_lead_info(phone_number, instance_id)
    event_id = lead.get("event_id", "")
    nome = lead.get("nome", "")
    evento_info = ""
    if event_id:
        try:
            from tools.manage_calendar import deleta_evento
            res = deleta_evento(event_id)
            if res.get("success"):
                save_lead_info(phone_number, {"event_id": ""}, instance_id)
                evento_info = f" Evento {event_id} cancelado."
                print(f"[AI_BLOCK] Evento {event_id} deletado do Calendar")
            else:
                evento_info = f" (falha ao deletar evento {event_id}: {res})"
                print(f"[AI_BLOCK] Falha ao deletar evento: {res}")
        except Exception as e:
            evento_info = f" (erro ao deletar evento: {e})"
            print(f"[AI_BLOCK] Erro ao deletar evento: {e}")

    # 5) Alerta equipe (token da instância que detectou a IA)
    try:
        from tools.send_whatsapp import send_message
        texto = (
            "🚨 *IA DETECTADA — CONVERSA INTERROMPIDA*\n"
            f"Instância: {instance_id}\n"
            f"Número: {phone_number}\n"
            f"Nome: {nome or '(desconhecido)'}\n"
            f"Motivo: {motivo}\n"
            f"Ações: IA bloqueada por 1 ano, follow-ups cancelados.{evento_info}"
        )
        send_message(f"{OWNER_NUMBER}@s.whatsapp.net", texto, instance_id)
        print(f"[AI_BLOCK] Equipe alertada para {phone_number}")
    except Exception as e:
        print(f"[AI_BLOCK] Falha ao alertar equipe: {e}")

    print(f"[AI_BLOCK] Lead {phone_number} bloqueado. Motivo: {motivo}")
    return True


def block_lead_for_support(phone_number: str, instance_id) -> bool:
    """
    Bloqueia um número indefinidamente porque a Mya o direcionou ao SUPORTE SAI:
    marca ai_blocked sem TTL, cancela follow-ups, limpa buffer de rajada e avisa a equipe.
    Não cancela o evento do Calendar (lead pode ter reunião marcada).
    """
    if not redis_client:
        return False

    from tools.manage_followups import cancel_followups

    prefix = redis_prefix(instance_id)

    redis_client.set(f"{prefix}:ai_blocked:{phone_number}", "suporte_sai")

    cancel_followups(phone_number, instance_id)

    redis_client.delete(f"{prefix}:burst:{phone_number}")
    redis_client.delete(f"{prefix}:burst_time:{phone_number}")

    print(f"[SUPORTE_SAI] Lead {phone_number} bloqueado indefinidamente (direcionado ao suporte)")
    return True
