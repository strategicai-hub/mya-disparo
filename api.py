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

# Prefixo para isolar dados no Redis compartilhado
KEY_PREFIX = "disparo"

# Configuração do Redis para Buffer Antirrajadas (mesmo DB, prefixo nas chaves)
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
    redis_client.ping()  # Valida conexão imediatamente
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

def publish_to_rabbitmq(payload):
    """Envia o payload consolidado para o RabbitMQ."""
    connection, channel = get_rabbitmq_channel()
    if channel:
        channel.basic_publish(
            exchange='',
            routing_key=RABBITMQ_QUEUE,
            body=json.dumps(payload),
            properties=pika.BasicProperties(delivery_mode=2) # Persistente
        )
        connection.close()
        print(">>> Mensagem (ou Rajada Agrupada) enfileirada com sucesso no RabbitMQ")
    else:
        print("Erro Crítico: Sem conexão com RabbitMQ!")

async def debounce_and_publish(phone_number: str, payload_base: dict):
    """Aguarda 30 segundos observando se o Lead digitou novamente. Se sim, ele morre. Se não, dispara."""
    await asyncio.sleep(30)

    if not redis_client:
        publish_to_rabbitmq(payload_base)
        return

    last_time = float(redis_client.get(f"{KEY_PREFIX}:burst_time:{phone_number}") or 0)

    # Verifica se esta task é a dona da ÚLTIMA mensagem enviada (gap de 29s+)
    if time.time() - last_time >= 29:
        messages = redis_client.lrange(f"{KEY_PREFIX}:burst:{phone_number}", 0, -1)
        if not messages:
            return # Task morta (já limparam a fila)

        full_text = "\n".join(messages)
        redis_client.delete(f"{KEY_PREFIX}:burst:{phone_number}")
        redis_client.delete(f"{KEY_PREFIX}:burst_time:{phone_number}")

        # Injeta o texto compilado no payload e envia
        payload_base["message"]["text"] = full_text
        print(f"\n[DEBOUNCER] Agrupou {len(messages)} mensagens do Lead {phone_number} num único bloco. Enviando...")
        publish_to_rabbitmq(payload_base)


@app.post("/mya-disparo")
async def receive_whatsapp_webhook(request: Request):
    """
    Recebe os eventos via UAZAPI e aciona o Buffer antirrajadas.
    """
    try:
        payload = await request.json()

        event_name = payload.get("EventType")

        if event_name == "messages":
            msg = payload.get("message", {})

            # Mensagem de prospecção enviada pelo n8n → salva histórico e agenda follow-ups
            if msg.get("fromMe") and msg.get("track_source") == "n8n":
                raw_chatid = msg.get("chatid", "")
                lead_phone = raw_chatid.split("@")[0]
                if lead_phone:
                    texto_disparo = msg.get("text", "")
                    if texto_disparo:
                        try:
                            from tools.manage_history import save_message
                            save_message(lead_phone, "ai", texto_disparo)
                            print(f"[DISPARO] Mensagem salva no histórico de {lead_phone}")
                        except Exception as e:
                            print(f"[DISPARO] Erro ao salvar no histórico: {e}")
                    try:
                        from tools.manage_followups import schedule_followups
                        schedule_followups(lead_phone)
                        print(f"[DISPARO] Follow-ups agendados para {lead_phone}")
                    except Exception as e:
                        print(f"[DISPARO] Erro ao agendar follow-ups: {e}")
                return {"status": "success", "message": "Disparo n8n registrado"}

            # Se foi você mesmo quem enviou pelo whats, ignora
            if not msg.get("fromMe", True):

                raw_sender = msg.get("sender_pn") or msg.get("chatid") or msg.get("sender", "")
                phone_number = raw_sender.split("@")[0]
                text = msg.get("text", "")

                print(f"=== MENSAGEM RECEBIDA [{phone_number}] ===\nTexto: '{text}'\n============================")

                # Verifica se IA está bloqueada (agente respondendo pelo Chatwoot)
                if redis_client and redis_client.exists(f"{KEY_PREFIX}:ai_blocked:{phone_number}"):
                    print(f"[CHATWOOT] IA bloqueada para {phone_number}. Ignorando mensagem do lead.")
                    return {"status": "success", "message": "IA bloqueada — Chatwoot ativo"}

                # Bypass do debounce para o numero do proprietario (testes rapidos)
                OWNER_NUMBER = "5511989887525"
                if phone_number == OWNER_NUMBER:
                    print(f"-> Numero do proprietario detectado. Enviando direto sem delay...")
                    publish_to_rabbitmq(payload)
                elif redis_client:
                    # Empilha a mensagem na rajada
                    redis_client.rpush(f"{KEY_PREFIX}:burst:{phone_number}", text)
                    redis_client.set(f"{KEY_PREFIX}:burst_time:{phone_number}", time.time())

                    # Inicia a contagem regressiva assíncrona invisível
                    task = asyncio.create_task(debounce_and_publish(phone_number, payload))
                    background_tasks.add(task)
                    task.add_done_callback(background_tasks.discard)
                    print(f"-> Acionou Delay de 30s para o Lead {phone_number}...")
                else:
                    # Fallback caso Redis morra, manda direto
                    publish_to_rabbitmq(payload)

        return {"status": "success", "message": "Webhook recebido"}

    except Exception as e:
        print(f"Erro no webhook: {e}")
        raise HTTPException(status_code=500, detail="Erro interno no servidor")

@app.get("/mya-disparo/logs/leads")
async def logs_leads():
    """Retorna todos os leads com dados de CRM."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis indisponível")

    lead_keys = redis_client.keys(f"{KEY_PREFIX}:lead:*")
    history_keys = redis_client.keys(f"{KEY_PREFIX}:history:*")

    phones = set()
    for k in lead_keys:
        phones.add(k.replace(f"{KEY_PREFIX}:lead:", ""))
    for k in history_keys:
        phones.add(k.replace(f"{KEY_PREFIX}:history:", ""))

    leads = []
    for phone in sorted(phones):
        crm_raw = redis_client.get(f"{KEY_PREFIX}:lead:{phone}")
        crm = json.loads(crm_raw) if crm_raw else {}
        msg_count = redis_client.llen(f"{KEY_PREFIX}:history:{phone}")
        has_followup = redis_client.exists(f"{KEY_PREFIX}:followup:active:{phone}") == 1
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


@app.get("/mya-disparo/logs/history/{phone}")
async def logs_history(phone: str):
    """Retorna o histórico completo de um lead."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis indisponível")

    raw = redis_client.lrange(f"{KEY_PREFIX}:history:{phone}", 0, -1)
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


@app.get("/mya-disparo/logs/events")
async def logs_events(limit: int = 100):
    """Retorna os últimos eventos de execução do worker (logs por sessão)."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis indisponível")
    raw = redis_client.lrange(f"{KEY_PREFIX}:logs", 0, limit - 1)
    events = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except Exception:
            pass
    return events


@app.post("/chatwoot-webhook")
async def receive_chatwoot_webhook(request: Request):
    """
    Recebe eventos do Chatwoot. Quando agente envia mensagem para um lead,
    bloqueia a IA por 1 hora para aquele número.
    """
    try:
        payload = await request.json()
        event = payload.get("event", "")

        if event != "message_created":
            return {"status": "ignored"}

        # message_type: 1 ou "outgoing" = mensagem enviada pelo agente
        msg_type = payload.get("message_type")
        is_outgoing = msg_type == 1 or msg_type == "outgoing"

        if not is_outgoing:
            return {"status": "ignored", "reason": "not outgoing"}

        # Extrai o telefone do lead da conversa
        conversation = payload.get("conversation", {})
        meta = conversation.get("meta", {})
        sender = meta.get("sender", {})
        phone_raw = sender.get("phone_number", "")

        if not phone_raw:
            print("[CHATWOOT] Webhook recebido sem phone_number no payload.")
            return {"status": "error", "reason": "phone not found"}

        # Normaliza: remove caracteres não numéricos (ex: "+55..." → "55...")
        phone = re.sub(r'\D', '', phone_raw)

        if not phone:
            return {"status": "error", "reason": "invalid phone"}

        # Bloqueia a IA por 1 hora para este lead
        if redis_client:
            redis_client.setex(f"{KEY_PREFIX}:ai_blocked:{phone}", 3600, "chatwoot")
            print(f"[CHATWOOT] IA bloqueada por 1h para {phone} (agente respondeu via Chatwoot)")

        return {"status": "success", "phone": phone, "blocked_seconds": 3600}

    except Exception as e:
        print(f"Erro no webhook Chatwoot: {e}")
        raise HTTPException(status_code=500, detail="Erro interno no servidor")


@app.get("/mya-disparo/apresentacao")
async def serve_pdf():
    """
    Rota pública que hospeda nativamente o PDF do bot
    """
    file_path = os.path.join(os.getcwd(), "apresentacao_mya.pdf")
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/pdf", filename="Apresentacao_Mya.pdf")
    else:
        raise HTTPException(status_code=404, detail="PDF não encontrado no disco do servidor")

@app.get("/mya-disparo/resultado")
async def serve_resultado_image():
    """
    Rota pública que hospeda a imagem de resultado para follow-up D+7
    """
    # Tenta jpg primeiro, depois png
    for ext in ["jpg", "jpeg", "png"]:
        file_path = os.path.join(os.getcwd(), f"resultado_followup.{ext}")
        if os.path.exists(file_path):
            media_type = "image/jpeg" if ext in ["jpg", "jpeg"] else "image/png"
            return FileResponse(file_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Imagem de resultado não encontrada no disco do servidor")
