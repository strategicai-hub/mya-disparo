import os
import re
import pika
import json
import asyncio
import time
import redis
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from config.instances import redis_prefix, valid_instance, OWNER_NUMBER, get_provider

load_dotenv()

app = FastAPI(title="Mya Disparo Bot Webhook")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configurações do RabbitMQ
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_QUEUE = "mya_disparo"

# Segredo compartilhado com o disparador-whatsapp para autenticar o forward Meta.
# Quando vazio, o endpoint /mya-disparo-meta-{instance_id} fica desabilitado.
CHATBOT_FORWARD_SECRET = os.getenv("CHATBOT_FORWARD_SECRET", "")

# Configuração do Redis para Buffer Antirrajadas (prefixo por instância)
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
    redis_client.ping()
except Exception as e:
    print(f"Erro ao conectar ao Redis para Buffer: {e}")
    redis_client = None

background_tasks = set()


def get_rabbitmq_channel():
    """Conecta ao RabbitMQ e retorna um canal."""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            virtual_host=RABBITMQ_VHOST,
            credentials=credentials
        ))
        channel = connection.channel()
        channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
        return connection, channel
    except Exception as e:
        print(f"Erro ao conectar ao RabbitMQ: {e}")
        return None, None


def publish_to_rabbitmq(payload, instance_id):
    """Envia o payload consolidado para o RabbitMQ, carimbando o instance_id."""
    payload["_instance_id"] = str(instance_id)
    connection, channel = get_rabbitmq_channel()
    if channel:
        channel.basic_publish(
            exchange='',
            routing_key=RABBITMQ_QUEUE,
            body=json.dumps(payload),
            properties=pika.BasicProperties(delivery_mode=2)  # Persistente
        )
        connection.close()
        print(f">>> Mensagem enfileirada com sucesso no RabbitMQ [inst {instance_id}]")
    else:
        print("Erro Crítico: Sem conexão com RabbitMQ!")


async def debounce_and_publish(phone_number: str, payload_base: dict, instance_id: str):
    """Aguarda 30 segundos observando se o Lead digitou novamente. Se sim, ele morre. Se não, dispara."""
    await asyncio.sleep(30)

    if not redis_client:
        publish_to_rabbitmq(payload_base, instance_id)
        return

    prefix = redis_prefix(instance_id)
    last_time = float(redis_client.get(f"{prefix}:burst_time:{phone_number}") or 0)

    if time.time() - last_time >= 29:
        messages = redis_client.lrange(f"{prefix}:burst:{phone_number}", 0, -1)
        if not messages:
            return

        full_text = "\n".join(messages)
        redis_client.delete(f"{prefix}:burst:{phone_number}")
        redis_client.delete(f"{prefix}:burst_time:{phone_number}")

        payload_base["message"]["text"] = full_text
        print(f"\n[DEBOUNCER] Agrupou {len(messages)} mensagens do Lead {phone_number} [inst {instance_id}]. Enviando...")
        publish_to_rabbitmq(payload_base, instance_id)


async def _handle_webhook(instance_id: str, request: Request):
    """Lógica compartilhada para recebimento de webhook da UAZAPI por instância."""
    if not valid_instance(instance_id):
        raise HTTPException(status_code=404, detail=f"Instância '{instance_id}' não configurada")

    try:
        payload = await request.json()
        event_name = payload.get("EventType")

        if event_name == "messages":
            msg = payload.get("message", {})
            prefix = redis_prefix(instance_id)

            # Mensagem de prospecção enviada pelo n8n → salva histórico e agenda follow-ups
            if msg.get("fromMe") and msg.get("track_source") == "n8n":
                raw_chatid = msg.get("chatid", "")
                lead_phone = raw_chatid.split("@")[0]
                if lead_phone:
                    texto_disparo = msg.get("text", "")
                    if texto_disparo:
                        try:
                            from tools.manage_history import save_message
                            save_message(lead_phone, "ai", texto_disparo, instance_id)
                            print(f"[DISPARO] Mensagem salva no histórico de {lead_phone} [inst {instance_id}]")
                        except Exception as e:
                            print(f"[DISPARO] Erro ao salvar no histórico: {e}")
                    try:
                        from tools.manage_leads import get_lead_info
                        event_id_atual = get_lead_info(lead_phone, instance_id).get("event_id", "")
                        if event_id_atual:
                            print(f"[DISPARO] Follow-ups NÃO agendados: lead {lead_phone} já tem reunião (event_id={event_id_atual}) [inst {instance_id}]")
                        else:
                            from tools.manage_followups import schedule_followups
                            schedule_followups(lead_phone, instance_id)
                            print(f"[DISPARO] Follow-ups agendados para {lead_phone} [inst {instance_id}]")
                    except Exception as e:
                        print(f"[DISPARO] Erro ao agendar follow-ups: {e}")
                return {"status": "success", "message": "Disparo n8n registrado"}

            # Mensagem enviada manualmente (Chatwoot/WhatsApp direto) → bloqueia IA por 1h
            if msg.get("fromMe"):
                track = msg.get("track_source", "")
                if track not in ("n8n", "IA"):
                    raw_chatid = msg.get("chatid", "")
                    lead_phone = raw_chatid.split("@")[0]
                    if lead_phone and redis_client:
                        redis_client.setex(f"{prefix}:ai_blocked:{lead_phone}", 3600, "manual")
                        print(f"[CHATWOOT] Mensagem manual para {lead_phone} [inst {instance_id}]. IA bloqueada por 1h.")
                return {"status": "success", "message": "Mensagem manual registrada"}

            # Mensagem recebida do lead
            if not msg.get("fromMe", True):
                raw_sender = msg.get("sender_pn") or msg.get("chatid") or msg.get("sender", "")
                phone_number = raw_sender.split("@")[0]
                text = msg.get("text", "")

                print(f"=== MENSAGEM RECEBIDA [inst {instance_id}][{phone_number}] ===\nTexto: '{text}'\n============================")

                # Bypass do debounce para o numero do proprietario (testes rapidos)
                # IMPORTANTE: este bypass deve vir antes de qualquer filtro (ai_blocked, debounce)
                # para garantir que comandos como /reset sempre cheguem ao worker.
                if phone_number == OWNER_NUMBER:
                    print(f"-> Numero do proprietario detectado. Enviando direto sem delay [inst {instance_id}]...")
                    publish_to_rabbitmq(payload, instance_id)
                    return {"status": "success", "message": "Webhook recebido"}

                if redis_client and redis_client.exists(f"{prefix}:ai_blocked:{phone_number}"):
                    print(f"[CHATWOOT] IA bloqueada para {phone_number} [inst {instance_id}]. Ignorando mensagem do lead.")
                    return {"status": "success", "message": "IA bloqueada — Chatwoot ativo"}

                if redis_client:
                    redis_client.rpush(f"{prefix}:burst:{phone_number}", text)
                    redis_client.set(f"{prefix}:burst_time:{phone_number}", time.time())

                    task = asyncio.create_task(debounce_and_publish(phone_number, payload, instance_id))
                    background_tasks.add(task)
                    task.add_done_callback(background_tasks.discard)
                    print(f"-> Acionou Delay de 30s para o Lead {phone_number} [inst {instance_id}]...")
                else:
                    publish_to_rabbitmq(payload, instance_id)

        return {"status": "success", "message": "Webhook recebido"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Erro no webhook [inst {instance_id}]: {e}")
        raise HTTPException(status_code=500, detail="Erro interno no servidor")


@app.post("/mya-disparo-meta-{instance_id}")
async def receive_meta_forward(instance_id: str, request: Request):
    """Recebe inbound da API oficial Meta encaminhado pelo disparador-whatsapp.

    Payload esperado:
        { "from": "5511...", "name": "...", "text": "...", "messageId": "wamid..." }

    Autenticação via header `x-chatbot-secret` igual a CHATBOT_FORWARD_SECRET.
    Normaliza para o shape interno UAZAPI e reutiliza o pipeline (debounce + fila).
    """
    if not CHATBOT_FORWARD_SECRET:
        raise HTTPException(status_code=503, detail="CHATBOT_FORWARD_SECRET não configurado")
    if request.headers.get("x-chatbot-secret") != CHATBOT_FORWARD_SECRET:
        raise HTTPException(status_code=401, detail="secret inválido")

    if not valid_instance(instance_id):
        raise HTTPException(status_code=404, detail=f"Instância '{instance_id}' não configurada")
    if get_provider(instance_id) != "meta":
        raise HTTPException(status_code=400, detail=f"Instância '{instance_id}' não está configurada como provider 'meta'")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    from_phone = (body.get("from") or "").lstrip("+").split("@")[0]
    text = body.get("text") or ""
    name = body.get("name") or "Lead"
    tenant_id = (body.get("tenantId") or "").strip()

    if not from_phone or not text:
        return {"status": "ignored", "reason": "missing from/text"}

    # Memoriza tenant_id do disparador para auditoria de outbound (24h).
    if tenant_id:
        try:
            from tools.audit import remember_tenant
            remember_tenant(instance_id, from_phone, tenant_id)
        except Exception as e:
            print(f"[AUDIT] remember_tenant falhou (não crítico): {e}")

    # Mesma estrutura interna que o webhook UAZAPI produz
    payload = {
        "EventType": "messages",
        "message": {
            "fromMe": False,
            "chatid": f"{from_phone}@s.whatsapp.net",
            "sender_pn": from_phone,
            "senderName": name,
            "text": text,
        },
    }

    prefix = redis_prefix(instance_id)

    if redis_client and redis_client.exists(f"{prefix}:ai_blocked:{from_phone}"):
        print(f"[CHATWOOT] IA bloqueada para {from_phone} [inst {instance_id}] (Meta forward). Ignorando.")
        return {"status": "ignored", "reason": "ai blocked"}

    print(f"=== MENSAGEM RECEBIDA META [inst {instance_id}][{from_phone}] ===\nTexto: '{text}'\n============================")

    if from_phone == OWNER_NUMBER:
        print(f"-> Numero do proprietario detectado (Meta). Enviando direto sem delay [inst {instance_id}]...")
        publish_to_rabbitmq(payload, instance_id)
    elif redis_client:
        redis_client.rpush(f"{prefix}:burst:{from_phone}", text)
        redis_client.set(f"{prefix}:burst_time:{from_phone}", time.time())
        task = asyncio.create_task(debounce_and_publish(from_phone, payload, instance_id))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        print(f"-> Acionou Delay de 30s para o Lead {from_phone} (Meta) [inst {instance_id}]...")
    else:
        publish_to_rabbitmq(payload, instance_id)

    return {"status": "success"}


@app.post("/mya-disparo-meta-outbound-{instance_id}")
async def receive_meta_outbound(instance_id: str, request: Request):
    """Registra disparo outbound da API Oficial Meta e agenda follow-ups.

    Chamado pelo disparador-whatsapp após envio bem-sucedido de template/texto.
    Payload: { "to": "5511...", "name": "...", "text": "..." }
    Auth: x-chatbot-secret
    """
    if not CHATBOT_FORWARD_SECRET:
        raise HTTPException(status_code=503, detail="CHATBOT_FORWARD_SECRET não configurado")
    if request.headers.get("x-chatbot-secret") != CHATBOT_FORWARD_SECRET:
        raise HTTPException(status_code=401, detail="secret inválido")
    if not valid_instance(instance_id):
        raise HTTPException(status_code=404, detail=f"Instância '{instance_id}' não configurada")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    to_phone = (body.get("to") or "").lstrip("+").split("@")[0]
    text = body.get("text") or ""
    name = body.get("name") or ""

    if not to_phone or not text:
        return {"status": "ignored", "reason": "missing to/text"}

    # Salva no histórico como mensagem "ai" (outbound)
    try:
        from tools.manage_history import save_message
        save_message(to_phone, "ai", text, instance_id)
        print(f"[OUTBOUND] Registrado no histórico de {to_phone}: '{text[:60]}' [inst {instance_id}]")
    except Exception as e:
        print(f"[OUTBOUND] Erro ao salvar histórico: {e}")

    # Agenda 2 follow-ups: 1h + amanhã 8-9h
    try:
        from tools.manage_leads import get_lead_info
        lead_info = get_lead_info(to_phone, instance_id)
        nome = lead_info.get("nome") or name
        nicho = lead_info.get("nicho") or ""
        resumo = lead_info.get("resumo") or ""
        from tools.manage_followups import schedule_meta_outbound_followups
        schedule_meta_outbound_followups(to_phone, instance_id, nome=nome, nicho=nicho, resumo=resumo)
    except Exception as e:
        print(f"[OUTBOUND] Erro ao agendar follow-ups: {e}")

    return {"status": "success"}


@app.post("/mya-disparo-{instance_id}")
async def receive_whatsapp_webhook(instance_id: str, request: Request):
    """Recebe os eventos via UAZAPI e aciona o Buffer antirrajadas (roteado por instância)."""
    return await _handle_webhook(instance_id, request)


@app.get("/mya-disparo-{instance_id}/logs/leads")
async def logs_leads(instance_id: str):
    """Retorna todos os leads da instância com dados de CRM."""
    if not valid_instance(instance_id):
        raise HTTPException(status_code=404, detail=f"Instância '{instance_id}' não configurada")
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis indisponível")

    prefix = redis_prefix(instance_id)
    lead_keys = redis_client.keys(f"{prefix}:lead:*")
    history_keys = redis_client.keys(f"{prefix}:history:*")

    phones = set()
    for k in lead_keys:
        phones.add(k.replace(f"{prefix}:lead:", ""))
    for k in history_keys:
        phones.add(k.replace(f"{prefix}:history:", ""))

    leads = []
    for phone in sorted(phones):
        crm_raw = redis_client.get(f"{prefix}:lead:{phone}")
        crm = json.loads(crm_raw) if crm_raw else {}
        msg_count = redis_client.llen(f"{prefix}:history:{phone}")
        has_followup = redis_client.exists(f"{prefix}:followup:active:{phone}") == 1
        leads.append({
            "phone": phone,
            "nome": crm.get("nome", ""),
            "nicho": crm.get("nicho", ""),
            "resumo": crm.get("resumo", ""),
            "event_id": crm.get("event_id", ""),
            "msg_count": msg_count,
            "has_followup": has_followup,
        })

    leads.sort(key=lambda x: x["msg_count"], reverse=True)
    return leads


@app.get("/mya-disparo-{instance_id}/logs/history/{phone}")
async def logs_history(instance_id: str, phone: str):
    """Retorna o histórico completo de um lead em uma instância."""
    if not valid_instance(instance_id):
        raise HTTPException(status_code=404, detail=f"Instância '{instance_id}' não configurada")
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis indisponível")

    raw = redis_client.lrange(f"{redis_prefix(instance_id)}:history:{phone}", 0, -1)
    messages = []
    for item in raw:
        try:
            msg = json.loads(item)
            messages.append({
                "role": msg.get("type", ""),
                "content": msg.get("data", {}).get("content", ""),
            })
        except Exception:
            pass
    return messages


@app.get("/mya-disparo-{instance_id}/logs/events")
async def logs_events(instance_id: str, limit: int = 100):
    """Retorna os últimos eventos de execução do worker (logs por sessão) da instância."""
    if not valid_instance(instance_id):
        raise HTTPException(status_code=404, detail=f"Instância '{instance_id}' não configurada")
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis indisponível")
    raw = redis_client.lrange(f"{redis_prefix(instance_id)}:logs", 0, limit - 1)
    events = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except Exception:
            pass
    return events


@app.post("/chatwoot-webhook-{instance_id}")
async def receive_chatwoot_webhook(instance_id: str, request: Request):
    """Recebe eventos do Chatwoot de uma instância; bloqueia a IA por 1h quando o agente responde."""
    if not valid_instance(instance_id):
        raise HTTPException(status_code=404, detail=f"Instância '{instance_id}' não configurada")

    try:
        payload = await request.json()
        print(f"[CHATWOOT inst {instance_id}] Webhook recebido: {json.dumps(payload, ensure_ascii=False)[:500]}")

        event = payload.get("event", "")
        if event != "message_created":
            print(f"[CHATWOOT inst {instance_id}] Evento ignorado: {event}")
            return {"status": "ignored"}

        msg_type = payload.get("message_type")
        is_outgoing = msg_type == 1 or msg_type == "outgoing"
        print(f"[CHATWOOT inst {instance_id}] message_type={msg_type!r}, is_outgoing={is_outgoing}")

        if not is_outgoing:
            return {"status": "ignored", "reason": "not outgoing"}

        conversation = payload.get("conversation", {})
        meta = conversation.get("meta", {})
        sender = meta.get("sender", {})
        phone_raw = sender.get("phone_number", "")
        print(f"[CHATWOOT inst {instance_id}] phone_raw: {phone_raw!r}")

        if not phone_raw:
            print(f"[CHATWOOT inst {instance_id}] ERRO: phone_number não encontrado no payload.")
            return {"status": "error", "reason": "phone not found"}

        phone = re.sub(r'\D', '', phone_raw)
        if not phone:
            return {"status": "error", "reason": "invalid phone"}

        if redis_client:
            redis_client.setex(f"{redis_prefix(instance_id)}:ai_blocked:{phone}", 3600, "chatwoot")
            print(f"[CHATWOOT inst {instance_id}] IA bloqueada por 1h para {phone}")

        return {"status": "success", "phone": phone, "blocked_seconds": 3600}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Erro no webhook Chatwoot [inst {instance_id}]: {e}")
        raise HTTPException(status_code=500, detail="Erro interno no servidor")


# --- Rotas de mídia compartilhadas (consumidas pela UAZAPI como URL pública de download) ---
@app.get("/mya-disparo/apresentacao")
async def serve_pdf():
    """Rota pública que hospeda o PDF de apresentação (compartilhado entre instâncias)."""
    file_path = os.path.join(os.getcwd(), "apresentacao_mya.pdf")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/pdf", filename="Apresentacao_Mya.pdf")
    raise HTTPException(status_code=404, detail="PDF não encontrado no disco do servidor")


@app.get("/mya-disparo/resultado")
async def serve_resultado_image():
    """Rota pública da imagem de resultado para follow-up D+7 (compartilhada entre instâncias)."""
    for ext in ["jpg", "jpeg", "png"]:
        file_path = os.path.join(os.getcwd(), f"resultado_followup.{ext}")
        if os.path.exists(file_path):
            media_type = "image/jpeg" if ext in ["jpg", "jpeg"] else "image/png"
            return FileResponse(file_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Imagem de resultado não encontrada no disco do servidor")
