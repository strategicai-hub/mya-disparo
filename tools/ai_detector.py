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
    # Fallbacks classicos de bots BR quando nao entendem o input
    r"\bn(ã|a)o entendi[,.!\s]+(pode|poderia) (repetir|reformular)\b",
    r"\bn(ã|a)o entendi (sua|a) (resposta|mensagem|pergunta)\b",
    r"\bdesculp(e|a)[,.!\s]+n(ã|a)o entendi\b",
    # Transferencia para atendente humano (padrao corporativo)
    r"\best(arei|ou) (te )?direcionando\b",
    r"\bvou (te )?(direcionar|transferir|encaminhar) (para|ao|à)\b",
    r"\b(direciona(do|ndo)|transferi(do|ndo)|encaminha(do|ndo)) (para|ao|à) (uma de )?(nossa|nossas) equipe\b",
    r"\b(um|uma) (de )?nossos? (atendentes?|agentes?|especialistas?|colaboradores?)\b.{0,40}\b(vai|ir(á|a)|entrar(á|a)?) (em contato|te atender|falar com)",
    r"\baguarde(,)? (um momento|um instante|por favor)\b",
]

_PHRASE_RES = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in _PHRASE_PATTERNS]

# Marcacao de markdown / bot (bullets, numeracao, negrito com *)
_MARKDOWN_BULLET_RE = re.compile(r"^\s*([-•*]|\d+[.)])\s+\S", re.MULTILINE)
_BOLD_MARKDOWN_RE = re.compile(r"\*\*[^*\n]+\*\*")

# Frases genericas curtas que SOZINHAS nao denunciam, mas em resposta <8s + historico ja suspeito = bot
_WEAK_GENERIC_PATTERNS = [
    r"\bn(ã|a)o entendi\b",
    r"\b(pode|poderia) repetir\b",
    r"\b(pode|poderia) reformular\b",
    r"\bentendido[,.!\s]*$",
    r"\bperfeito[,.!\s]*$",
    r"\bcerto[,.!\s]*$",
    r"\bok[,.!\s]*$",
]
_WEAK_GENERIC_RES = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in _WEAK_GENERIC_PATTERNS]


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


def detect_weak_generic(text: str) -> tuple[bool, str]:
    """
    Detecta frases genericas curtas (ex: 'nao entendi', 'pode repetir').
    Isoladas nao denunciam — use combinado com outros sinais (ex: resposta <8s).
    """
    if not text or len(text.strip()) < 2:
        return False, ""
    t = text.strip()
    # So considera sinal fraco se a msg for curta (bots respondem curto quando nao entendem)
    if len(t) > 120:
        return False, ""
    for pat in _WEAK_GENERIC_RES:
        m = pat.search(t)
        if m:
            return True, f"frase generica curta: '{m.group(0)[:40]}'"
    return False, ""
