#!/usr/bin/env python3
"""Busca logs do serviço Swarm contendo termos específicos."""
import os
import sys
import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

PORTAINER_URL = os.getenv("PORTAINER_URL", "https://portainer.strategicai.com.br").rstrip("/")
PORTAINER_TOKEN = os.getenv("PORTAINER_TOKEN", "")
ENDPOINT_ID = 1
HEADERS = {"X-API-Key": PORTAINER_TOKEN}


def list_services():
    r = requests.get(f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/services",
                     headers=HEADERS, verify=False, timeout=15)
    r.raise_for_status()
    return r.json()


def service_logs(service_id, tail=500, since=None):
    url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/services/{service_id}/logs"
    params = {"stdout": "true", "stderr": "true", "tail": tail, "timestamps": "true"}
    if since:
        params["since"] = since
    r = requests.get(url, headers=HEADERS, params=params, verify=False, timeout=120, stream=True)
    r.raise_for_status()
    # Docker logs come as multiplexed stream with 8-byte header per chunk
    raw = r.content
    output, i = [], 0
    while i < len(raw):
        if i + 8 > len(raw):
            break
        size = int.from_bytes(raw[i+4:i+8], "big")
        chunk = raw[i+8:i+8+size]
        output.append(chunk.decode("utf-8", errors="replace"))
        i += 8 + size
    return "".join(output) if output else raw.decode("utf-8", errors="replace")


def main():
    if len(sys.argv) < 2:
        print("Uso: python fetch_logs.py <service_name> [grep_term1] [grep_term2]...")
        sys.exit(1)
    service_name = sys.argv[1]
    grep_terms = sys.argv[2:]

    services = list_services()
    target = None
    for s in services:
        if s.get("Spec", {}).get("Name") == service_name:
            target = s
            break
    if not target:
        print(f"Serviço não encontrado: {service_name}")
        print("Serviços disponíveis:")
        for s in services:
            print(f"  {s.get('Spec', {}).get('Name')}")
        sys.exit(2)

    # since: pega do env SINCE_HOURS (default 24h)
    import time as _t
    hours = int(os.getenv("SINCE_HOURS", "24"))
    since = int(_t.time()) - hours*3600
    print(f"[INFO] Buscando logs de {service_name} (id={target['ID'][:12]}), since={hours}h atrás")
    logs = service_logs(target["ID"], tail=20000, since=since)

    if not grep_terms:
        print(logs)
        return

    print(f"[INFO] Filtrando linhas com qualquer um de: {grep_terms}")
    matched = []
    for line in logs.splitlines():
        if any(t in line for t in grep_terms):
            matched.append(line)
    print(f"[INFO] {len(matched)} linhas matchadas:\n")
    for line in matched:
        print(line)


if __name__ == "__main__":
    main()
