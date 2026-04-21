import requests
from dotenv import load_dotenv

from config.instances import UAZAPI_URL, get_token, ALERT_GROUP_TOKEN, ALERT_GROUP_ID

load_dotenv()


def send_message(phone_number: str, text: str, instance_id) -> bool:
    """
    Envia uma mensagem de texto via WhatsApp usando o token da instância informada.
    """
    try:
        token = get_token(instance_id)
    except ValueError as e:
        print(f"Erro: {e}")
        return False

    endpoint = f"{UAZAPI_URL}/send/text"
    headers = {"token": token, "Content-Type": "application/json"}
    payload = {
        "number": phone_number,
        "text": text,
        "delay": 4000,
        "track_source": "IA",
    }

    try:
        response = requests.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "SUCCESS" or response.status_code in [200, 201]:
            return True
        print(f"Erro da API [inst {instance_id}]: {data}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar mensagem [inst {instance_id}]: {e}")
        return False


def send_group_alert(text: str, group_id: str = ALERT_GROUP_ID) -> bool:
    """
    Envia mensagem ao grupo de alertas usando o token dedicado (UAZAPI_ALERT_GROUP_TOKEN).
    Usado para alertas consolidados de todas as instâncias (ex.: lead_agendou).
    """
    if not ALERT_GROUP_TOKEN:
        print("Erro: UAZAPI_ALERT_GROUP_TOKEN não configurado")
        return False

    endpoint = f"{UAZAPI_URL}/send/text"
    headers = {"token": ALERT_GROUP_TOKEN, "Content-Type": "application/json"}
    payload = {
        "number": group_id,
        "text": text,
        "delay": 0,
        "track_source": "IA_alert",
    }

    try:
        response = requests.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        return response.status_code in [200, 201]
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar alerta de grupo: {e}")
        return False
