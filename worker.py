import os
import time
import pika
import json
import redis as redis_module
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

# --- LOG SESSION (captura prints para o Redis) ---
_LOG_KEY = "disparo:logs"
try:
    _log_redis = redis_module.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    _log_redis.ping()
except Exception:
    _log_redis = None

_session_log = []

def log(msg):
    print(msg)
    _session_log.append(str(msg))

def _save_session_log(phone_number):
    global _session_log
    if _log_redis and _session_log:
        entry = json.dumps({"ts": time.time(), "phone": phone_number, "lines": list(_session_log)})
        _log_redis.lpush(_LOG_KEY, entry)
        _log_redis.ltrim(_LOG_KEY, 0, 499)
    _session_log = []

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
        log("Mensagem sem formato de texto inteligível. Ignorando.")
        _save_session_log(phone_number)
        return

    log(f"[{phone_number} - {push_name}] Mensagem: {text_message}")

    # Comando Secreto: Reseta a memória sem precisar de acesso via terminal ao Redis
    if text_message.strip().lower() == "/reset":
        from tools.manage_history import clear_history
        from tools.manage_leads import clear_lead_info
        from tools.manage_followups import reset_followup_cycle
        clear_history(phone_number)
        clear_lead_info(phone_number)
        reset_followup_cycle(phone_number)
        log("Memória de Chat, CRM e ciclo de follow-up apagados pelo usuário.")
        send_message(f"{phone_number}@s.whatsapp.net", "✅ *Amnésia Dupla ativada!*\nFui perfeitamente deletada e não faço ideia de nicho ou qualificação sua.\nPode testar a rajada de mensagens do zero.")
        _save_session_log(phone_number)
        return

    # Detecção heurística pré-LLM: padrões óbvios de IA/atendente virtual no input do "lead"
    try:
        from tools.ai_detector import detect as detect_ai
        is_ai, motivo_ai = detect_ai(text_message)
        if is_ai:
            log(f"[AI_DETECT] Heurística detectou IA em {phone_number}: {motivo_ai}")
            save_message(phone_number, "human", text_message)
            save_message(phone_number, "ai", f"[IA detectada pela heurística: {motivo_ai}]")
            from tools.manage_leads import block_lead_as_ai
            block_lead_as_ai(phone_number, f"heurística: {motivo_ai}")
            _save_session_log(phone_number)
            return
    except Exception as e:
        log(f"[AI_DETECT] Erro na heurística (seguindo fluxo normal): {e}")

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
    contexto_lead += "\nUse o campo **Nome** para chamar o lead (só se estiver preenchido). O campo **Nome no WhatsApp** é apenas para uso em criar_evento — não o use para chamar o lead."

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
            function_calls = []
            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)

            if not function_calls:
                break  # Sem function calls — resposta final de texto

            tool_round += 1
            log(f"[TOOL GEMINI] Round {tool_round}: chamadas {[fc.name for fc in function_calls]}")

            function_responses = []
            for fc in function_calls:
                fn_name = fc.name
                fn_args = dict(fc.args) if fc.args else {}
                log(f"[TOOL {fn_name.upper()}] Executando({fn_args})")

                if fn_name == "lead_agendou":
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
                    from tools.manage_followups import cancel_followups
                    cancel_followups(fn_args.get("telefone", phone_number))
                    result = {"success": True, "message": "Follow-ups cancelados"}
                    evento_criado = True
                else:
                    dispatch = TOOL_DISPATCH.get(fn_name)
                    if dispatch:
                        result = dispatch(fn_args)
                        if fn_name == "criar_evento" and result.get("event_id"):
                            save_lead_info(phone_number, {"event_id": result["event_id"]})
                        if fn_name == "deleta_evento" and result.get("success"):
                            save_lead_info(phone_number, {"event_id": ""})
                    else:
                        result = {"error": f"Tool '{fn_name}' não encontrada"}

                _status = "SUCESSO" if (isinstance(result, dict) and (result.get("success") or result.get("event_id") or result.get("slots_disponiveis") is not None or result.get("bookedSlots") is not None)) else ("FALHA" if isinstance(result, dict) and (result.get("error") or result.get("success") is False) else "OK")
                log(f"[TOOL {fn_name.upper()}] Resultado: {_status} - {json.dumps(result, ensure_ascii=False)[:250]}")
                function_responses.append(
                    types.Part(function_response=types.FunctionResponse(
                        name=fn_name,
                        response=result
                    ))
                )

            response = chat.send_message(function_responses)

        resposta_ai = response.text or ""

        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count
            total_tokens = input_tokens + output_tokens
            log(f"[TOKENS] Entrada: {input_tokens} | Saída: {output_tokens} | Total: {total_tokens}")

    except Exception as e:
        log(f"[LLM] Erro: {e}. Mensagem retornada à fila.")
        _save_session_log(phone_number)
        raise  # Propaga para o callback fazer nack → requeue
    # ------------------------------------------------------------------------------------------

    # 0. Checa MENSAGEM AUTOMATICA (ignora auto-replies detectados pelo LLM)
    match_auto = re.search(r'<IGNORAR_AUTO>(.*?)</IGNORAR_AUTO>', resposta_ai, re.IGNORECASE)
    if match_auto:
        motivo_auto = match_auto.group(1).strip()
        log(f"[AUTO] Mensagem automática detectada: {motivo_auto}. Ignorando e mantendo follow-ups.")
        save_message(phone_number, "ai", f"[auto-reply ignorada: {motivo_auto}]")
        _save_session_log(phone_number)
        return  # NÃO reseta follow-ups — auto-reply não conta como resposta humana

    # 0b. Checa IA DO OUTRO LADO (Mya identificou que o "lead" é outra IA)
    match_ia = re.search(r'<IGNORAR_IA>(.*?)</IGNORAR_IA>', resposta_ai, re.IGNORECASE)
    if match_ia:
        motivo_ia = match_ia.group(1).strip() or "sinais de IA detectados pelo LLM"
        log(f"[AI_DETECT] LLM detectou IA em {phone_number}: {motivo_ia}")
        save_message(phone_number, "ai", f"[IA detectada pelo LLM: {motivo_ia}]")
        from tools.manage_leads import block_lead_as_ai
        block_lead_as_ai(phone_number, f"LLM: {motivo_ia}")
        _save_session_log(phone_number)
        return  # NÃO responde, NÃO agenda follow-up — conversa encerrada

    # Mensagem humana confirmada → reseta timer de follow-ups
    from tools.manage_followups import reset_followup_timer
    reset_followup_timer(phone_number)

    # 1. Checa NOME (via tag XML → fallback padrão 'Muito prazer' → fallback histórico)
    match = re.search(r'<SAVE_NAME>(.*?)</SAVE_NAME>', resposta_ai, re.IGNORECASE)
    if match:
        nome_lead = match.group(1).strip()
        save_lead_info(phone_number, {"nome": nome_lead, "whatsapp": phone_number})
        nome_conhecido = nome_lead  # Atualiza local para schedule_followups usar o valor correto
        log(f"[LEAD] Nome salvo via tag: {nome_lead}")
        resposta_ai = re.sub(r'<SAVE_NAME>.*?</SAVE_NAME>', '', resposta_ai, flags=re.IGNORECASE).strip()
    elif not nome_conhecido:
        # Fallback 1: detecta 'Muito prazer, X!' — sem IGNORECASE para exigir inicial maiúscula (evita capturar verbos como "ajudar")
        fallback_texto = re.search(r'(?:muito prazer|prazer)[,!\s]+([A-ZÀ-Ú][a-zà-ú]+)', resposta_ai)
        if fallback_texto:
            nome_lead = fallback_texto.group(1).strip()
            save_lead_info(phone_number, {"nome": nome_lead, "whatsapp": phone_number})
            nome_conhecido = nome_lead  # Atualiza local
            log(f"[LEAD] Nome salvo via fallback texto: {nome_lead}")
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
                        log(f"[LEAD] Nome salvo via histórico: {ultima_humana.title()}")

    # 2a. Checa NICHO (via tag <SAVE_NICHO> — apenas 2-3 palavras do negócio)
    match_nicho = re.search(r'<SAVE_NICHO>(.*?)</SAVE_NICHO>', resposta_ai, re.IGNORECASE)
    if match_nicho:
        nicho_tag = match_nicho.group(1).strip()
        save_lead_info(phone_number, {"nicho": nicho_tag})
        resposta_ai = re.sub(r'<SAVE_NICHO>.*?</SAVE_NICHO>', '', resposta_ai, flags=re.IGNORECASE).strip()
        log(f"[LEAD] Nicho salvo via tag: {nicho_tag}")
        nicho_conhecido = nicho_tag
    elif '[PDF_APRESENTACAO]' in resposta_ai and not nicho_conhecido:
        # Fallback: quando a IA dispara o PDF, a última mensagem humana era o nicho
        msgs_humanas = [m for m in historico[:-1] if m.get("type") == "human"]
        if msgs_humanas:
            nicho_detectado = msgs_humanas[-1].get("data", {}).get("content", "").strip()
            if nicho_detectado:
                save_lead_info(phone_number, {"nicho": nicho_detectado})
                log(f"[LEAD] Nicho salvo via histórico (fallback PDF): {nicho_detectado}")
                nicho_conhecido = nicho_detectado  # Atualiza para usar no Sheets abaixo

    # 2b. Checa RESUMO (via tag <SAVE_RESUMO> — atualiza após toda troca de mensagem)
    match_resumo = re.search(r'<SAVE_RESUMO>(.*?)</SAVE_RESUMO>', resposta_ai, re.IGNORECASE)
    resumo_texto = ""
    if match_resumo:
        resumo_texto = match_resumo.group(1).strip()
        save_lead_info(phone_number, {"resumo": resumo_texto})
        resposta_ai = re.sub(r'<SAVE_RESUMO>.*?</SAVE_RESUMO>', '', resposta_ai, flags=re.IGNORECASE).strip()
        log(f"[LEAD] Resumo atualizado: {resumo_texto}")

    # Salva/atualiza na planilha Google Sheets a cada mensagem (upsert por telefone)
    log(f"[TOOL SHEETS] Executando save_lead_to_sheet(phone={phone_number})")
    try:
        from tools.save_to_sheets import save_lead_to_sheet
        lead_info_atual = get_lead_info(phone_number)
        save_lead_to_sheet(
            phone=phone_number,
            name=lead_info_atual.get("nome", push_name),
            niche=lead_info_atual.get("nicho", ""),
            resumo=resumo_texto or lead_info_atual.get("resumo", "")
        )
        log(f"[TOOL SHEETS] Resultado: SUCESSO (upsert por telefone)")
    except Exception as e:
        log(f"[TOOL SHEETS] Resultado: FALHA (não crítico) - {e}")


    # 2c. Checa LEAD_INTERESSADO (envia lead ao CRM Básico — 1x por telefone)
    match_interesse = re.search(r'<LEAD_INTERESSADO\s*/?>', resposta_ai, re.IGNORECASE)
    if match_interesse:
        resposta_ai = re.sub(r'<LEAD_INTERESSADO\s*/?>', '', resposta_ai, flags=re.IGNORECASE).strip()
        crm_args = {"phone": phone_number, "name": nome_conhecido or push_name, "company": push_name}
        log(f"[TOOL CRM] Tag <LEAD_INTERESSADO> detectada - Executando send_lead_to_crm({crm_args})")
        try:
            from tools.crm_api import send_lead_to_crm
            resultado_crm = send_lead_to_crm(**crm_args)
            if resultado_crm.get("success"):
                log(f"[TOOL CRM] Resultado: SUCESSO (status {resultado_crm.get('status')}) - lead enviado ao CRM Básico")
            elif resultado_crm.get("skipped"):
                log(f"[TOOL CRM] Resultado: IGNORADO - lead {phone_number} já havia sido enviado anteriormente")
            else:
                log(f"[TOOL CRM] Resultado: FALHA - {json.dumps(resultado_crm, ensure_ascii=False)[:300]}")
        except Exception as e:
            log(f"[TOOL CRM] Resultado: EXCEÇÃO - {e}")
    else:
        log(f"[TOOL CRM] Tag <LEAD_INTERESSADO> não emitida pela IA - CRM não acionado nesta mensagem")

    # 3. Checa SEM_INTERESSE (lead recusou definitivamente → cancela follow-ups)
    match_sem_interesse = re.search(r'<SEM_INTERESSE\s*/?>', resposta_ai, re.IGNORECASE)
    sem_interesse = False
    if match_sem_interesse:
        log(f"[TOOL FOLLOWUP] Executando cancel_followups(telefone={phone_number}) [origem: SEM_INTERESSE]")
        from tools.manage_followups import cancel_followups as cancel_fu_si
        cancel_fu_si(phone_number)
        sem_interesse = True
        resposta_ai = re.sub(r'<SEM_INTERESSE\s*/?>', '', resposta_ai, flags=re.IGNORECASE).strip()
        log(f"[TOOL FOLLOWUP] Resultado: SUCESSO - lead {phone_number} sem interesse, follow-ups cancelados")

    # 4. Checa ALARME DE EQUIPE (Atendimento Humano)
    match_humano = re.search(r'<ATENDIMENTO_HUMANO>(.*?)</ATENDIMENTO_HUMANO>', resposta_ai, re.IGNORECASE)
    if match_humano:
        motivo = match_humano.group(1).strip()
        log(f"[TOOL ALERTA_EQUIPE] Executando(motivo={motivo})")
        from tools.send_whatsapp import send_message as sms_raw
        ok_alerta = sms_raw("5511989887525@s.whatsapp.net", f"🚨 *MYA DISPARO LEAD ALERTA* 🚨\nLead: {push_name} ({phone_number})\nMotivo: {motivo}")
        log(f"[TOOL ALERTA_EQUIPE] Resultado: {'SUCESSO - equipe notificada' if ok_alerta else 'FALHA - Uazapi não entregou o alerta'}")
        resposta_ai = re.sub(r'<ATENDIMENTO_HUMANO>.*?</ATENDIMENTO_HUMANO>', '', resposta_ai, flags=re.IGNORECASE).strip()
        # Cancela follow-ups se o lead fechou reunião
        log(f"[TOOL FOLLOWUP] Executando cancel_followups(telefone={phone_number}) [origem: ATENDIMENTO_HUMANO]")
        from tools.manage_followups import cancel_followups as cancel_fu
        cancel_fu(phone_number)
        log(f"[TOOL FOLLOWUP] Resultado: SUCESSO - follow-ups cancelados")

    # 5. Salva a resposta gerada inteira (já limpa da tag) no Redis Histórico
    save_message(phone_number, "ai", resposta_ai.replace("[PDF_APRESENTACAO]", "").strip())

    # 6. FATIADOR DE MENSAGENS E DELAY HUMANO COORDENADO
    mensagens = [m.strip() for m in resposta_ai.split("\n\n") if m.strip()]

    for indice, msg_text in enumerate(mensagens):
        if "[PDF_APRESENTACAO]" in msg_text:
            clean_text = msg_text.replace("[PDF_APRESENTACAO]", "").strip()
            if clean_text:
                send_message(f"{phone_number}@s.whatsapp.net", clean_text)
                time.sleep(2)

            log(f"[TOOL PDF] Executando send_pdf(destino={phone_number}, arquivo=apresentacao_mya.pdf)")
            from tools.send_media import send_pdf
            ok_pdf = send_pdf(phone_number + "@s.whatsapp.net", "apresentacao_mya.pdf")
            log(f"[TOOL PDF] Resultado: {'SUCESSO - PDF entregue via Uazapi' if ok_pdf else 'FALHA - Uazapi não entregou o PDF'}")
            time.sleep(4) # Tempo de respiro de documento
        else:
            sucesso = send_message(f"{phone_number}@s.whatsapp.net", msg_text)
            if not sucesso:
                log("[TOOL WHATSAPP] Resultado: FALHA - Uazapi não entregou a mensagem")
            elif indice < len(mensagens) - 1:
                time.sleep(2)

    # 7. AGENDA FOLLOW-UPS após toda resposta enviada (reseta timer se já existiam)
    # Não agenda se: acabou de confirmar reunião via tag OU via function calling (evento_criado) OU lead sem interesse
    if not match_humano and not evento_criado and not sem_interesse:
        log(f"[TOOL FOLLOWUP] Executando schedule_followups(telefone={phone_number}, nome={nome_conhecido}, nicho={nicho_conhecido})")
        from tools.manage_followups import schedule_followups
        schedule_followups(phone_number, nome=nome_conhecido, nicho=nicho_conhecido)
        log(f"[TOOL FOLLOWUP] Resultado: SUCESSO - follow-ups agendados/resetados para {phone_number}")

    _save_session_log(phone_number)

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
