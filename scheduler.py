import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

from tools.manage_followups import (
    get_due_followups, remove_followup, reschedule_followup,
    has_active_followups, get_all_instance_ids,
)
from config.instances import OWNER_NUMBER
from tools.send_whatsapp import send_message
from tools.send_media import send_image
from tools.manage_history import save_message
from tools.manage_leads import mark_ai_sent_now

SAO_PAULO_TZ = timezone(timedelta(hours=-3))


def is_business_hours() -> bool:
    """Retorna True se estiver entre 8h e 19h horário de São Paulo."""
    now = datetime.now(SAO_PAULO_TZ)
    return 8 <= now.hour < 19


def next_business_morning() -> float:
    """Retorna timestamp da próxima manhã às 8h (São Paulo)."""
    now = datetime.now(SAO_PAULO_TZ)
    if now.hour < 8:
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        target = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return target.timestamp()


def process_due_followups_for_instance(instance_id: str):
    """Busca e envia follow-ups vencidos de uma instância específica."""
    now = time.time()
    due = get_due_followups(now, instance_id)

    if not due:
        return

    for item in due:
        phone = item["phone"]
        step = item.get("step", 0)
        raw = item["_raw"]
        # Item garantidamente tem instance_id (set pelo get_due_followups)
        item_instance = item.get("instance_id", instance_id)

        # Lead respondeu entre a query e o envio?
        if not has_active_followups(phone, item_instance):
            remove_followup(raw, phone, item_instance)
            print(f"[SCHEDULER inst {item_instance}] Step {step} de {phone} cancelado (lead respondeu)")
            continue

        # Horário comercial (pula check para owner)
        if phone != OWNER_NUMBER and not is_business_hours():
            new_time = next_business_morning()
            reschedule_followup(raw, new_time, item_instance)
            print(f"[SCHEDULER inst {item_instance}] Step {step} de {phone} reagendado para próxima manhã 8h")
            continue

        # Envia conforme tipo
        msg_type = item.get("type", "text")
        whatsapp_id = f"{phone}@s.whatsapp.net"

        if msg_type == "image":
            caption = item.get("message", "")
            if caption:
                send_message(whatsapp_id, caption, item_instance)
                time.sleep(2)
            image_url = item.get("image_url", "")
            success = send_image(whatsapp_id, image_url, item_instance)
            if success:
                texto_historico = (caption + "\n[imagem de resultado enviada]") if caption else "[imagem de resultado enviada]"
                save_message(phone, "ai", texto_historico, item_instance)
                mark_ai_sent_now(phone, item_instance)
            print(f"[SCHEDULER inst {item_instance}] Step {step} (imagem) para {phone}: {'OK' if success else 'FALHA'}")
        else:
            message = item.get("message", "")
            success = send_message(whatsapp_id, message, item_instance)
            if success:
                save_message(phone, "ai", message, item_instance)
                mark_ai_sent_now(phone, item_instance)
            print(f"[SCHEDULER inst {item_instance}] Step {step} (texto) para {phone}: {'OK' if success else 'FALHA'}")

        remove_followup(raw, phone, item_instance)


def process_due_followups():
    """Itera por todas as instâncias configuradas e processa follow-ups vencidos de cada uma."""
    for instance_id in get_all_instance_ids():
        try:
            process_due_followups_for_instance(instance_id)
        except Exception as e:
            print(f"[SCHEDULER inst {instance_id}] Erro processando follow-ups: {e}")


def main():
    print("[SCHEDULER] Mya Disparo follow-up scheduler iniciado. Checando a cada 30 segundos...")
    while True:
        try:
            process_due_followups()
        except Exception as e:
            print(f"[SCHEDULER] Erro no ciclo: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
