import os
import time
import pika
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Ferramentas WAT
from tools.send_whatsapp import send_message
from tools.manage_history import save_message, get_history
from tools.manage_leads import save_lead_info, get_lead_info
import re

load_dotenv()

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
RABBITMQ_QUEUE = "mya_disparo"

LLM_API_KEY = os.getenv("LLM_API_KEY")
if LLM_API_KEY:
    client = genai.Client(api_key=LLM_API_KEY)
else:
    client = None

try:
    with open("workflows/sdr_mya.md", "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except FileNotFoundError:
    SYSTEM_PROMPT = "Você é um atendente virtual SDR corporativo. Pergunte o nome do cliente."
    print("AVISO: Arquivo sdr_mya.md não encontrado!")

def process_message(msg_payload):
    """
    Recebe os dados do WhatsApp e invoca o Agente LLM baseado no Workflow da Mya.
    """
    # A estrutura depende exatamente do JSON que a UAZAPI repassa:
    message_data = msg_payload.get("message", {})
    raw_sender = message_data.get("sender_pn") or message_data.get("chatid") or message_data.get("sender", "")
    phone_number = raw_sender.split("@")[0]
    push_name = message_data.get("senderName", "Lead")
    text_message = message_data.get("text", "")

    if not text_message:
        print("Mensagem sem formato de texto inteligível. Ignorando.")
        return

    print(f"[{phone_number} - {push_name}] Mensagem: {text_message}")

    # Comando Secreto: Reseta a memória sem precisar de acesso via terminal ao Redis
    if text_message.strip().lower() == "/reset":
        from tools.manage_history import clear_history
        from tools.manage_leads import clear_lead_info
        clear_history(phone_number)
        clear_lead_info(phone_number)
        print("Memória de Chat e CRM apagadas pelo usuário.")
        send_message(f"{phone_number}@s.whatsapp.net", "✅ *Amnésia Dupla ativada!*\nFui perfeitamente deletada e não faço ideia de nicho ou qualificação sua.\nPode testar a rajada de mensagens do zero.")
        return

    # Salva o input no Redis
    save_message(phone_number, "human", text_message)

    # Lê dados já conhecidos do lead no CRM
    lead_info = get_lead_info(phone_number)
    nome_conhecido = lead_info.get("nome", "")
    nicho_conhecido = lead_info.get("nicho", "")
    resumo_conhecido = lead_info.get("resumo", "")

    # Constrói o contexto do lead para injetar no System Prompt
    contexto_lead = ""
    if nome_conhecido or nicho_conhecido:
        contexto_lead = "\n\n---\n## 📎 MEMO DO LEAD ATUAL\n"
        if nome_conhecido:
            contexto_lead += f"- **Nome:** {nome_conhecido}\n"
        if nicho_conhecido:
            contexto_lead += f"- **Nicho/Área:** {nicho_conhecido}\n"
        if resumo_conhecido:
            contexto_lead += f"- **Resumo da última conversa:** {resumo_conhecido}\n"
        contexto_lead += "\nUse essas informações para personalizar a conversa. Chame-o pelo nome quando natural."

    prompt_completo = SYSTEM_PROMPT + contexto_lead

    # HISTÓRICO CONTEXTUAL PARA O LLM
    historico = get_history(phone_number)
    print(f"Chamando o Agente (LLM) com workflow SDR_MYA para responder a: {text_message}")

    # --- CHAMADA REAL AO LLM (Gemini) ---
    try:
        model_id = "gemini-2.5-flash"
        gemini_history = []

        if len(historico) > 1:
            for msg in historico[:-1]:
                role = "user" if msg["type"] == "human" else "model"
                content = msg.get("data", {}).get("content", "")
                if content:
                    gemini_history.append(types.Content(role=role, parts=[types.Part.from_text(text=content)]))

        chat = client.chats.create(
            model=model_id,
            config=types.GenerateContentConfig(
                system_instruction=prompt_completo,  # Prompt enriquecido com dados do lead
            ),
            history=gemini_history
        )

        response = chat.send_message(text_message)
        resposta_ai = response.text

        # Log de uso de tokens
        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count
            total_tokens = input_tokens + output_tokens
            print(f"[TOKENS] Entrada: {input_tokens} | Saída: {output_tokens} | Total: {total_tokens}")

    except Exception as e:
        print(f"Erro ao chamar o LLM: {e}")
        resposta_ai = "Desculpe, não consegui processar sua solicitação no momento. Por favor, tente novamente mais tarde."
    # ------------------------------------------------------------------------------------------

    # 1. Checa NOME (via tag XML → fallback padrão 'Muito prazer' → fallback histórico)
    match = re.search(r'<SAVE_NAME>(.*?)</SAVE_NAME>', resposta_ai, re.IGNORECASE)
    if match:
        nome_lead = match.group(1).strip()
        save_lead_info(phone_number, {"nome": nome_lead, "whatsapp": phone_number})
        nome_conhecido = nome_lead  # Atualiza local para schedule_followups usar o valor correto
        print(f"[CRM] Nome salvo via tag: {nome_lead}")
        resposta_ai = re.sub(r'<SAVE_NAME>.*?</SAVE_NAME>', '', resposta_ai, flags=re.IGNORECASE).strip()
    elif not nome_conhecido:
        # Fallback 1: detecta 'Muito prazer, X!' — sem IGNORECASE para exigir inicial maiúscula (evita capturar verbos como "ajudar")
        fallback_texto = re.search(r'(?:muito prazer|prazer)[,!\s]+([A-ZÀ-Ú][a-zà-ú]+)', resposta_ai)
        if fallback_texto:
            nome_lead = fallback_texto.group(1).strip()
            save_lead_info(phone_number, {"nome": nome_lead, "whatsapp": phone_number})
            nome_conhecido = nome_lead  # Atualiza local
            print(f"[CRM] Nome salvo via fallback texto: {nome_lead}")
        else:
            # Fallback 2: se a IA mandou saudação de nome, a mensagem ANTERIOR do humano era o nome
            saudacao_nome = re.search(r'(?:muito prazer|que nome bonito|que nome lindo|boa tarde|bom dia|boa noite),?\s', resposta_ai, re.IGNORECASE)
            if saudacao_nome and len(historico) >= 2:
                # Penúltima mensagem humana (a que veio antes da resposta atual)
                msgs_humanas = [m for m in historico[:-1] if m.get("type") == "human"]
                if msgs_humanas:
                    ultima_humana = msgs_humanas[-1].get("data", {}).get("content", "").strip()
                    # Salva se parece um nome (máx 3 palavras, sem números ou sinais especiais)
                    if ultima_humana and len(ultima_humana.split()) <= 3 and re.match(r'^[A-Za-zÀ-ÿ\s]+$', ultima_humana):
                        save_lead_info(phone_number, {"nome": ultima_humana.title(), "whatsapp": phone_number})
                        nome_conhecido = ultima_humana.title()  # Atualiza local
                        print(f"[CRM] Nome salvo via histórico: {ultima_humana.title()}")

    # 2a. Checa NICHO (via tag <SAVE_NICHO> — apenas 2-3 palavras do negócio)
    match_nicho = re.search(r'<SAVE_NICHO>(.*?)</SAVE_NICHO>', resposta_ai, re.IGNORECASE)
    if match_nicho:
        nicho_tag = match_nicho.group(1).strip()
        save_lead_info(phone_number, {"nicho": nicho_tag})
        resposta_ai = re.sub(r'<SAVE_NICHO>.*?</SAVE_NICHO>', '', resposta_ai, flags=re.IGNORECASE).strip()
        print(f"[CRM] Nicho salvo via tag: {nicho_tag}")
        nicho_conhecido = nicho_tag
    elif '[PDF_APRESENTACAO]' in resposta_ai and not nicho_conhecido:
        # Fallback: quando a IA dispara o PDF, a última mensagem humana era o nicho
        msgs_humanas = [m for m in historico[:-1] if m.get("type") == "human"]
        if msgs_humanas:
            nicho_detectado = msgs_humanas[-1].get("data", {}).get("content", "").strip()
            if nicho_detectado:
                save_lead_info(phone_number, {"nicho": nicho_detectado})
                print(f"[CRM] Nicho salvo via histórico (fallback PDF): {nicho_detectado}")
                nicho_conhecido = nicho_detectado  # Atualiza para usar no Sheets abaixo

    # 2b. Checa RESUMO (via tag <SAVE_RESUMO> — atualiza após toda troca de mensagem)
    match_resumo = re.search(r'<SAVE_RESUMO>(.*?)</SAVE_RESUMO>', resposta_ai, re.IGNORECASE)
    resumo_texto = ""
    if match_resumo:
        resumo_texto = match_resumo.group(1).strip()
        save_lead_info(phone_number, {"resumo": resumo_texto})
        resposta_ai = re.sub(r'<SAVE_RESUMO>.*?</SAVE_RESUMO>', '', resposta_ai, flags=re.IGNORECASE).strip()
        print(f"[CRM] Resumo atualizado: {resumo_texto}")

    # Salva/atualiza na planilha Google Sheets a cada mensagem (upsert por telefone)
    try:
        from tools.save_to_sheets import save_lead_to_sheet
        lead_info_atual = get_lead_info(phone_number)
        save_lead_to_sheet(
            phone=phone_number,
            name=lead_info_atual.get("nome", push_name),
            niche=lead_info_atual.get("nicho", ""),
            resumo=resumo_texto or lead_info_atual.get("resumo", "")
        )
    except Exception as e:
        print(f"[SHEETS] Falha ao salvar na planilha (não crítico): {e}")


    # 3. Checa ALARME DE EQUIPE (Atendimento Humano)
    match_humano = re.search(r'<ATENDIMENTO_HUMANO>(.*?)</ATENDIMENTO_HUMANO>', resposta_ai, re.IGNORECASE)
    if match_humano:
        motivo = match_humano.group(1).strip()
        from tools.send_whatsapp import send_message as sms_raw
        sms_raw("5511989887525@s.whatsapp.net", f"🚨 *MYA DISPARO LEAD ALERTA* 🚨\nLead: {push_name} ({phone_number})\nMotivo: {motivo}")
        resposta_ai = re.sub(r'<ATENDIMENTO_HUMANO>.*?</ATENDIMENTO_HUMANO>', '', resposta_ai, flags=re.IGNORECASE).strip()
        # Cancela follow-ups se o lead fechou reunião
        from tools.manage_followups import cancel_followups as cancel_fu
        cancel_fu(phone_number)

    # 4. Salva a resposta gerada inteira (já limpa da tag) no Redis Histórico
    save_message(phone_number, "ai", resposta_ai.replace("[PDF_APRESENTACAO]", "").strip())

    # 5. FATIADOR DE MENSAGENS E DELAY HUMANO COORDENADO
    mensagens = [m.strip() for m in resposta_ai.split("\n\n") if m.strip()]

    for indice, msg_text in enumerate(mensagens):
        if "[PDF_APRESENTACAO]" in msg_text:
            clean_text = msg_text.replace("[PDF_APRESENTACAO]", "").strip()
            if clean_text:
                send_message(f"{phone_number}@s.whatsapp.net", clean_text)
                time.sleep(2)

            print("Mya solicitou envio da apresentação PDF. Disparando...")
            from tools.send_media import send_pdf
            send_pdf(phone_number + "@s.whatsapp.net", "apresentacao_mya.pdf")
            time.sleep(4) # Tempo de respiro de documento
        else:
            sucesso = send_message(f"{phone_number}@s.whatsapp.net", msg_text)
            if sucesso:
                print(f"[{indice+1}/{len(mensagens)}] Mensagem fatiada enviada.")
                if indice < len(mensagens) - 1:
                    time.sleep(2)
            else:
                print("A API da Uazapi falhou no envio. Manter no log.")

    # 6. AGENDA FOLLOW-UPS após toda resposta enviada (reseta timer se já existiam)
    if not match_humano:  # Não agenda se acabou de confirmar reunião
        from tools.manage_followups import schedule_followups
        schedule_followups(phone_number, nome=nome_conhecido, nicho=nicho_conhecido)
        print(f"[FOLLOWUP] Follow-ups agendados/resetados para {phone_number}")

def callback(ch, method, properties, body):
    """Função invocada sempre que chegou nova msg do RabbitMQ."""
    msg_payload = json.loads(body)
    try:
        process_message(msg_payload)
        # Confirma que processou com sucesso e pode retirar da fila
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"Falha ao processar a mensagem: {e}")
        # Nack joga de volta na requisição (ou manda para DLQ) se falhar para não perder a mensagem do lead
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def main():
    """Conecta no Rabbit e escuta ininterruptamente"""
    print("Iniciando Mya Disparo Worker...")

    connection = None
    while connection is None:
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            connection = pika.BlockingConnection(pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                virtual_host=RABBITMQ_VHOST,
                credentials=credentials
            ))
        except Exception as e:
            print(f"ERRO REAL DO RABBITMQ: {type(e).__name__} - {str(e)}")
            print("Tentando novamente em 5 segundos...")
            time.sleep(5)

    channel = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
    channel.basic_qos(prefetch_count=1)

    channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=callback)

    print(" [*] Esperando por mensagens SMS/Whatsapp via Uazapi. Pressione CTRL+C para sair.")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("Desligando worker.")
        channel.stop_consuming()
    finally:
        connection.close()


if __name__ == '__main__':
    main()
