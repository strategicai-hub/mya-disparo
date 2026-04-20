#!/usr/bin/env python3
"""
Executa o emergency_stop dentro do container mya-disparo_api em producao
via API do Portainer (docker exec). Nao precisa de redeploy.

Uso:
    python run_remote_stop.py 5513997957799 "IA detectada"
"""
import os
import sys
import json
import time

import requests
import urllib3

from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

PORTAINER_URL = os.getenv("PORTAINER_URL", "https://91.98.64.92:9443").rstrip("/")
PORTAINER_TOKEN = os.getenv("PORTAINER_TOKEN", "")
ENDPOINT_ID = 1
SERVICE_NAME = "mya-disparo_api"

if not PORTAINER_TOKEN:
    print("ERRO: PORTAINER_TOKEN nao encontrado no .env")
    sys.exit(1)

HEADERS = {"X-API-Key": PORTAINER_TOKEN, "Content-Type": "application/json"}


def find_container_id():
    """Acha o container running do servico mya-disparo_api."""
    url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/containers/json?all=0"
    r = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    r.raise_for_status()
    for c in r.json():
        labels = c.get("Labels", {}) or {}
        svc = labels.get("com.docker.swarm.service.name", "")
        if svc == SERVICE_NAME:
            return c["Id"]
    raise RuntimeError(f"Nenhum container running encontrado para o servico {SERVICE_NAME}")


def exec_command(container_id, cmd_list):
    """Roda um comando dentro do container e retorna stdout+stderr."""
    # 1) cria exec
    create_url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/containers/{container_id}/exec"
    create_body = {
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "Cmd": cmd_list,
    }
    r = requests.post(create_url, headers=HEADERS, json=create_body, verify=False, timeout=15)
    r.raise_for_status()
    exec_id = r.json()["Id"]

    # 2) start exec (detach=false para receber o output)
    start_url = f"{PORTAINER_URL}/api/endpoints/{ENDPOINT_ID}/docker/exec/{exec_id}/start"
    start_body = {"Detach": False, "Tty": False}
    r = requests.post(start_url, headers=HEADERS, json=start_body, verify=False, timeout=120, stream=True)
    r.raise_for_status()

    # Output vem como multiplexed stream (docker format). Para simplicidade, ignora header de 8 bytes.
    raw = r.content
    output = []
    i = 0
    while i < len(raw):
        if i + 8 > len(raw):
            break
        size = int.from_bytes(raw[i+4:i+8], "big")
        chunk = raw[i+8:i+8+size]
        output.append(chunk.decode("utf-8", errors="replace"))
        i += 8 + size
    if not output:
        # Fallback: decodifica cru
        return raw.decode("utf-8", errors="replace")
    return "".join(output)


def main():
    if len(sys.argv) < 2:
        print("Uso: python run_remote_stop.py <phone> [motivo]")
        sys.exit(1)
    phone = sys.argv[1].strip()
    motivo = sys.argv[2].strip() if len(sys.argv) > 2 else "IA detectada"

    print(f"[1/3] Procurando container de {SERVICE_NAME}...")
    container_id = find_container_id()
    print(f"      Container: {container_id[:12]}")

    # Script inline: faz tudo dentro do container (tem REDIS_URL, tools/, etc)
    inline = f"""
import os, json
import redis
KEY_PREFIX = "disparo"
phone = "{phone}"
motivo = "{motivo}"
r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
r.ping()
r.setex(f"{{KEY_PREFIX}}:ai_blocked:{{phone}}", 60*60*24*365, f"ia_detectada:{{motivo}}")
print(f"[OK] ai_blocked setado para {{phone}} (1 ano)")
members_key = f"{{KEY_PREFIX}}:followup:members:{{phone}}"
members = r.smembers(members_key)
followups_key = f"{{KEY_PREFIX}}:followups"
for m in members:
    r.zrem(followups_key, m)
if members:
    r.delete(members_key)
r.delete(f"{{KEY_PREFIX}}:followup:active:{{phone}}")
r.delete(f"{{KEY_PREFIX}}:followup:cycle:{{phone}}")
r.delete(f"{{KEY_PREFIX}}:burst:{{phone}}")
r.delete(f"{{KEY_PREFIX}}:burst_time:{{phone}}")
print(f"[OK] follow-ups ({{len(members)}}) + burst limpos")
lead_raw = r.get(f"{{KEY_PREFIX}}:lead:{{phone}}")
event_id = ""; nome = ""
if lead_raw:
    try:
        lead = json.loads(lead_raw)
        event_id = lead.get("event_id", "") or ""
        nome = lead.get("nome", "") or ""
    except Exception as e:
        print(f"[AVISO] Falha ao parsear lead: {{e}}")
if event_id:
    try:
        from tools.manage_calendar import deleta_evento
        res = deleta_evento(event_id)
        if res.get("success"):
            print(f"[OK] Evento {{event_id}} deletado do Google Calendar")
            try:
                lead = json.loads(lead_raw); lead["event_id"] = ""
                r.set(f"{{KEY_PREFIX}}:lead:{{phone}}", json.dumps(lead, ensure_ascii=False))
            except Exception:
                pass
        else:
            print(f"[AVISO] deleta_evento falhou: {{res}}")
    except Exception as e:
        print(f"[AVISO] Erro ao deletar evento: {{e}}")
else:
    print(f"[INFO] Sem event_id ativo em {{phone}}")
try:
    from tools.send_whatsapp import send_message
    txt = (
        f"[ALERTA] IA DETECTADA - CONVERSA INTERROMPIDA\\n"
        f"Numero: {{phone}}\\n"
        f"Nome: {{nome or '(desconhecido)'}}\\n"
        f"Motivo: {{motivo}}\\n"
        f"Acoes: IA bloqueada 1 ano, follow-ups cancelados, evento removido (se havia)."
    )
    send_message("5511989887525@s.whatsapp.net", txt)
    print(f"[OK] Equipe alertada")
except Exception as e:
    print(f"[AVISO] Falha ao alertar equipe: {{e}}")
print(f"[DONE] lead {{phone}} bloqueado")
"""
    print(f"[2/3] Executando bloqueio inline no container (phone={phone})...")
    out = exec_command(container_id, ["python", "-c", inline])
    print(f"[3/3] Saida do container:\n{'-'*60}\n{out}\n{'-'*60}")


if __name__ == "__main__":
    main()
