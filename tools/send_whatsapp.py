import requests
from dotenv import load_dotenv

from config.instances import UAZAPI_URL, get_token, get_provider, ALERT_GROUP_TOKEN, ALERT_GROUP_ID, HUMAN_ALERT_NUMBERS
from providers import meta as meta_provider

load_dotenv()


def _send_via_uazapi(phone_number: str, text: str, instance_id) -> bool:
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


def send_message(phone_number: str, text: str, instance_id) -> bool:
    """Envia mensagem de texto via provider configurado para a instância (UAZAPI ou Meta)."""
    if get_provider(instance_id) == "meta":
        return meta_provider.send_text(phone_number, text, instance_id)
    return _send_via_uazapi(phone_number, text, instance_id)


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


def send_human_alert(text: str, numbers: list[str] | None = None) -> dict:
    """Envia o alerta de 'humano respondeu' para uma lista de números.

    Usa o ALERT_GROUP_TOKEN (número dedicado, separado das instâncias de disparo)
    para que o bloqueio frequente dos números de disparo não impeça o alerta.

    Retorna dict {numero: bool} indicando sucesso de cada envio.
    """
    targets = numbers if numbers is not None else HUMAN_ALERT_NUMBERS
    results = {}

    if not ALERT_GROUP_TOKEN:
        print("[HUMAN_ALERT] UAZAPI_ALERT_GROUP_TOKEN não configurado — alerta não enviado")
        return {n: False for n in targets}

    endpoint = f"{UAZAPI_URL}/send/text"
    headers = {"token": ALERT_GROUP_TOKEN, "Content-Type": "application/json"}

    for number in targets:
        if not number:
            continue
        payload = {
            "number": number,
            "text": text,
            "delay": 0,
            "track_source": "IA_alert",
        }
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=15)
            r.raise_for_status()
            results[number] = r.status_code in [200, 201]
        except requests.exceptions.RequestException as e:
            print(f"[HUMAN_ALERT] Falha ao enviar para {number}: {e}")
            results[number] = False

    return results
