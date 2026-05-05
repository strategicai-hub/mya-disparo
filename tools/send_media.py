import requests
from dotenv import load_dotenv

from config.instances import UAZAPI_URL, get_token, get_provider
from providers import meta as meta_provider

load_dotenv()

_PDF_PUBLIC_URL = "https://webhook-whatsapp.strategicai.com.br/mya-disparo/apresentacao"


def _send_pdf_via_uazapi(phone_number: str, filename: str, instance_id) -> bool:
    try:
        token = get_token(instance_id)
    except ValueError as e:
        print(f"Erro: {e}")
        return False

    url = f"{UAZAPI_URL}/send/media"
    headers = {"Content-Type": "application/json", "token": token}
    payload = {
        "number": phone_number,
        "type": "document",
        "file": _PDF_PUBLIC_URL,
        "docName": filename,
        "delay": 4000,
    }

    try:
        print(f"Disparando PDF [inst {instance_id}] para UAZAPI...")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print("PDF enviado com sucesso!")
        return True
    except Exception as e:
        print(f"Erro crítico ao enviar PDF [inst {instance_id}]: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Resposta bruta da UAZAPI: {response.text}")
        return False


def _send_image_via_uazapi(phone_number: str, image_url: str, instance_id) -> bool:
    try:
        token = get_token(instance_id)
    except ValueError as e:
        print(f"Erro: {e}")
        return False

    url = f"{UAZAPI_URL}/send/media"
    headers = {"Content-Type": "application/json", "token": token}
    payload = {
        "number": phone_number,
        "type": "image",
        "file": image_url,
        "delay": 4000,
    }

    try:
        print(f"Disparando imagem [inst {instance_id}] para UAZAPI...")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print("Imagem enviada com sucesso!")
        return True
    except Exception as e:
        print(f"Erro ao enviar imagem [inst {instance_id}]: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Resposta bruta da UAZAPI: {response.text}")
        return False


def send_pdf(phone_number: str, filename: str, instance_id) -> bool:
    """Envia PDF de apresentação via provider configurado para a instância."""
    if get_provider(instance_id) == "meta":
        return meta_provider.send_pdf(phone_number, _PDF_PUBLIC_URL, filename, instance_id)
    return _send_pdf_via_uazapi(phone_number, filename, instance_id)


def send_image(phone_number: str, image_url: str, instance_id) -> bool:
    """Envia imagem via provider configurado para a instância."""
    if get_provider(instance_id) == "meta":
        return meta_provider.send_image(phone_number, image_url, instance_id)
    return _send_image_via_uazapi(phone_number, image_url, instance_id)
