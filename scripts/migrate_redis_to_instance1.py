#!/usr/bin/env python3
"""
Migração one-shot: renomeia chaves Redis do formato antigo (disparo:*) para o
formato multi-instância da instância 1 (disparo:1:*).

Antes de rodar esta migração, coloque o serviço mya-disparo em "pause" ou garanta
que não há tráfego chegando (pode migrar tudo por cima de tráfego em curso mas corre
risco de uma chave ser criada novamente no prefixo antigo logo após o RENAME).

Uso:
    python scripts/migrate_redis_to_instance1.py --dry-run   # só lista o que faria
    python scripts/migrate_redis_to_instance1.py             # executa
"""
import os
import sys
import argparse

import redis
from dotenv import load_dotenv

load_dotenv()

OLD_PREFIX = "disparo:"
KNOWN_INSTANCES = {"1", "2", "3"}  # chaves que já começam com estes IDs são puladas
NEW_PREFIX_FOR_OLD_DATA = "disparo:1:"


def already_migrated(key: str) -> bool:
    """True se a chave já está no formato multi-instância."""
    # Formato: disparo:<id>:... — checa segundo segmento
    parts = key.split(":", 2)
    if len(parts) < 2:
        return False
    return parts[1] in KNOWN_INSTANCES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Apenas lista o que seria renomeado")
    parser.add_argument("--yes", action="store_true", help="Não pede confirmação")
    args = parser.parse_args()

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        print("ERRO: REDIS_URL não configurado no ambiente/.env")
        sys.exit(1)

    r = redis.Redis.from_url(redis_url, decode_responses=True)
    r.ping()
    print(f"[OK] Conectado ao Redis")

    to_rename = []
    collisions = []
    already_skipped = 0

    for key in r.scan_iter(match=f"{OLD_PREFIX}*", count=500):
        if already_migrated(key):
            already_skipped += 1
            continue

        # Remove prefixo antigo e aplica o novo
        suffix = key[len(OLD_PREFIX):]  # ex.: "history:5511...", "followups", "lead:5511..."
        new_key = f"{NEW_PREFIX_FOR_OLD_DATA}{suffix}"

        # Proteção contra colisão: se new_key já existe, sinaliza (não sobrescreve cegamente)
        if r.exists(new_key):
            collisions.append((key, new_key))
        else:
            to_rename.append((key, new_key))

    print(f"\n[INFO] Chaves já no formato novo (puladas): {already_skipped}")
    print(f"[INFO] Chaves a renomear: {len(to_rename)}")
    print(f"[INFO] Colisões (destino já existe, NÃO serão tocadas): {len(collisions)}")

    if collisions:
        print("\n[ALERTA] Colisões detectadas — investigue antes de forçar migração:")
        for old, new in collisions[:20]:
            print(f"  {old}  →  {new}  (destino já existe)")
        if len(collisions) > 20:
            print(f"  ... e mais {len(collisions) - 20}")

    if args.dry_run:
        print("\n[DRY-RUN] Amostra do que seria renomeado (até 20):")
        for old, new in to_rename[:20]:
            print(f"  {old}  →  {new}")
        print("\n[DRY-RUN] Nenhuma alteração foi feita. Rode sem --dry-run para executar.")
        return

    if not to_rename:
        print("\n[OK] Nada a migrar.")
        return

    if not args.yes:
        resp = input(f"\nConfirma renomear {len(to_rename)} chaves? [digite 'SIM' para confirmar]: ")
        if resp.strip() != "SIM":
            print("Cancelado.")
            return

    renamed = 0
    errors = []
    for old, new in to_rename:
        try:
            r.rename(old, new)
            renamed += 1
        except redis.exceptions.ResponseError as e:
            errors.append((old, new, str(e)))

    print(f"\n[OK] Renomeadas: {renamed}")
    if errors:
        print(f"[ERRO] Falhas: {len(errors)}")
        for old, new, err in errors[:20]:
            print(f"  {old} → {new}: {err}")


if __name__ == "__main__":
    main()
