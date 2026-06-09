import os
import requests
from dotenv import load_dotenv

from tools.manage_leads import get_lead_info, save_lead_info

load_dotenv()

CRM_API_URL = os.getenv(
    "CRM_API_URL",
    "https://crm-basico.strategicai.com.br/api/integrations/lead",
)
CRM_API_KEY = os.getenv(
    "CRM_API_KEY",
    "4tG&9kP2#mL7xR5@zQ8sW1$nB4jH6fD9vY0uI3oO7pA1sS5dD8fF2gG4hH6jJ0kL",
)
CRM_OWNER_ID = os.getenv("CRM_OWNER_ID", "53cd0aa1-cad2-4bf1-aec8-d4497e13a066")

# --- SAI Comercial (pipeline de vendas). Auth via x-ingest-secret do AssistantBot. ---
SAI_LEADS_URL = os.getenv(
    "SAI_LEADS_URL",
    "https://comercial.strategicai.com.br/api/integrations/leads",
)
SAI_INGEST_SECRET = os.getenv("SAI_INGEST_SECRET", "")
# Key da etapa "Reunião agendada" no funil do tenant. Em plano-completo é "PROPOSTA"
# (legado renomeado). Configurável por env para outros tenants.
SAI_STAGE_KEY_MEETING_SCHEDULED = os.getenv("SAI_STAGE_KEY_MEETING_SCHEDULED", "PROPOSTA")


def send_lead_to_crm(phone: str, name: str, company: str, instance_id) -> dict:
    """Envia lead interessado ao CRM Básico. Chama apenas 1 vez por telefone/instância."""
    info = get_lead_info(phone, instance_id)
    if info.get("crm_sent"):
        print(f"[CRM-API] Lead {phone} já enviado anteriormente [inst {instance_id}] — pulando")
        return {"skipped": True}

    payload = {
        "name": name or "",
        "phone": phone,
        "company": company or "",
        "ownerId": CRM_OWNER_ID,
        "origin": "Disparos",
    }
    headers = {
        "X-Api-Key": CRM_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(CRM_API_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code in (200, 201):
            save_lead_info(phone, {"crm_sent": True}, instance_id)
            print(f"[CRM-API] ✅ Lead {phone} enviado [inst {instance_id}] ({resp.status_code})")
            return {"success": True, "status": resp.status_code}
        print(f"[CRM-API] ❌ Falha {resp.status_code}: {resp.text[:200]}")
        return {"success": False, "status": resp.status_code, "body": resp.text[:200]}
    except Exception as e:
        print(f"[CRM-API] ❌ Erro de rede: {e}")
        return {"success": False, "error": str(e)}


def mark_meeting_scheduled(phone: str, name: str, company: str, instance_id, meeting_at: str = "") -> dict:
    """Cria/move o card do lead para a etapa "Reunião agendada" no pipeline do SAI Comercial.

    Idempotente: o endpoint /api/integrations/leads faz upsert do Contact/Client/Deal
    e move o deal se já existir. Marca crm_meeting_sent no Redis para não repetir.

    meeting_at: data/hora no formato "YYYY-MM-DD HH:MM" (alimenta a nota automática).
    """
    info = get_lead_info(phone, instance_id)
    if info.get("crm_meeting_sent"):
        print(f"[SAI-CRM] Lead {phone} já marcado como reunião agendada [inst {instance_id}] — pulando")
        return {"skipped": True}

    if not SAI_INGEST_SECRET:
        print("[SAI-CRM] ❌ SAI_INGEST_SECRET ausente — pulando envio de reunião")
        return {"success": False, "error": "missing_secret"}

    payload = {
        "name": name or "",
        "phone": phone,
        "company": company or "",
        "origin": "Mya Disparo",
        "stageKey": SAI_STAGE_KEY_MEETING_SCHEDULED,
        "niche": info.get("nicho") or "",
        "resumo": info.get("resumo") or "",
        "meetingAt": meeting_at or "",
    }
    headers = {
        "x-ingest-secret": SAI_INGEST_SECRET,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(SAI_LEADS_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code in (200, 201):
            save_lead_info(phone, {"crm_meeting_sent": True}, instance_id)
            print(f"[SAI-CRM] ✅ Reunião agendada marcada para {phone} [inst {instance_id}] ({resp.status_code})")
            return {"success": True, "status": resp.status_code}
        print(f"[SAI-CRM] ❌ Falha ao marcar reunião {resp.status_code}: {resp.text[:200]}")
        return {"success": False, "status": resp.status_code, "body": resp.text[:200]}
    except Exception as e:
        print(f"[SAI-CRM] ❌ Erro de rede ao marcar reunião: {e}")
        return {"success": False, "error": str(e)}
