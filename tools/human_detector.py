"""Classificador LLM dedicado: a mensagem do lead foi escrita por um HUMANO?

Chamado apenas no primeiro inbound do lead (após o disparo), em paralelo ao
fluxo principal, para evitar latência. Usa gemini-2.5-flash com thinking=0.

Retorna (is_human: bool, confianca: str, motivo: str).
"""
import os
from google import genai
from google.genai import types as gtypes

_API_KEY = os.getenv("LLM_API_KEY")
_client = genai.Client(api_key=_API_KEY) if _API_KEY else None

_PROMPT = """Você é um classificador binário. Receberá UMA mensagem que um número de WhatsApp respondeu logo após receber um disparo comercial. Decida se foi um HUMANO ou um BOT/AUTORESPOSTA/IA.

Sinais de BOT/AUTO/IA:
- Resposta em poucos segundos após o disparo (tempo absoluto será informado)
- Frases típicas de atendente virtual ("como posso ajudar", "estou à disposição", "agradecemos o contato", "vou te direcionar", "aguarde um momento")
- Markdown estruturado (bullets, **negrito**, listas numeradas)
- Mensagem genérica corporativa sem traço de pessoalidade
- Saudação automática ("Olá, este é o atendimento da X", "Bem-vindo à empresa Y")
- Mensagem de ausência ("estamos fora do horário", "responderemos em breve")
- Resposta excessivamente formal/longa para um WhatsApp casual

Sinais de HUMANO:
- Erros de digitação, abreviações ("vc", "tb", "pq", "kkk", "rs")
- Pergunta específica sobre o disparo ("o que é isso?", "como funciona?", "quanto custa?")
- Resposta curta conversacional ("oi", "manda detalhes", "to interessado")
- Linguagem informal, gírias, emojis casuais
- Demonstra leitura/reação ao disparo específico (cita o produto, o nicho, etc.)
- Tempo de resposta > 30 segundos é forte indício humano (mas humanos também respondem rápido às vezes)

Regra de decisão:
- Tempo < 5s + sem sinais claros de humano → BOT
- Frase de atendente virtual presente → BOT
- Markdown estruturado → BOT
- Caso contrário, com sinais de humano → HUMANO
- Em dúvida → BOT (preferimos falso negativo a alerta falso)

Responda APENAS no formato:
RESULTADO: <HUMANO|BOT>
CONFIANCA: <ALTA|MEDIA|BAIXA>
MOTIVO: <frase curta justificando>"""


def detect_human(text: str, elapsed_seconds: float | None) -> tuple[bool, str, str]:
    """Classifica se a mensagem foi escrita por um humano.

    Args:
        text: mensagem recebida do lead
        elapsed_seconds: segundos entre o disparo e a resposta (None se desconhecido)

    Returns:
        (is_human, confianca, motivo)
    """
    if not _client or not text or not text.strip():
        return False, "BAIXA", "input vazio ou client LLM indisponível"

    tempo_str = f"{elapsed_seconds:.1f}s" if elapsed_seconds is not None else "desconhecido"
    user_msg = (
        f"Tempo entre disparo e resposta: {tempo_str}\n"
        f"Mensagem recebida: \"\"\"{text.strip()[:800]}\"\"\""
    )

    try:
        response = _client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_msg,
            config=gtypes.GenerateContentConfig(
                system_instruction=_PROMPT,
                temperature=0.1,
                max_output_tokens=120,
                thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
            ),
        )
        out = (response.text or "").strip()
    except Exception as e:
        return False, "BAIXA", f"erro LLM: {e}"

    resultado = "BOT"
    confianca = "BAIXA"
    motivo = ""
    for line in out.splitlines():
        line = line.strip()
        if line.upper().startswith("RESULTADO:"):
            resultado = line.split(":", 1)[1].strip().upper()
        elif line.upper().startswith("CONFIANCA:") or line.upper().startswith("CONFIANÇA:"):
            confianca = line.split(":", 1)[1].strip().upper()
        elif line.upper().startswith("MOTIVO:"):
            motivo = line.split(":", 1)[1].strip()

    is_human = resultado.startswith("HUMANO")
    return is_human, confianca, motivo or out[:200]
