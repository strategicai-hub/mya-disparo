import requests
from dotenv import load_dotenv

from config.instances import UAZAPI_URL, get_token

load_dotenv()


def send_pdf(phone_number: str, filename: str, instance_id) -> bool:
    """
    Envia um PDF público via UAZAPI usando o token da instância informada.
    """
    try:
        token = get_token(instance_id)
    except ValueError as e:
        print(f"Erro: {e}")
        return False

    url = f"{UAZAPI_URL}/send/media"
    headers = {"Content-Type": "application/json", "token": token}
    url_public = "https://webhook-whatsapp.strategicai.com.br/mya-disparo/apresentacao"

    payload = {
        "number": phone_number,
        "type": "document",
        "file": url_public,
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


def send_image(phone_number: str, image_url: str, instance_id) -> bool:
    """Envia uma imagem via UAZAPI usando o token da instância informada."""
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
