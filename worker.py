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
from tools.manage_calendar import (
    consulta_disponibilidade, consulta_proximos_horarios, criar_evento, consulta_id, deleta_evento
)
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

# --- DEFINIÇÃO DAS TOOLS DO GEMINI (Google Calendar) ---
CALENDAR_TOOLS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="consulta_proximos_horarios",
        description="Busca os próximos horários disponíveis a partir de uma data, iterando dia a dia automaticamente. Use SEMPRE que o lead pedir horários — a tool já calcula gaps, antecedência e horários de atendimento.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "data_inicio": types.Schema(type="STRING", description="Data de início da busca no formato YYYY-MM-DD"),
                "quantidade": types.Schema(type="INTEGER", description="Número de slots a retornar (padrão 3)"),
            },
            required=["data_inicio"]
        )
    ),
    types.FunctionDeclaration(
        name="consulta_disponibilidade",
        description="Consulta os horários ocupados (bookedSlots) de um dia específico no Google Calendar. Prefira consulta_proximos_horarios para oferecer horários ao lead.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "data": types.Schema(type="STRING", description="Data no formato YYYY-MM-DD"),
            },
            required=["data"]
        )
    ),
    types.FunctionDeclaration(
        name="criar_evento",
        description="Cria um evento de reunião (demo de 30 min) no Google Calendar. Use SOMENTE após confirmar horário, nome completo e email do lead.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "data": types.Schema(type="STRING", description="Data no formato YYYY-MM-DD"),
                "horario": types.Schema(type="STRING", description="Horário de início no formato HH:MM"),
                "nome": types.Schema(type="STRING", description="Nome completo do lead"),
                "email": types.Schema(type="STRING", description="Email do lead"),
                "telefone": types.Schema(type="STRING", description="Telefone do lead (número do WhatsApp)"),
                "nicho": types.Schema(type="STRING", description="Nicho/segmento do lead (do memo do lead)"),
                "wa_name": types.Schema(type="STRING", description="Nome salvo no WhatsApp / nome da empresa (do memo do lead)"),
            },
            required=["data", "horario", "nome", "email"]
        )
    ),
    types.FunctionDeclaration(
        name="consulta_id",
        description="Busca eventos agendados pelo telefone do lead. Use para encontrar o ID de um evento antes de cancelar.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "telefone": types.Schema(type="STRING", description="Telefone do lead"),
                "data": types.Schema(type="STRING", description="Data opcional no formato YYYY-MM-DD para filtrar"),
            },
            required=["telefone"]
        )
    ),
    types.FunctionDeclaration(
        name="deleta_evento",
        description="Cancela/deleta um evento do Google Calendar pelo ID. Use após consultar o ID com consulta_id.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "event_id": types.Schema(type="STRING", description="ID do evento no Google Calendar"),
            },
            required=["event_id"]
        )
    ),
    types.FunctionDeclaration(
        name="lead_agendou",
        description="Notifica a equipe via WhatsApp que um lead agendou uma reunião. Chame APÓS criar o evento com sucesso.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "nome": types.Schema(type="STRING", description="Nome do lead"),
                "telefone": types.Schema(type="STRING", description="Telefone do lead"),
                "dia_horario": types.Schema(type="STRING", description="Dia e horário da reunião ex: '15/04 às 10:00'"),
            },
            required=["nome", "telefone", "dia_horario"]
        )
    ),
    types.FunctionDeclaration(
        name="reuniao_agendada",
        description="Cancela os follow-ups do lead após agendamento confirmado. Chame APÓS criar o evento com sucesso.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "telefone": types.Schema(type="STRING", description="Telefone do lead"),
            },
            required=["telefone"]
        )
    ),
])

# Mapeamento nome → função real
TOOL_DISPATCH = {
    "consulta_proximos_horarios": lambda args: consulta_proximos_horarios(args["data_inicio"], args.get("quantidade", 3)),
    "consulta_disponibilidade": lambda args: consulta_disponibilidade(args["data"]),
    "criar_evento": lambda args: criar_evento(
        args["data"], args["horario"], args["nome"], args["email"],
        args.get("telefone", ""), args.get("nicho", ""), args.get("wa_name", "")
    ),
    "consulta_id": lambda args: consulta_id(args["telefone"], args.get("data", "")),
    "deleta_evento": lambda args: deleta_evento(args["event_id"]),
    "lead_agendou": None,       # Tratado diretamente no loop
    "reuniao_agendada": None,   # Tratado diretamente no loop
}

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
    event_id_conhecido = lead_info.get("event_id", "")

    # Constrói o contexto do lead para injetar no System Prompt
    contexto_lead = f"\n\n---\n## 📎 MEMO DO LEAD ATUAL\n"
    contexto_lead += f"- **Telefone (WhatsApp):** {phone_number}\n"
    if push_name:
        contexto_lead += f"- **Nome no WhatsApp (wa_name):** {push_name}\n"
    if nome_conhecido:
        contexto_lead += f"- **Nome:** {nome_conhecido}\n"
    if nicho_conhecido:
        contexto_lead += f"- **Nicho/Área:** {nicho_conhecido}\n"
    if event_id_conhecido:
        contexto_lead += f"- **ID do agendamento ativo:** {event_id_conhecido}\n"
    if resumo_conhecido:
        contexto_lead += f"- **Resumo acumulado da conversa:** {resumo_conhecido}\n"
    contexto_lead += "\nUse essas informações para personalizar a conversa. Chame-o pelo nome quando natural."

    # Injeta data/hora atual (timezone São Paulo) no prompt para o LLM saber "hoje"
    from datetime import datetime, timezone, timedelta
    _sp_tz = timezone(timedelta(hours=-3))
    _agora = datetime.now(_sp_tz)
    _dias_semana = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    contexto_data = (
        f"\n\n---\n## 🗓️ DATA E HORA ATUAL (São Paulo)\n"
        f"- **Hoje:** {_dias_semana[_agora.weekday()]}, {_agora.strftime('%d/%m/%Y')} (formato ISO: {_agora.strftime('%Y-%m-%d')})\n"
        f"- **Hora atual:** {_agora.strftime('%H:%M')}\n"
        f"Use SEMPRE essa data como referência ao chamar tools de calendário. Nunca invente datas.\n"
    )

    prompt_completo = SYSTEM_PROMPT + contexto_lead + contexto_data

    # HISTÓRICO CONTEXTUAL PARA O LLM
    historico = get_history(phone_number)
    print(f"Chamando o Agente (LLM) com workflow SDR_MYA para responder a: {text_message}")

    # --- CHAMADA REAL AO LLM (Gemini) COM FUNCTION CALLING ---
    evento_criado = False
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
                system_instruction=prompt_completo,
                tools=[CALENDAR_TOOLS],
            ),
            history=gemini_history
        )

        response = chat.send_message(text_message)

        # --- LOOP DE FUNCTION CALLING ---
        max_tool_rounds = 8
        tool_round = 0

        while tool_round < max_tool_rounds:
            # Verifica se a resposta contém function calls
            function_calls = []
            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)

            if not function_calls:
                break  # Sem function calls — resposta final de texto

            tool_round += 1
            print(f"[TOOL CALL] Round {tool_round}: {[fc.name for fc in function_calls]}")

            # Executa cada function call e monta as respostas
            function_responses = []
            for fc in function_calls:
                fn_name = fc.name
                fn_args = dict(fc.args) if fc.args else {}
                print(f"[TOOL] Executando {fn_name}({fn_args})")

                if fn_name == "lead_agendou":
                    # Notifica equipe via WhatsApp
                    from tools.send_whatsapp import send_message as sms_raw
                    alerta = (
                        f"📅 *LEAD AGENDOU REUNIÃO* 📅\n"
                        f"Nome: {fn_args.get('nome', '?')}\n"
                        f"Telefone: {fn_args.get('telefone', '?')}\n"
                        f"Dia/Horário: {fn_args.get('dia_horario', '?')}"
                    )
                    sms_raw("5511989887525@s.whatsapp.net", alerta)
                    result = {"success": True, "message": "Equipe notificada"}
                elif fn_name == "reuniao_agendada":
                    # Cancela follow-ups
                    from tools.manage_followups import cancel_followups
                    cancel_followups(fn_args.get("telefone", phone_number))
                    result = {"success": True, "message": "Follow-ups cancelados"}
                    evento_criado = True
                else:
                    dispatch = TOOL_DISPATCH.get(fn_name)
                    if dispatch:
                        result = dispatch(fn_args)
                        # Salva event_id no CRM após agendamento bem-sucedido
                        if fn_name == "criar_evento" and result.get("event_id"):
                            save_lead_info(phone_number, {"event_id": result["event_id"]})
                        # Limpa event_id do CRM após cancelamento
                        if fn_name == "deleta_evento" and result.get("success"):
                            save_lead_info(phone_number, {"event_id": ""})
                    else:
                        result = {"error": f"Tool '{fn_name}' não encontrada"}

                print(f"[TOOL] Resultado de {fn_name}: {json.dumps(result, ensure_ascii=False)[:200]}")
                function_responses.append(
                    types.Part(function_response=types.FunctionResponse(
                        name=fn_name,
                        response=result
                    ))
                )

            # Envia os resultados de volta ao modelo
            response = chat.send_message(function_responses)

        # Extrai a resposta final de texto
        resposta_ai = response.text or ""

        # Log de uso de tokens
        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count
            total_tokens = input_tokens + output_tokens
            print(f"[TOKENS] Entrada: {input_tokens} | Saída: {output_tokens} | Total: {total_tokens}")

    except Exception as e:
        print(f"Erro ao chamar o LLM: {e}")
        import traceback
        traceback.print_exc()
        resposta_ai = "Desculpe, não consegui processar sua solicitação no momento. Por favor, tente novamente mais tarde."
    # ------------------------------------------------------------------------------------------

    # 0. Checa MENSAGEM AUTOMATICA (ignora auto-replies detectados pelo LLM)
    match_auto = re.search(r'<IGNORAR_AUTO>(.*?)</IGNORAR_AUTO>', resposta_ai, re.IGNORECASE)
    if match_auto:
        motivo_auto = match_auto.group(1).strip()
        print(f"[AUTO] Mensagem automática detectada: {motivo_auto}. Ignorando e mantendo follow-ups.")
        save_message(phone_number, "ai", f"[auto-reply ignorada: {motivo_auto}]")
        return  # NÃO reseta follow-ups — auto-reply não conta como resposta humana

    # Mensagem humana confirmada → reseta timer de follow-ups
    from tools.manage_followups import reset_followup_timer
    reset_followup_timer(phone_number)

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
    # Não agenda se: acabou de confirmar reunião via tag OU via function calling (evento_criado)
    if not match_humano and not evento_criado:
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
