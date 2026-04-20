#!/usr/bin/env python3
"""Deleta o evento ativo de um lead via container worker (tem Google Calendar credentials)."""
import os, sys, json
import requests, urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

PORTAINER_URL = os.getenv("PORTAINER_URL").rstrip("/")
PORTAINER_TOKEN = os.getenv("PORTAINER_TOKEN")
ENDPOINT_ID = 1
SERVICE_NAME = "mya-disparo_worker"
HEADERS = {"X-API-Key": PORTAINER_TOKEN, "Content-Type": "application/json"}


def find_container_id():
    url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/containers/json?all=0"
    r = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    r.raise_for_status()
    for c in r.json():
        labels = c.get("Labels", {}) or {}
        if labels.get("com.docker.swarm.service.name", "") == SERVICE_NAME:
            return c["Id"]
    raise RuntimeError(f"container de {SERVICE_NAME} nao encontrado")


def exec_command(container_id, cmd_list):
    create_url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/containers/{container_id}/exec"
    r = requests.post(create_url, headers=HEADERS, json={"AttachStdout": True, "AttachStderr": True, "Tty": False, "Cmd": cmd_list}, verify=False, timeout=15)
    r.raise_for_status()
    exec_id = r.json()["Id"]
    start_url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/exec/{exec_id}/start"
    r = requests.post(start_url, headers=HEADERS, json={"Detach": False, "Tty": False}, verify=False, timeout=120)
    r.raise_for_status()
    raw = r.content
    output = []
    i = 0
    while i < len(raw):
        if i + 8 > len(raw): break
        size = int.from_bytes(raw[i+4:i+8], "big")
        chunk = raw[i+8:i+8+size]
        output.append(chunk.decode("utf-8", errors="replace"))
        i += 8 + size
    return "".join(output) or raw.decode("utf-8", errors="replace")


if __name__ == "__main__":
    phone = sys.argv[1].strip()
    container_id = find_container_id()
    print(f"container worker: {container_id[:12]}")

    inline = f"""
import os, json, redis
KEY = "disparo"
phone = "{phone}"
r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
lead_raw = r.get(f"{{KEY}}:lead:{{phone}}")
if not lead_raw:
    print("Sem lead no Redis"); raise SystemExit(0)
lead = json.loads(lead_raw)
event_id = lead.get("event_id", "")
print(f"event_id: '{{event_id}}'")
if not event_id:
    print("Sem event_id ativo"); raise SystemExit(0)
from tools.manage_calendar import deleta_evento
res = deleta_evento(event_id)
print(f"deleta_evento: {{res}}")
if res.get("success"):
    lead["event_id"] = ""
    r.set(f"{{KEY}}:lead:{{phone}}", json.dumps(lead, ensure_ascii=False))
    print("event_id limpo do CRM")
"""
    out = exec_command(container_id, ["python", "-c", inline])
    print(out)
