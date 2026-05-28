#!/usr/bin/env python3
"""Forca resposta do bot para leads que ficaram travados, alimentando Redis e
publicando na fila RabbitMQ.

Para cada lead:
  1. Limpa ai_blocked (se existir)
  2. Salva no historico Redis a mensagem de disparo como 'ai' (se ainda nao existir)
  3. Publica na fila uma mensagem 'human' combinando todas as mensagens do lead

Executar DENTRO do container mya-disparo_api (tem REDIS_URL e RABBITMQ_*).
"""
import os
import json
import pika
import redis

LEADS = [
    {
        "instance_id": "3",
        "phone": "5511978844746",
        "name": "Areia Racket Club",
        "disparo": None,
        "lead_text": "Boa tarde, tudo bem ? Como posso te ajudar ?",
    },
]


def main():
    r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    r.ping()

    rmq_creds = pika.PlainCredentials(
        os.getenv("RABBITMQ_USER", "guest"),
        os.getenv("RABBITMQ_PASS", "guest"),
    )
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
        virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
        credentials=rmq_creds,
    ))
    channel = connection.channel()
    channel.queue_declare(queue="mya_disparo", durable=True)

    for lead in LEADS:
        phone = lead["phone"]
        instance_id = lead["instance_id"]
        prefix = f"disparo:{instance_id}"
        print(f"\n===== {lead['name']} ({phone}) — instance={instance_id} =====")

        # 1) Limpa ai_blocked
        block_key = f"{prefix}:ai_blocked:{phone}"
        if r.exists(block_key):
            val = r.get(block_key)
            r.delete(block_key)
            print(f"  [BLOCK] Deletado ai_blocked (era: {val!r})")
        else:
            print(f"  [BLOCK] Sem ai_blocked ativo")

        # 2) Garante disparo no historico (so se lead["disparo"] for fornecido)
        hist_key = f"{prefix}:history:{phone}"
        existing_len = r.llen(hist_key)
        if existing_len == 0 and lead.get("disparo"):
            disparo_msg = {"type": "ai", "data": {"content": lead["disparo"]}}
            r.rpush(hist_key, json.dumps(disparo_msg, ensure_ascii=False))
            print(f"  [HIST] Disparo inicial salvo no historico")
        else:
            print(f"  [HIST] Historico ja tinha {existing_len} msg(s) — pulando insercao do disparo")

        # 3) Publica na fila
        payload = {
            "EventType": "messages",
            "message": {
                "fromMe": False,
                "chatid": f"{phone}@s.whatsapp.net",
                "sender_pn": phone,
                "senderName": lead["name"],
                "text": lead["lead_text"],
            },
            "_instance_id": instance_id,
        }
        channel.basic_publish(
            exchange="",
            routing_key="mya_disparo",
            body=json.dumps(payload, ensure_ascii=False),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        print(f"  [QUEUE] Mensagem publicada na fila mya_disparo")

    connection.close()
    print("\n===== FIM =====")


if __name__ == "__main__":
    main()
