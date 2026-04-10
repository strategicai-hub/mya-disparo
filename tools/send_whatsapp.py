import os
import requests
import urllib.parse
from dotenv import load_dotenv

# Carrega as variáveis de ambiente
load_dotenv()

UAZAPI_URL = os.getenv('UAZAPI_URL')
UAZAPI_INSTANCE = os.getenv('UAZAPI_INSTANCE')
UAZAPI_TOKEN = os.getenv('UAZAPI_TOKEN')

def send_message(phone_number, text):
    """
    Envia uma mensagem de texto via WhatsApp usando a UAZAPI.
    Retorna True se sucesso, False caso contrário.
    """
    if not UAZAPI_URL or not UAZAPI_INSTANCE or not UAZAPI_TOKEN:
        print("Erro: Credenciais da UAZAPI não configuradas no .env")
        return False

    safe_instance = urllib.parse.quote(UAZAPI_INSTANCE)
    endpoint = f"{UAZAPI_URL}/send/text"

    headers = {
        "token": UAZAPI_TOKEN,
        "Content-Type": "application/json"
    }

    payload = {
        "number": phone_number,
        "text": text,
        "delay": 4000, # Delay opcional de digitação (4s)
        "track_source": "IA"
    }

    try:
        response = requests.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json()
        if data.get("status") == "SUCCESS" or response.status_code in [200, 201]:
            return True
        else:
            print(f"Erro da API: {data}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar mensagem: {e}")
        return False
