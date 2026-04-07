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


def criar_evento(data: str, horario: str, nome: str, email: str, telefone: str = "") -> dict:
    """
    Cria um evento de 30 minutos no Google Calendar.

    Args:
        data: Data no formato "YYYY-MM-DD"
        horario: Horário de início no formato "HH:MM"
        nome: Nome completo do lead
        email: Email do lead
        telefone: Telefone do lead (opcional)

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

    descricao = f"Reunião agendada via Mya (WhatsApp)\nNome: {nome}\nEmail: {email}"
    if telefone:
        descricao += f"\nTelefone: {telefone}"

    event_body = {
        "summary": f"Demo IA - {nome}",
        "description": descricao,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "America/Sao_Paulo"
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "America/Sao_Paulo"
        },
        "attendees": [{"email": email}] if email else [],
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
            sendUpdates="all"
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
