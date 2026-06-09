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
    # Padroes capturados de SDR/atendente de outra empresa (ex: caso Dani/Arena Fitness)
    # Auto-apresentacao corporativa com cargo: "Sou a Dani, do time comercial da Arena Fitness"
    r"\bsou (o|a) \w+,?\s+(do|da)\s+(time|setor|equipe|departamento|atendimento|area)\s+(comercial|de vendas|de atendimento|de relacionamento|administrativ[oa])\b",
    # Frase institucional: "nosso foco aqui é garantir / atender / oferecer"
    r"\bnosso foco (aqui )?(é|e) (garantir|atender|oferecer|prestar|proporcionar)\b",
    # "este canal é exclusivo para vendas e informações"
    r"\b(este|esse) canal (é|e) (exclusivo|destinado|reservado|dedicado) (para|a)\b",
    # Linguagem corporativa formal: "sigo processos internos"
    r"\bsigo processos internos\b",
    # Auto-referência formal: "como informei", "como já mencionei anteriormente"
    r"\bcomo (já )?(informei|mencionei|disse|expliquei|comuniquei)( anteriormente| antes)?\b",
    # Pergunta scripted: "gostaria que eu te enviasse/mandasse os valores/o material"
    r"\bgostaria que eu (te |lhe )?(envias?se|mandass?e|apresentas?se|passas?se|disponibilizas?se)\b",
    # Estrutura condicional formal: "caso você tenha interesse em ..., posso te apresentar"
    r"\bcaso (você|vc|o senhor|a senhora) (tenha|tiver) interesse em\b.{0,80}\bposso (te |lhe )?(apresentar|enviar|mostrar|passar)\b",
    # Pergunta de qualificacao tipica de SDR-bot: "qual o seu objetivo com os treinos / com a contratacao"
    r"\bqual (o seu|seu|o) objetivo (com|na|nos|no|na sua)\b",
    # "agradeço o contato, mas no momento meu foco é..."
    r"\bagradeço (o|seu) contato,?\s*mas\s+no momento\b",
    # Pergunta dupla de qualificacao em uma unica msg ("Você já é X ou gostaria de Y")
    r"\bvocê já (é|tem|conhece|utiliza)\b.{0,80}\bou gostaria (de|que)\b",
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


# Marcas de escrita casual humana — se presentes, NAO e um bot "polido demais".
# Todos ancorados em \b para nao casar dentro de palavras comuns (ex.: "interesse",
# "conversa") — falso positivo aqui derruba a deteccao.
_CASUAL_MARKERS_RE = re.compile(
    r"(\bvc\b|\bvcs\b|\btb\b|\btbm\b|\bpra\b|\bpq\b|\bpqp\b|\bblz\b|\bvlw\b|\bobg\b|\bné\b|\bne\b|\baki\b|\bto\b|"
    r"\bk{2,}\b|\bkk+|\brs+\b|\bha(?:ha)+\b|\bhue+\b|\.{3,}|!{2,}|\?{2,})",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤✨]"
)


def detect_polished_human(text: str) -> tuple[bool, str]:
    """
    Caso 1 (chatbot de IA imitando humano): texto que LE como pessoa, mas e
    'polido demais' — inicial maiuscula + virgula + pontuacao final, e SEM
    nenhuma marca casual (gírias, repeticao de letra, emoji, reticencias).

    So denuncia quando COMBINADO com resposta rapida (<60s) no worker. Sozinho
    nao basta — varios humanos escrevem certinho. Por isso o worker exige
    AI_SIGNAL_BLOCK_THRESHOLD mensagens assim antes de bloquear.
    """
    if not text:
        return False, ""
    t = text.strip()
    # Muito curta nao da sinal confiavel ("Ok.", "Sim, claro.")
    if len(t) < 15:
        return False, ""
    # Marca casual ou emoji => humano, nao bot polido
    if _CASUAL_MARKERS_RE.search(t) or _EMOJI_RE.search(t):
        return False, ""
    # Inicial maiuscula
    first = t[0]
    if not (first.isalpha() and first.isupper()):
        return False, ""
    has_comma = "," in t
    has_end_punct = bool(re.search(r"[.!?]", t))
    if has_comma and has_end_punct:
        return True, "texto polido (maiuscula + virgula + pontuacao) sem marcas casuais"
    return False, ""
