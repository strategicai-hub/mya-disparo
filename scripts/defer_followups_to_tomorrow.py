#!/usr/bin/env python3
"""Adia para amanhã (8h-18h SP) os follow-ups de um lead que cairiam HOJE.

Uso (dentro do container ou via run_remote_defer):
    python defer_followups_to_tomorrow.py <phone> <instance_id> [--apply]

Sem --apply: dry-run (só mostra o que faria).
"""
import os
import sys
import json
import time
import random
import redis
from datetime import datetime, timezone, timedelta

SP = timezone(timedelta(hours=-3))


def _skip_weekend(dt: datetime) -> datetime:
    wd = dt.weekday()
    if wd == 5:
        dt = dt + timedelta(days=2)
    elif wd == 6:
        dt = dt + timedelta(days=1)
    return dt


def main():
    args = [a for a in sys.argv[1:] if a]
    apply = "--apply" in args
    args = [a for a in args if a != "--apply"]
    if len(args) < 2:
        print("Uso: python defer_followups_to_tomorrow.py <phone> <instance_id> [--apply]")
        sys.exit(1)
    phone, instance_id = args[0], args[1]

    r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    r.ping()

    prefix = f"disparo:{instance_id}"
    followups_key = f"{prefix}:followups"
    members_key = f"{prefix}:followup:members:{phone}"

    now = datetime.now(SP)
    # Fronteira: tudo agendado antes de amanhã 08:00 SP é considerado "hoje"
    tomorrow_8 = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    boundary_ts = tomorrow_8.timestamp()

    members = sorted(r.smembers(members_key))
    if not members:
        print(f"[INFO] Nenhum follow-up ativo para {phone} [inst {instance_id}].")
        return

    print(f"[NOW] {now.strftime('%d/%m %H:%M:%S')} SP | boundary (amanhã 08:00) = {tomorrow_8.strftime('%d/%m %H:%M')}")
    print(f"[INFO] {len(members)} follow-up(s) no set de {phone}:\n")

    # Ordena membros por step para dar slots crescentes amanhã
    parsed = []
    for raw in members:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            item = {}
        score = r.zscore(followups_key, raw)
        parsed.append((item.get("step", 0), score, raw, item))
    parsed.sort(key=lambda x: (x[0], x[1] or 0))

    # Slots crescentes amanhã: step menor mais cedo. Distribui pela manhã/tarde.
    slot_idx = 0
    slot_hours = [9, 11, 14, 16]

    for step, score, raw, item in parsed:
        when = datetime.fromtimestamp(score, tz=SP) if score is not None else None
        when_str = when.strftime('%d/%m %H:%M') if when else "?"
        kind = item.get("kind") or item.get("type")
        if score is None:
            print(f"  step {step} ({kind}): SEM score no zset — pulando")
            continue
        if score >= boundary_ts:
            print(f"  step {step} ({kind}): {when_str} — JÁ é amanhã+, mantém")
            continue
        # Cai hoje → reagenda pra amanhã
        h = slot_hours[min(slot_idx, len(slot_hours) - 1)]
        slot_idx += 1
        new_dt = (now + timedelta(days=1)).replace(
            hour=h, minute=random.randint(0, 59), second=random.randint(0, 59), microsecond=0
        )
        new_dt = _skip_weekend(new_dt)
        new_ts = new_dt.timestamp()
        print(f"  step {step} ({kind}): {when_str} -> {new_dt.strftime('%d/%m %H:%M')} {'[APLICADO]' if apply else '[dry-run]'}")
        if apply:
            r.zadd(followups_key, {raw: new_ts})

    if not apply:
        print("\n[DRY-RUN] Nada alterado. Rode com --apply para efetivar.")
    else:
        print("\n[OK] Follow-ups de hoje adiados para amanhã.")


if __name__ == "__main__":
    main()
