"""One-shot: envia follow-up contextual (+1h) para todos os contatos do tenant
plano-completo que receberam disparo via API Oficial Meta HOJE no SAI Comercial,
com jitter aleatório entre 2 e 6 minutos entre cada envio.

Uso (dentro do container mya-disparo_api ou worker):

    SAI_DB_URL='postgres://comercial:<pwd>@sai-comercial_db:5432/comercial' \\
    SAI_TENANT_ID='cmp5uvcel0000336zx122h85y' \\
    INSTANCE_ID=disparo \\
    DRY_RUN=0 \\
    python tools/bulk_followup_today.py

Variáveis:
- SAI_DB_URL: DSN postgres do SAI Comercial.
- SAI_TENANT_ID: id do tenant (plano-completo no caso).
- INSTANCE_ID: instance_id do mya-disparo a usar para o envio Meta (default: disparo).
- DRY_RUN: se "1", apenas imprime o que seria enviado (default: 0).
- JITTER_MIN_SECONDS / JITTER_MAX_SECONDS: limites do jitter (default: 120 / 360).
- SKIP_WITH_EVENT: se "1", pula leads que já têm event_id no Redis (default: 1).
- DATE_OVERRIDE: opcional, formato YYYY-MM-DD para usar como "hoje".

Comportamento por contato (na ordem):
1. Lê dados do lead do Redis (nome, nicho, resumo) e histórico.
2. Garante que `tenant_id` está memorizado em Redis (para o notify_outbound do
   provider Meta refletir no inbox do SAI via /api/chatbot/outbound).
3. Gera mensagem contextual via Gemini.
4. Envia via Meta API (provider meta da instância).
5. Salva no histórico do mya-disparo + marca timestamps + cancela follow-ups
   antigos e agenda os novos (+1h/fim da janela 24h) via schedule_meta_outbound_followups.
6. Sleep random entre JITTER_MIN_SECONDS e JITTER_MAX_SECONDS antes do próximo.
"""
from __future__ import annotations

import os
import sys
import time
import random
from datetime import datetime, timezone, timedelta

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None  # type: ignore
import json as _json
from dotenv import load_dotenv

load_dotenv()

# Imports do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.instances import get_provider, valid_instance  # noqa: E402
from tools.send_whatsapp import send_message  # noqa: E402
from tools.manage_history import save_message  # noqa: E402
from tools.manage_leads import (  # noqa: E402
    get_lead_info, mark_disparo_sent_now, mark_outbound_sent_now,
)
from tools.manage_followups import schedule_meta_outbound_followups  # noqa: E402
from tools.audit import remember_tenant  # noqa: E402
from tools.followup_llm import generate_followup_text  # noqa: E402

SAO_PAULO_TZ = timezone(timedelta(hours=-3))


def _today_utc_range(override: str | None = None) -> tuple[str, str]:
    """Retorna (inicio, fim) em UTC ISO para 'hoje' no calendário São Paulo.

    Como hoje no servidor (UTC) e em São Paulo podem divergir por 3h, usamos o
    calendário SP para definir o dia, depois convertemos para UTC.
    """
    if override:
        ref = datetime.strptime(override, "%Y-%m-%d").replace(tzinfo=SAO_PAULO_TZ)
    else:
        ref = datetime.now(SAO_PAULO_TZ)
    start_sp = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    end_sp = start_sp + timedelta(days=1)
    return (
        start_sp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        end_sp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _fetch_contacts(conn, tenant_id: str, start_utc: str, end_utc: str) -> list[dict]:
    """Retorna a lista de contatos disparoados hoje no tenant via API Oficial."""
    sql = """
        WITH out_today AS (
            SELECT c."contactId", MAX(m."createdAt") AS last_out
            FROM "Message" m
            JOIN "Conversation" c ON c.id = m."conversationId"
            JOIN "Contact" ct ON ct.id = c."contactId"
            WHERE m.direction = 'OUT'
              AND m.provider = 'OFFICIAL'
              AND m."createdAt" >= %s
              AND m."createdAt" <  %s
              AND ct."tenantId" = %s
            GROUP BY c."contactId"
        )
        SELECT ct.id      AS contact_id,
               ct."phoneE164" AS phone_e164,
               COALESCE(ct.name, '') AS name,
               o.last_out
          FROM out_today o
          JOIN "Contact" ct ON ct.id = o."contactId"
         ORDER BY o.last_out ASC;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (start_utc, end_utc, tenant_id))
        return list(cur.fetchall())


def _phone_digits(e164: str) -> str:
    return "".join(ch for ch in (e164 or "") if ch.isdigit())


def _human_or_machine_indicator(conn, contact_id: str) -> str:
    """Retorna 'auto-reply', 'humano', 'sem inbound' para anotar no log.
    Heurística simples baseada em tempo entre 1º outbound e 1º inbound do dia.
    """
    sql = """
      WITH first_out AS (
        SELECT MIN(m."createdAt") AS t FROM "Message" m
        JOIN "Conversation" c ON c.id = m."conversationId"
        WHERE c."contactId"=%s AND m.direction='OUT' AND m.provider='OFFICIAL'
      ),
      first_in AS (
        SELECT MIN(m."createdAt") AS t FROM "Message" m
        JOIN "Conversation" c ON c.id = m."conversationId"
        WHERE c."contactId"=%s AND m.direction='IN'  AND m.provider='OFFICIAL'
      )
      SELECT (SELECT t FROM first_out) AS out_at,
             (SELECT t FROM first_in)  AS in_at;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (contact_id, contact_id))
        row = cur.fetchone()
    if not row:
        return "sem inbound"
    out_at, in_at = row
    if not in_at:
        return "sem inbound"
    if not out_at:
        return "humano?"
    delta = (in_at - out_at).total_seconds()
    if delta < 30:
        return f"auto-reply ({delta:.0f}s)"
    return f"humano? ({delta:.0f}s)"


def main() -> int:
    tenant_id = os.getenv("SAI_TENANT_ID", "").strip()
    db_url = os.getenv("SAI_DB_URL", "").strip()
    contacts_json = os.getenv("CONTACTS_JSON", "").strip()
    instance_id = os.getenv("INSTANCE_ID", "disparo").strip()
    dry_run = os.getenv("DRY_RUN", "0").strip() in ("1", "true", "True", "yes")
    jitter_min = int(os.getenv("JITTER_MIN_SECONDS", "120"))
    jitter_max = int(os.getenv("JITTER_MAX_SECONDS", "360"))
    skip_with_event = os.getenv("SKIP_WITH_EVENT", "1").strip() in ("1", "true", "True", "yes")
    date_override = os.getenv("DATE_OVERRIDE", "").strip() or None

    if not tenant_id:
        print("ERRO: SAI_TENANT_ID não definido.")
        return 2
    if not contacts_json and not db_url:
        print("ERRO: defina CONTACTS_JSON (lista pronta) OU SAI_DB_URL (para consultar do DB).")
        return 2
    if not valid_instance(instance_id):
        print(f"ERRO: instance_id '{instance_id}' não configurado no mya-disparo.")
        return 2
    if get_provider(instance_id) != "meta":
        print(f"ERRO: instance_id '{instance_id}' não é provider 'meta'.")
        return 2

    print(f"[BULK] Tenant: {tenant_id}  |  Instance: {instance_id}  |  Dry-run: {dry_run}")

    conn = None
    try:
        if contacts_json:
            try:
                raw = _json.loads(contacts_json)
            except Exception as e:
                print(f"ERRO: CONTACTS_JSON inválido: {e}")
                return 2
            contacts = []
            for c in raw:
                contacts.append({
                    "contact_id": c.get("contact_id") or c.get("id") or "",
                    "phone_e164": c.get("phone_e164") or c.get("phone") or "",
                    "name": c.get("name") or "",
                    "last_out": c.get("last_out"),
                    "inbound_tag": c.get("inbound_tag") or "?",
                })
            print(f"[BULK] {len(contacts)} contatos via CONTACTS_JSON.")
        else:
            if psycopg2 is None:
                print("ERRO: psycopg2 não instalado neste runtime.")
                return 2
            start_utc, end_utc = _today_utc_range(date_override)
            print(f"[BULK] Janela UTC: {start_utc} → {end_utc}")
            conn = psycopg2.connect(db_url)
            contacts = _fetch_contacts(conn, tenant_id, start_utc, end_utc)
            print(f"[BULK] {len(contacts)} contatos encontrados via DB.")

        for idx, c in enumerate(contacts, start=1):
            phone_e164 = c["phone_e164"]
            phone = _phone_digits(phone_e164)
            name_sai = c.get("name") or ""
            if conn is not None and c.get("contact_id"):
                tag = _human_or_machine_indicator(conn, c["contact_id"])
            else:
                tag = c.get("inbound_tag") or "?"
            print(f"\n[BULK] ({idx}/{len(contacts)}) {phone_e164}  nome={name_sai!r}  inbound={tag}")

            # Garante que o tenant está memorizado pra refletir outbound no SAI
            try:
                remember_tenant(instance_id, phone, tenant_id)
            except Exception as e:
                print(f"  ! remember_tenant falhou (não crítico): {e}")

            lead_info = get_lead_info(phone, instance_id)
            if skip_with_event and lead_info.get("event_id"):
                print(f"  -> PULA: já tem event_id={lead_info['event_id']}")
                continue

            nome = lead_info.get("nome") or name_sai or ""
            nicho = lead_info.get("nicho") or ""
            resumo = lead_info.get("resumo") or ""

            # Texto contextual via Gemini
            fallback = (
                f"Oi {nome}, " if nome else "Oi, "
            ) + "imagino que seu dia esteja corrido. Conseguiu dar uma olhada na mensagem que te mandei?"
            text = generate_followup_text(
                phone, instance_id,
                nome=nome, nicho=nicho, resumo=resumo,
                fallback=fallback,
            )
            print(f"  texto: {text!r}")

            if dry_run:
                print("  [DRY_RUN] envio pulado.")
            else:
                # Marca o outbound ANTES do envio para a janela de 30s já valer
                # contra qualquer auto-resposta retornando rapidamente.
                mark_outbound_sent_now(phone, instance_id)
                mark_disparo_sent_now(phone, instance_id)

                ok = send_message(f"{phone}@s.whatsapp.net", text, instance_id)
                if ok:
                    save_message(phone, "ai", text, instance_id)
                    print("  ENVIADO OK.")
                    # Agenda novo ciclo (+1h/fim da janela 24h) a partir de agora
                    schedule_meta_outbound_followups(
                        phone, instance_id,
                        nome=nome, nicho=nicho, resumo=resumo,
                    )
                else:
                    print("  FALHA no envio.")

            if idx < len(contacts):
                delay = random.randint(jitter_min, jitter_max)
                eta = datetime.now(SAO_PAULO_TZ) + timedelta(seconds=delay)
                print(f"  ⏳ sleep {delay}s — próximo às {eta.strftime('%H:%M:%S')} SP")
                time.sleep(delay)

    finally:
        if conn is not None:
            conn.close()

    print("\n[BULK] Concluído.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
