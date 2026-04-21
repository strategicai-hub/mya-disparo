#!/usr/bin/env python3
"""
Emergency stop: bloqueia um numero permanentemente, cancela follow-ups,
deleta evento ativo no Google Calendar e alerta a equipe.

Uso local (via Portainer docker exec):
    python3 -c "..." -> ver helper run_via_portainer.py

Argumentos:
    --phone 5513997957799
    --motivo "IA detectada"
"""
import os
import sys
import json
import argparse

import redis

BLOCK_TTL_SECONDS = 60 * 60 * 24 * 365  # 1 ano


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", required=True, help="Numero do lead (ex: 5513997957799)")
    parser.add_argument("--motivo", default="IA detectada", help="Motivo do bloqueio")
    parser.add_argument("--instance", required=True, help="ID da instancia (1, 2 ou 3)")
    args = parser.parse_args()

    phone = args.phone.strip()
    motivo = args.motivo.strip()
    instance_id = args.instance.strip()
    KEY_PREFIX = f"disparo:{instance_id}"

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    r = redis.Redis.from_url(redis_url, decode_responses=True)
    r.ping()
    print(f"[OK] Conectado ao Redis")

    # 1) Bloqueia IA por 1 ano (reutiliza flag ai_blocked ja usada pelo api.py)
    r.setex(f"{KEY_PREFIX}:ai_blocked:{phone}", BLOCK_TTL_SECONDS, f"ia_detectada:{motivo}")
    print(f"[OK] ai_blocked setado para {phone} (TTL: 1 ano)")

    # 2) Cancela follow-ups
    members_key = f"{KEY_PREFIX}:followup:members:{phone}"
    members = r.smembers(members_key)
    followups_key = f"{KEY_PREFIX}:followups"
    if members:
        for m in members:
            r.zrem(followups_key, m)
        r.delete(members_key)
    r.delete(f"{KEY_PREFIX}:followup:active:{phone}")
    r.delete(f"{KEY_PREFIX}:followup:cycle:{phone}")
    print(f"[OK] {len(members)} follow-ups cancelados")

    # 3) Limpa burst/debounce pendente (se tiver mensagem na fila de rajada)
    r.delete(f"{KEY_PREFIX}:burst:{phone}")
    r.delete(f"{KEY_PREFIX}:burst_time:{phone}")
    print(f"[OK] buffer de rajada limpo")

    # 4) Pega dados do lead e tenta deletar evento do Google Calendar
    lead_raw = r.get(f"{KEY_PREFIX}:lead:{phone}")
    event_id = ""
    nome = ""
    if lead_raw:
        try:
            lead = json.loads(lead_raw)
            event_id = lead.get("event_id", "")
            nome = lead.get("nome", "")
        except Exception:
            pass

    if event_id:
        try:
            from tools.manage_calendar import deleta_evento
            resultado = deleta_evento(event_id)
            if resultado.get("success"):
                print(f"[OK] Evento {event_id} deletado do Google Calendar")
                # Limpa event_id do CRM
                try:
                    lead = json.loads(lead_raw) if lead_raw else {}
                    lead["event_id"] = ""
                    r.set(f"{KEY_PREFIX}:lead:{phone}", json.dumps(lead, ensure_ascii=False))
                except Exception:
                    pass
            else:
                print(f"[AVISO] Falha ao deletar evento: {resultado}")
        except Exception as e:
            print(f"[AVISO] Erro ao deletar evento: {e}")
    else:
        print(f"[INFO] Sem event_id ativo para {phone}")

    # 5) Alerta equipe
    try:
        from tools.send_whatsapp import send_message
        texto = (
            f"[ALERTA] IA DETECTADA — CONVERSA INTERROMPIDA\n"
            f"Numero: {phone}\n"
            f"Nome: {nome or '(desconhecido)'}\n"
            f"Motivo: {motivo}\n"
            f"Acoes: IA bloqueada 1 ano, follow-ups cancelados, evento removido (se havia)."
        )
        from config.instances import OWNER_NUMBER
        send_message(f"{OWNER_NUMBER}@s.whatsapp.net", texto, instance_id)
        print(f"[OK] Equipe alertada via WhatsApp")
    except Exception as e:
        print(f"[AVISO] Falha ao alertar equipe: {e}")

    print(f"\n[DONE] Lead {phone} bloqueado com sucesso.")


if __name__ == "__main__":
    main()
