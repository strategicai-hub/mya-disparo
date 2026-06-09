#!/usr/bin/env python3
"""Roda scripts/defer_followups_to_tomorrow.py no container mya-disparo_api via Portainer exec.

Uso:
    python scripts/run_remote_defer.py <phone> <instance_id> [--apply]
"""
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
SERVICE_NAME = "mya-disparo_api"

HEADERS = {"X-API-Key": PORTAINER_TOKEN, "Content-Type": "application/json"}


def find_container_id():
    url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/containers/json?all=0"
    r = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    r.raise_for_status()
    for c in r.json():
        labels = c.get("Labels", {}) or {}
        if labels.get("com.docker.swarm.service.name", "") == SERVICE_NAME:
            return c["Id"]
    raise RuntimeError(f"Nenhum container running para {SERVICE_NAME}")


def exec_command(container_id, cmd_list):
    create_url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/containers/{container_id}/exec"
    r = requests.post(create_url, headers=HEADERS, json={
        "AttachStdout": True, "AttachStderr": True, "Tty": False, "Cmd": cmd_list,
    }, verify=False, timeout=15)
    r.raise_for_status()
    exec_id = r.json()["Id"]
    start_url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/exec/{exec_id}/start"
    r = requests.post(start_url, headers=HEADERS, json={"Detach": False, "Tty": False},
                      verify=False, timeout=180, stream=True)
    r.raise_for_status()
    raw = r.content
    output, i = [], 0
    while i < len(raw):
        if i + 8 > len(raw):
            break
        size = int.from_bytes(raw[i+4:i+8], "big")
        output.append(raw[i+8:i+8+size].decode("utf-8", errors="replace"))
        i += 8 + size
    return "".join(output) if output else raw.decode("utf-8", errors="replace")


def main():
    passthru = [a for a in sys.argv[1:] if a.strip()]
    if len(passthru) < 2:
        print("Uso: python run_remote_defer.py <phone> <instance_id> [--apply]")
        sys.exit(1)

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "defer_followups_to_tomorrow.py"), "r", encoding="utf-8") as f:
        script_src = f.read()

    container_id = find_container_id()
    print(f"[1/2] Container: {container_id[:12]}")
    print(f"[2/2] Executando defer_followups_to_tomorrow.py args={passthru}...\n")
    out = exec_command(container_id, ["python", "-c", script_src] + passthru)
    print(out)


if __name__ == "__main__":
    main()
