"""Sender via Meta WhatsApp Cloud API (graph.facebook.com).

Espelha a interface de tools/send_whatsapp.py e tools/send_media.py: recebe um
phone_number "5511...@s.whatsapp.net" (formato UAZAPI interno) e instance_id;
faz o stripping para o formato E.164 esperado pela Graph API.
"""
import requests

from config.instances import META_GRAPH_VERSION, get_meta_config


def _normalize_to(phone_number: str) -> str:
    """Aceita '5511...@s.whatsapp.net' ou '5511...' e devolve só dígitos."""
    return phone_number.split("@")[0].lstrip("+")


def _post(instance_id, payload: dict) -> bool:
    try:
        cfg = get_meta_config(instance_id)
    except ValueError as e:
        print(f"Erro Meta: {e}")
        return False

    url = f"https://graph.facebook.com/{META_GRAPH_VERSION}/{cfg['phone_number_id']}/messages"
    headers = {
        "Authorization": f"Bearer {cfg['access_token']}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code in (200, 201):
            return True
        print(f"Erro Meta API [inst {instance_id}] {response.status_code}: {response.text[:300]}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Erro ao chamar Meta API [inst {instance_id}]: {e}")
        return False


def send_text(phone_number: str, text: str, instance_id) -> bool:
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(phone_number),
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    return _post(instance_id, payload)


def send_pdf(phone_number: str, file_url: str, filename: str, instance_id) -> bool:
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(phone_number),
        "type": "document",
        "document": {"link": file_url, "filename": filename},
    }
    return _post(instance_id, payload)


def send_image(phone_number: str, image_url: str, instance_id) -> bool:
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(phone_number),
        "type": "image",
        "image": {"link": image_url},
    }
    return _post(instance_id, payload)
