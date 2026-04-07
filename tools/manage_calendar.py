import os
import json
from datetime import datetime, timedelta, timezone
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SAO_PAULO_TZ = timezone(timedelta(hours=-3))

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
IMPERSONATE_USER = os.getenv("GOOGLE_IMPERSONATE_USER", "")

# Horários de atendimento por dia da semana (0=seg, 6=dom)
# Cada dia tem uma lista de (inicio_hora, fim_hora)
HORARIOS_ATENDIMENTO = {
    0: [(7, 12), (14, 20)],   # Segunda
    1: [(7, 12), (14, 20)],   # Terça
    2: [(7, 12), (14, 20)],   # Quarta
    3: [(7, 12), (14, 20)],   # Quinta
    4: [(7, 12), (14, 20)],   # Sexta
    5: [(8, 12)],              # Sábado
    6: [],                     # Domingo — fechado
}

SLOT_DURACAO_MIN = 30
GAP_MIN = 15           # Intervalo obrigatório entre eventos
ANTECEDENCIA_HORAS = 4  # Mínimo de antecedência para agendar


def _get_calendar_service():
    """Autentica e retorna o serviço Google Calendar."""
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    creds_path = os.path.join(os.getcwd(), "google_credentials.json")

    if creds_json_str:
        creds_info = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    elif os.path.exists(creds_path):
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    else:
        print("[CALENDAR] ERRO: Nenhuma credencial Google encontrada.")
        return None

    # Domain-Wide Delegation: service account age em nome do usuário do Workspace
    if IMPERSONATE_USER:
        creds = creds.with_subject(IMPERSONATE_USER)

    return build("calendar", "v3", credentials=creds)


def consulta_disponibilidade(data: str) -> dict:
    """
    Consulta os horários ocupados de um dia específico.

    Args:
        data: Data no formato "YYYY-MM-DD"

    Returns:
        Dict com 'date', 'weekday', 'bookedSlots' (lista de {start, end}),
        'horarios_atendimento' e 'now' (horário atual em SP).
    """
    service = _get_calendar_service()
    if not service:
        return {"error": "Não foi possível conectar ao Google Calendar"}

    try:
        target_date = datetime.strptime(data, "%Y-%m-%d")
    except ValueError:
        return {"error": f"Data inválida: {data}. Use o formato YYYY-MM-DD"}

    weekday = target_date.weekday()
    periodos = HORARIOS_ATENDIMENTO.get(weekday, [])

    if not periodos:
        dia_nome = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][weekday]
        return {
            "date": data,
            "weekday": dia_nome,
            "bookedSlots": [],
            "horarios_atendimento": [],
            "message": f"Não há atendimento na {dia_nome}."
        }

    # Range do dia inteiro para buscar eventos
    day_start = target_date.replace(hour=0, minute=0, second=0, tzinfo=SAO_PAULO_TZ)
    day_end = target_date.replace(hour=23, minute=59, second=59, tzinfo=SAO_PAULO_TZ)

    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()
    except Exception as e:
        return {"error": f"Erro ao consultar Google Calendar: {e}"}

    events = events_result.get("items", [])
    booked = []
    for ev in events:
        start = ev["start"].get("dateTime", ev["start"].get("date", ""))
        end = ev["end"].get("dateTime", ev["end"].get("date", ""))
        booked.append({
            "start": start,
            "end": end,
            "summary": ev.get("summary", "(sem título)")
        })

    now_sp = datetime.now(SAO_PAULO_TZ)
    dia_nome = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][weekday]

    horarios_str = []
    for inicio, fim in periodos:
        horarios_str.append(f"{inicio:02d}:00-{fim:02d}:00")

    return {
        "date": data,
        "weekday": dia_nome,
        "bookedSlots": booked,
        "horarios_atendimento": horarios_str,
        "now": now_sp.strftime("%Y-%m-%d %H:%M"),
        "regras": f"Slots de {SLOT_DURACAO_MIN}min. Gap de {GAP_MIN}min entre eventos. Antecedência mínima de {ANTECEDENCIA_HORAS}h."
    }


def criar_evento(data: str, horario: str, nome: str, email: str, telefone: str = "", nicho: str = "", wa_name: str = "") -> dict:
    """
    Cria um evento de 30 minutos no Google Calendar.

    Args:
        data: Data no formato "YYYY-MM-DD"
        horario: Horário de início no formato "HH:MM"
        nome: Nome completo do lead
        email: Email do lead
        telefone: Telefone do lead (opcional)
        nicho: Nicho/segmento do lead (opcional)
        wa_name: Nome salvo no WhatsApp / nome da empresa (opcional)

    Returns:
        Dict com 'event_id', 'start', 'end' ou 'error'.
    """
    service = _get_calendar_service()
    if not service:
        return {"error": "Não foi possível conectar ao Google Calendar"}

    try:
        start_dt = datetime.strptime(f"{data} {horario}", "%Y-%m-%d %H:%M")
        start_dt = start_dt.replace(tzinfo=SAO_PAULO_TZ)
    except ValueError:
        return {"error": f"Data/horário inválido: {data} {horario}"}

    end_dt = start_dt + timedelta(minutes=SLOT_DURACAO_MIN)

    # Formata telefone: remove espaços/hífens e garante prefixo 55
    telefone_fmt = telefone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if telefone_fmt and not telefone_fmt.startswith("55"):
        telefone_fmt = "55" + telefone_fmt

    descricao_linhas = []
    if telefone_fmt:
        descricao_linhas.append(f"Whatsapp: {telefone_fmt}")
    if nicho:
        descricao_linhas.append(f"Nicho: {nicho}")
    if wa_name:
        descricao_linhas.append(f"Empresa: {wa_name}")
    if email:
        descricao_linhas.append(f"Email: {email}")
    descricao = "\n".join(descricao_linhas)

    event_body = {
        "summary": f"Reunião SAI - {nome}",
        "description": descricao,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "America/Sao_Paulo"
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "America/Sao_Paulo"
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 30},
            ]
        }
    }

    try:
        event = service.events().insert(
            calendarId=CALENDAR_ID,
            body=event_body,
            sendUpdates="none"
        ).execute()

        event_id = event.get("id", "")
        print(f"[CALENDAR] Evento criado: {event_id} — {nome} em {data} {horario}")

        return {
            "event_id": event_id,
            "start": start_dt.strftime("%Y-%m-%d %H:%M"),
            "end": end_dt.strftime("%Y-%m-%d %H:%M"),
            "summary": event.get("summary", ""),
            "link": event.get("htmlLink", "")
        }
    except Exception as e:
        print(f"[CALENDAR] Erro ao criar evento: {e}")
        return {"error": f"Erro ao criar evento: {e}"}


def consulta_id(telefone: str, data: str = "") -> dict:
    """
    Busca eventos pelo telefone do lead (na descrição do evento).

    Args:
        telefone: Telefone do lead
        data: Data opcional no formato "YYYY-MM-DD" para filtrar

    Returns:
        Dict com lista de 'events' encontrados.
    """
    service = _get_calendar_service()
    if not service:
        return {"error": "Não foi possível conectar ao Google Calendar"}

    now_sp = datetime.now(SAO_PAULO_TZ)

    if data:
        try:
            target = datetime.strptime(data, "%Y-%m-%d")
            time_min = target.replace(hour=0, minute=0, tzinfo=SAO_PAULO_TZ)
            time_max = target.replace(hour=23, minute=59, tzinfo=SAO_PAULO_TZ)
        except ValueError:
            time_min = now_sp - timedelta(days=30)
            time_max = now_sp + timedelta(days=60)
    else:
        time_min = now_sp - timedelta(days=30)
        time_max = now_sp + timedelta(days=60)

    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            q=telefone
        ).execute()
    except Exception as e:
        return {"error": f"Erro ao buscar eventos: {e}"}

    events = events_result.get("items", [])
    found = []
    for ev in events:
        found.append({
            "event_id": ev.get("id", ""),
            "summary": ev.get("summary", ""),
            "start": ev["start"].get("dateTime", ev["start"].get("date", "")),
            "end": ev["end"].get("dateTime", ev["end"].get("date", "")),
        })

    return {"events": found, "total": len(found)}


def consulta_proximos_horarios(data_inicio: str, quantidade: int = 3) -> dict:
    """
    Busca os próximos N horários disponíveis a partir de data_inicio, iterando dia a dia.
    Respeita horários de atendimento, eventos agendados, gap de 15min e antecedência de 4h.

    Args:
        data_inicio: Data de início da busca no formato "YYYY-MM-DD"
        quantidade: Número de slots a retornar (padrão 3)

    Returns:
        Dict com 'slots_disponiveis' (lista de {date, weekday, start, end}), 'total' e metadados.
    """
    service = _get_calendar_service()
    if not service:
        return {"error": "Não foi possível conectar ao Google Calendar"}

    try:
        start_date = datetime.strptime(data_inicio, "%Y-%m-%d")
    except ValueError:
        return {"error": f"Data inválida: {data_inicio}. Use o formato YYYY-MM-DD"}

    now_sp = datetime.now(SAO_PAULO_TZ)
    min_start = now_sp + timedelta(hours=ANTECEDENCIA_HORAS)

    available = []
    current_date = datetime(start_date.year, start_date.month, start_date.day, tzinfo=SAO_PAULO_TZ)

    for _ in range(30):
        if len(available) >= quantidade:
            break

        weekday = current_date.weekday()
        periodos = HORARIOS_ATENDIMENTO.get(weekday, [])

        if periodos:
            day_start = current_date
            day_end = current_date.replace(hour=23, minute=59, second=59)

            try:
                events_result = service.events().list(
                    calendarId=CALENDAR_ID,
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime"
                ).execute()
                events = events_result.get("items", [])
            except Exception:
                events = []

            booked = []
            for ev in events:
                start_str = ev["start"].get("dateTime", "")
                end_str = ev["end"].get("dateTime", "")
                if start_str and end_str:
                    try:
                        ev_start = datetime.fromisoformat(start_str).astimezone(SAO_PAULO_TZ)
                        ev_end = datetime.fromisoformat(end_str).astimezone(SAO_PAULO_TZ)
                        booked.append((ev_start, ev_end))
                    except Exception:
                        pass

            weekday_name = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][weekday]

            for inicio_h, fim_h in periodos:
                slot_time = current_date.replace(hour=inicio_h, minute=0, second=0, microsecond=0)
                period_end = current_date.replace(hour=fim_h, minute=0, second=0, microsecond=0)

                while slot_time + timedelta(minutes=SLOT_DURACAO_MIN) <= period_end:
                    slot_end = slot_time + timedelta(minutes=SLOT_DURACAO_MIN)

                    if slot_time < min_start:
                        slot_time += timedelta(minutes=SLOT_DURACAO_MIN)
                        continue

                    conflict = False
                    for ev_start, ev_end in booked:
                        if (slot_time < ev_end + timedelta(minutes=GAP_MIN) and
                                slot_end > ev_start - timedelta(minutes=GAP_MIN)):
                            conflict = True
                            break

                    if not conflict:
                        available.append({
                            "date": current_date.strftime("%Y-%m-%d"),
                            "weekday": weekday_name,
                            "start": slot_time.strftime("%H:%M"),
                            "end": slot_end.strftime("%H:%M"),
                        })
                        if len(available) >= quantidade:
                            break

                    slot_time += timedelta(minutes=SLOT_DURACAO_MIN)

                if len(available) >= quantidade:
                    break

        current_date += timedelta(days=1)

    return {
        "slots_disponiveis": available,
        "total": len(available),
        "pesquisado_a_partir_de": data_inicio,
        "now": now_sp.strftime("%Y-%m-%d %H:%M"),
        "regras": f"Slots de {SLOT_DURACAO_MIN}min. Gap de {GAP_MIN}min entre eventos. Antecedência mínima de {ANTECEDENCIA_HORAS}h.",
        "instrucao": "Use 'date' e 'start' ao chamar criar_evento. Se não há slots para o dia pedido, informe o lead e ofereça os próximos disponíveis."
    }


def deleta_evento(event_id: str) -> dict:
    """
    Deleta um evento do Google Calendar pelo ID.

    Args:
        event_id: ID do evento no Google Calendar

    Returns:
        Dict com 'success' ou 'error'.
    """
    service = _get_calendar_service()
    if not service:
        return {"error": "Não foi possível conectar ao Google Calendar"}

    try:
        service.events().delete(
            calendarId=CALENDAR_ID,
            eventId=event_id,
            sendUpdates="all"
        ).execute()
        print(f"[CALENDAR] Evento {event_id} deletado com sucesso")
        return {"success": True, "message": f"Evento {event_id} cancelado com sucesso"}
    except Exception as e:
        print(f"[CALENDAR] Erro ao deletar evento: {e}")
        return {"error": f"Erro ao deletar evento: {e}"}
