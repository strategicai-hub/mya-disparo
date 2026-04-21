#!/usr/bin/env python3
"""
Atualiza as Environment variables do stack mya-disparo (Portainer stack id 48)
e re-puxa o docker-compose.yml do Git, incrementando a imagem.

As envs saem do arquivo local `stack.env` (gitignored). Use `stack.env.example`
como template.

Credenciais Portainer saem do `.env` (PORTAINER_URL, PORTAINER_TOKEN).

Uso:
    python scripts/update_stack.py                    # executa
    python scripts/update_stack.py --dry-run          # apenas mostra o que enviaria
    python scripts/update_stack.py --no-pull          # redeploy sem pullImage
"""
import os
import sys
import argparse
import json

import requests
import urllib3
from dotenv import dotenv_values, load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STACK_ID = 48
STACK_NAME = "mya-disparo"
ENDPOINT_ID = 1

# Envs que DEVEM estar preenchidas para o sistema subir em multi-instância
REQUIRED = [
    "REDIS_URL",
    "RABBITMQ_HOST", "RABBITMQ_USER", "RABBITMQ_PASS", "RABBITMQ_VHOST",
    "UAZAPI_URL",
    "UAZAPI_INSTANCE_1", "UAZAPI_TOKEN_1",
    "UAZAPI_INSTANCE_2", "UAZAPI_TOKEN_2",
    "UAZAPI_INSTANCE_3", "UAZAPI_TOKEN_3",
    "UAZAPI_ALERT_GROUP_TOKEN", "UAZAPI_ALERT_GROUP_ID",
    "OWNER_NUMBER",
    "LLM_API_KEY",
]


def mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "***"
    return f"{v[:4]}...{v[-4:]}"


def load_stack_envs(path: str) -> dict:
    if not os.path.exists(path):
        print(f"ERRO: {path} não existe. Copie stack.env.example para stack.env e preencha.")
        sys.exit(1)
    envs = dotenv_values(path)
    envs = {k: (v or "") for k, v in envs.items()}
    return envs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-pull", action="store_true", help="Não força pull de nova imagem")
    parser.add_argument("--stack-env", default="stack.env", help="Caminho do arquivo com as envs do stack")
    args = parser.parse_args()

    load_dotenv()
    portainer_url = os.getenv("PORTAINER_URL", "").rstrip("/")
    portainer_token = os.getenv("PORTAINER_TOKEN", "")
    if not portainer_url or not portainer_token:
        print("ERRO: PORTAINER_URL e PORTAINER_TOKEN precisam estar no .env")
        sys.exit(1)

    envs = load_stack_envs(args.stack_env)

    missing = [k for k in REQUIRED if not envs.get(k)]
    if missing:
        print(f"ERRO: envs obrigatórias vazias em {args.stack_env}:")
        for k in missing:
            print(f"  - {k}")
        sys.exit(1)

    env_list = [{"name": k, "value": v} for k, v in envs.items() if v != ""]

    print(f"[INFO] Stack: {STACK_NAME} (id {STACK_ID})")
    print(f"[INFO] Portainer: {portainer_url}")
    print(f"[INFO] Envs a enviar: {len(env_list)}")
    for e in env_list:
        print(f"  {e['name']} = {mask(e['value'])}")

    if args.dry_run:
        print("\n[DRY-RUN] Nada foi enviado.")
        return

    headers = {"X-API-Key": portainer_token, "Content-Type": "application/json"}

    # Endpoint correto para stack linkado ao Git: /git/redeploy
    url = f"{portainer_url}/api/stacks/{STACK_ID}/git/redeploy?endpointId={ENDPOINT_ID}"
    body = {
        "env": env_list,
        "prune": False,
        "pullImage": not args.no_pull,
        "repositoryReferenceName": "refs/heads/main",
        "repositoryAuthentication": False,
    }

    print(f"\n[PUT] {url}")
    print(f"      pullImage={body['pullImage']}, prune={body['prune']}")
    resp = requests.put(url, headers=headers, json=body, verify=False, timeout=180)
    print(f"[HTTP] {resp.status_code}")
    if resp.status_code >= 300:
        print(f"[ERRO] {resp.text[:800]}")
        sys.exit(1)

    try:
        data = resp.json()
        print(f"[OK] Stack atualizado. Status: {data.get('Status')}  UpdateDate: {data.get('UpdateDate')}")
    except Exception:
        print(f"[OK] Resposta: {resp.text[:300]}")

    print("\n[POS] Verificando envs nos services...")
    svcs = requests.get(
        f"{portainer_url}/api/endpoints/{ENDPOINT_ID}/docker/services",
        headers=headers, verify=False,
    ).json()
    for s in svcs:
        name = s.get("Spec", {}).get("Name", "")
        if not name.startswith("mya-disparo_"):
            continue
        svc_env = s.get("Spec", {}).get("TaskTemplate", {}).get("ContainerSpec", {}).get("Env", [])
        keys = sorted([e.split("=", 1)[0] for e in svc_env])
        print(f"  {name}: {len(keys)} envs — {', '.join(keys)}")


if __name__ == "__main__":
    main()
