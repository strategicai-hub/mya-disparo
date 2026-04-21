import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

from tools.manage_followups import (
    get_due_followups, remove_followup, reschedule_followup,
    has_active_followups, OWNER_NUMBER
)
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


def process_due_followups():
    """Busca e envia follow-ups que já passaram do horário agendado."""
    now = time.time()
    due = get_due_followups(now)

    if not due:
        return

    for item in due:
        phone = item["phone"]
        step = item.get("step", 0)
        raw = item["_raw"]

        # Lead respondeu entre a query e o envio?
        if not has_active_followups(phone):
            remove_followup(raw, phone)
            print(f"[SCHEDULER] Step {step} de {phone} cancelado (lead respondeu)")
            continue

        # Horário comercial (pula check para owner)
        if phone != OWNER_NUMBER and not is_business_hours():
            new_time = next_business_morning()
            reschedule_followup(raw, new_time)
            print(f"[SCHEDULER] Step {step} de {phone} reagendado para próxima manhã 8h")
            continue

        # Envia conforme tipo
        msg_type = item.get("type", "text")
        whatsapp_id = f"{phone}@s.whatsapp.net"

        if msg_type == "image":
            # Envia texto de contexto antes da imagem, se houver
            caption = item.get("message", "")
            if caption:
                send_message(whatsapp_id, caption)
                time.sleep(2)
            image_url = item.get("image_url", "")
            success = send_image(whatsapp_id, image_url)
            if success:
                texto_historico = (caption + "\n[imagem de resultado enviada]") if caption else "[imagem de resultado enviada]"
                save_message(phone, "ai", texto_historico)
                mark_ai_sent_now(phone)
            print(f"[SCHEDULER] Step {step} (imagem) para {phone}: {'OK' if success else 'FALHA'}")
        else:
            message = item.get("message", "")
            success = send_message(whatsapp_id, message)
            if success:
                save_message(phone, "ai", message)
                mark_ai_sent_now(phone)
            print(f"[SCHEDULER] Step {step} (texto) para {phone}: {'OK' if success else 'FALHA'}")

        # Remove do sorted set após envio
        remove_followup(raw, phone)


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
