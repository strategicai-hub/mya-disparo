"""
Detector heuristico de respostas de IA (pre-LLM).

Objetivo: filtro barato que pega os casos mais obvios (saudacao de atendente virtual,
markdown de chatbot, etc.) antes mesmo de chamar o Gemini.

Casos sutis (IA conversacional bem treinada) ficam para o LLM detectar via a
tag <IGNORAR_IA> descrita no workflow sdr_mya.md.
"""
import re


# Frases quase sempre emitidas por bots / atendentes virtuais corporativos
_PHRASE_PATTERNS = [
    r"\bcomo posso (te )?ajud(a|á)(-lo|-la|lo|la)?\b",
    r"\bem que posso (te )?(ajudar|auxili(a|á)(-lo|-la|lo|la)?)\b",
    r"\bestou (aqui )?(à|a) (sua )?disposi(ç|c)(ã|a)o\b",
    r"\bfico (à|a) (sua )?disposi(ç|c)(ã|a)o\b",
    r"\bser(á|a) um prazer (te )?atend(er|ê-lo|e-lo|ê-la|e-la)\b",
    r"\bagrade(ç|c)o (pelo|o seu) contato\b",
    r"\bsou (um(a)? )?(assistente virtual|atendente virtual|chatbot|bot)\b",
    r"\batenciosamente,?\s*$",
    r"\bcordialmente,?\s*$",
    r"\bprezado(a|\(a\))?\b",
    r"\bolá,?\s*(tudo bem|como vai|como está|seja bem[- ]vindo)",
]

_PHRASE_RES = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in _PHRASE_PATTERNS]

# Marcacao de markdown / bot (bullets, numeracao, negrito com *)
_MARKDOWN_BULLET_RE = re.compile(r"^\s*([-•*]|\d+[.)])\s+\S", re.MULTILINE)
_BOLD_MARKDOWN_RE = re.compile(r"\*\*[^*\n]+\*\*")


def detect(text: str) -> tuple[bool, str]:
    """
    Roda heuristica rapida. Retorna (is_ai, motivo).
    So retorna True quando a evidencia for forte — falso positivo fode UX.
    """
    if not text or len(text.strip()) < 3:
        return False, ""

    t = text.strip()

    # 1) Frase denunciadora unica é suficiente (sao frases raríssimas em leads humanos reais)
    for pat in _PHRASE_RES:
        m = pat.search(t)
        if m:
            return True, f"frase de atendente virtual: '{m.group(0)[:60]}'"

    # 2) Markdown bullets + negrito juntos numa mensagem curta = quase certeza de bot
    bullets = _MARKDOWN_BULLET_RE.findall(t)
    bolds = _BOLD_MARKDOWN_RE.findall(t)
    if len(bullets) >= 2 and len(bolds) >= 1:
        return True, f"markdown de bot: {len(bullets)} bullets + negrito"

    # 3) Listas numeradas longas (3+ itens) — incomum em WhatsApp humano
    if len(bullets) >= 3:
        return True, f"lista estruturada ({len(bullets)} itens) em WhatsApp"

    return False, ""
