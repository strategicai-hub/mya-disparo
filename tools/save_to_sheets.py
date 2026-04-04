import os
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file"
]

SPREADSHEET_ID = "NOVO_SPREADSHEET_ID"
CREDENTIALS_PATH = os.path.join(os.getcwd(), "google_credentials.json")

HEADERS = ["Data/Hora", "WhatsApp", "Nome", "Nicho", "Resumo da Conversa"]


def get_sheet():
    """Autentica e retorna a primeira aba da planilha."""
    try:
        creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if creds_json_str:
            creds_info = json.loads(creds_json_str)
            creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        elif os.path.exists(CREDENTIALS_PATH):
            creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        else:
            print("[SHEETS] ERRO: Nenhuma credencial Google encontrada.")
            return None

        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        return spreadsheet.sheet1
    except Exception as e:
        print(f"[SHEETS] ERRO ao conectar na planilha: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None


def save_lead_to_sheet(phone: str, name: str = "", niche: str = "", resumo: str = ""):
    """
    Upsert do lead na planilha: atualiza a linha se o telefone já existe, senão cria nova.
    Colunas: Data/Hora | WhatsApp | Nome | Nicho | Resumo da Conversa
    """
    sheet = get_sheet()
    if not sheet:
        return

    try:
        all_rows = sheet.get_all_values()

        # Garante cabeçalho
        if not all_rows:
            sheet.append_row(HEADERS)
            all_rows = [HEADERS]

        # Garante que o cabeçalho está correto (migração de versões antigas)
        if all_rows[0] != HEADERS:
            sheet.delete_rows(1)
            sheet.insert_row(HEADERS, 1)
            all_rows = sheet.get_all_values()

        # Procura linha existente pelo telefone (coluna B = índice 1)
        existing_row_index = None
        for i, row in enumerate(all_rows):
            if len(row) > 1 and row[1] == phone:
                existing_row_index = i + 1  # gspread usa índice 1-based
                break

        now = datetime.now().strftime("%d/%m/%Y %H:%M")

        if existing_row_index:
            # Atualiza campos não-vazios na linha existente
            existing = all_rows[existing_row_index - 1]
            updated_name   = name  if name  else (existing[2] if len(existing) > 2 else "")
            updated_niche  = niche if niche else (existing[3] if len(existing) > 3 else "")
            updated_resumo = resumo if resumo else (existing[4] if len(existing) > 4 else "")

            sheet.update(f"A{existing_row_index}:E{existing_row_index}", [[
                now, phone, updated_name, updated_niche, updated_resumo
            ]])
            print(f"[SHEETS] Lead {updated_name} ({phone}) atualizado na planilha.")
        else:
            sheet.append_row([now, phone, name, niche, resumo])
            print(f"[SHEETS] Lead {name} ({phone}) inserido na planilha.")

    except Exception as e:
        print(f"[SHEETS] ERRO ao salvar lead na planilha: {e}")
