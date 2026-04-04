import os
import requests
import base64
from dotenv import load_dotenv
import urllib.parse

load_dotenv()

UAZAPI_URL = os.getenv("UAZAPI_URL", "https://strategicai.uazapi.com")
UAZAPI_INSTANCE = os.getenv("UAZAPI_INSTANCE", "SAI TESTES")
UAZAPI_TOKEN = os.getenv("UAZAPI_TOKEN", "1234")

INSTANCE_CODADA = urllib.parse.quote(UAZAPI_INSTANCE)

def send_pdf(phone_number: str, filename: str):
    """
    Tenta enviar um PDF em formato Base64 para a UAZAPI usando o padrão genérico de envio de Media.
    """
    url = f"{UAZAPI_URL}/send/media"

    headers = {
        "Content-Type": "application/json",
        "token": UAZAPI_TOKEN # Padrão V2
    }

    url_public = "https://webhook-whatsapp.strategicai.com.br/mya-disparo/apresentacao"

    payload = {
        "number": phone_number,
        "type": "document",
        "file": url_public,
        "docName": filename,
        "delay": 4000
    }

    try:
        print(f"Disparando PDF para UAZAPI...")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print("PDF enviado com sucesso!")
        return True
    except Exception as e:
        print(f"Erro crítico ao enviar PDF: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Resposta bruta da UAZAPI: {response.text}")
        return False


def send_image(phone_number: str, image_url: str) -> bool:
    """Envia uma imagem via UAZAPI."""
    url = f"{UAZAPI_URL}/send/media"

    headers = {
        "Content-Type": "application/json",
        "token": UAZAPI_TOKEN
    }

    payload = {
        "number": phone_number,
        "type": "image",
        "file": image_url,
        "delay": 4000
    }

    try:
        print(f"Disparando imagem para UAZAPI...")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print("Imagem enviada com sucesso!")
        return True
    except Exception as e:
        print(f"Erro ao enviar imagem: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Resposta bruta da UAZAPI: {response.text}")
        return False
