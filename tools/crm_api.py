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


def send_lead_to_crm(phone: str, name: str, company: str) -> dict:
    """Envia lead interessado ao CRM Básico. Chama apenas 1 vez por telefone."""
    info = get_lead_info(phone)
    if info.get("crm_sent"):
        print(f"[CRM-API] Lead {phone} já enviado anteriormente — pulando")
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
            save_lead_info(phone, {"crm_sent": True})
            print(f"[CRM-API] ✅ Lead {phone} enviado ({resp.status_code})")
            return {"success": True, "status": resp.status_code}
        print(f"[CRM-API] ❌ Falha {resp.status_code}: {resp.text[:200]}")
        return {"success": False, "status": resp.status_code, "body": resp.text[:200]}
    except Exception as e:
        print(f"[CRM-API] ❌ Erro de rede: {e}")
        return {"success": False, "error": str(e)}
