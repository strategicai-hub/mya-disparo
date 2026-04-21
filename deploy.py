#!/usr/bin/env python3
"""
Deploy script para Mya Disparo via Portainer API.
Força a atualização dos serviços Swarm para usar a imagem `ghcr.io/strategicai-hub/mya-disparo:latest`.
"""
import os
import json
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

PORTAINER_URL = os.getenv("PORTAINER_URL", "https://91.98.64.92:9443").rstrip("/")
PORTAINER_TOKEN = os.getenv("PORTAINER_TOKEN", "")
SERVICES_TO_UPDATE = ["mya-disparo_api", "mya-disparo_worker", "mya-disparo_scheduler"]
IMAGE = "ghcr.io/strategicai-hub/mya-disparo:latest"
ENDPOINT_ID = 1  # Local endpoint em Portainer (assumido)

if not PORTAINER_TOKEN:
    print("❌ PORTAINER_TOKEN não encontrado em .env")
    exit(1)

# Desativar warnings de SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def api_get(path):
    """GET na API Portainer"""
    url = f"{PORTAINER_URL}{path}"
    headers = {"X-API-Key": PORTAINER_TOKEN}
    resp = requests.get(url, headers=headers, verify=False)
    resp.raise_for_status()
    return resp.json()

def api_post(path, data):
    """POST na API Portainer"""
    url = f"{PORTAINER_URL}{path}"
    headers = {"X-API-Key": PORTAINER_TOKEN, "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=data, verify=False)
    resp.raise_for_status()
    return resp.json() if resp.text else {}

def get_services():
    """Lista todos os serviços no endpoint"""
    return api_get(f"/api/endpoints/{ENDPOINT_ID}/docker/services")

def get_service_spec(service_id):
    """Obtem spec completo de um serviço"""
    return api_get(f"/api/endpoints/{ENDPOINT_ID}/docker/services/{service_id}")

def update_service(service_id, spec, version):
    """Força atualização de um serviço (incrementa ForceUpdate + sobrescreve Image para forçar repull)"""
    # Incrementa o ForceUpdate timestamp para forçar recriação
    spec["Spec"]["TaskTemplate"]["ForceUpdate"] = int(time.time() * 1e9)  # nanoseconds

    # SEMPRE sobrescreve o Image pela IMAGE canônica (sem sha256), garantindo
    # que Swarm resolva :latest fresco e que refs antigas (org/repo antigos) sejam substituídas.
    container_spec = spec["Spec"]["TaskTemplate"]["ContainerSpec"]
    img_antigo = container_spec.get("Image", "")
    container_spec["Image"] = IMAGE
    if img_antigo != IMAGE:
        print(f"  Image sobrescrito: {img_antigo} -> {IMAGE}")

    path = f"/api/endpoints/{ENDPOINT_ID}/docker/services/{service_id}/update?version={version}"
    print(f"  Enviando spec atualizado (version={version})...")
    api_post(path, spec["Spec"])

def main():
    print("=" * 60)
    print("[DEPLOY] Mya Disparo via Portainer")
    print("=" * 60)

    # 1. Listar serviços
    print("\n[1] Buscando serviços no Swarm...")
    try:
        services = get_services()
    except Exception as e:
        print(f"[ERRO] Falha ao listar serviços: {e}")
        exit(1)

    # 2. Encontrar os serviços que queremos atualizar
    service_ids = {}
    for svc in services:
        name = svc.get("Spec", {}).get("Name", "")
        svc_id = svc.get("ID", "")
        if name in SERVICES_TO_UPDATE:
            service_ids[name] = svc_id
            print(f"  [OK] Encontrado: {name} (ID: {svc_id[:12]}...)")

    missing = set(SERVICES_TO_UPDATE) - set(service_ids.keys())
    if missing:
        print(f"[AVISO] Serviços não encontrados: {missing}")

    # 3. Atualizar cada serviço
    print(f"\n[2] Atualizando serviços (ForceUpdate para nova imagem {IMAGE})...")
    for service_name, service_id in service_ids.items():
        print(f"\n  [ATUALIZAR] {service_name}...")
        try:
            spec = get_service_spec(service_id)
            version = spec.get("Version", {}).get("Index", 0)

            # Verifica imagem atual
            image_atual = spec.get("Spec", {}).get("TaskTemplate", {}).get("ContainerSpec", {}).get("Image", "")
            print(f"    Imagem atual: {image_atual}")
            print(f"    Imagem esperada: {IMAGE}")

            update_service(service_id, spec, version)
            print(f"    [OK] Serviço '{service_name}' atualizado com sucesso")
        except Exception as e:
            print(f"    [ERRO] Falha ao atualizar '{service_name}': {e}")

    # 4. Verificar saúde
    print(f"\n[3] Aguardando estabilização dos containers (15s)...")
    time.sleep(15)

    print(f"\n[4] Verificando saúde dos serviços...")
    try:
        services_novo = get_services()
        for svc in services_novo:
            name = svc.get("Spec", {}).get("Name", "")
            if name in SERVICES_TO_UPDATE:
                running = svc.get("ServiceStatus", {}).get("RunningTasks", 0)
                desired = svc.get("ServiceStatus", {}).get("DesiredTasks", 0)
                print(f"  {name}: {running}/{desired} tasks running")
    except Exception as e:
        print(f"  [AVISO] Falha ao verificar saúde: {e}")

    print(f"\n[5] Verificando health check do webhook...")
    try:
        resp = requests.get("https://webhook-whatsapp.strategicai.com.br/mya-disparo/", verify=False, timeout=5)
        if resp.status_code == 200:
            print(f"  [OK] Webhook respondendo HTTP 200")
        else:
            print(f"  [AVISO] Webhook respondendo HTTP {resp.status_code}")
    except Exception as e:
        print(f"  [ERRO] Webhook não respondeu: {e}")

    print("\n" + "=" * 60)
    print("[OK] Deploy concluído!")
    print("=" * 60)

if __name__ == "__main__":
    main()
