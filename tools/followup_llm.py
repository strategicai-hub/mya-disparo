"""Gera mensagem de follow-up contextual usando Gemini, com base no histórico
real da conversa com o lead.

Usado pelo scheduler.py quando o item de follow-up tem `use_llm: True`.

Saída: string com a mensagem pronta para enviar. Em caso de erro, retorna o
fallback fornecido pelo chamador.
"""
import os
from google import genai
from google.genai import types as gtypes

from tools.manage_history import get_history

_API_KEY = os.getenv("LLM_API_KEY")
_client = genai.Client(api_key=_API_KEY) if _API_KEY else None

_SYSTEM_PROMPT = """Você é a Mya, SDR digital da Strategic AI. Está escrevendo um follow-up curto e contextual no WhatsApp para um lead que já recebeu um disparo comercial e ficou pelo menos 1 hora sem responder (ou respondeu apenas auto-resposta).

Sua missão: escrever UMA mensagem curta, leve e personalizada, retomando a conversa de onde parou e gerando reengajamento. Não é um pitch — é um "oi, tudo bem? só queria te chamar de volta".

REGRAS RÍGIDAS:
1. Tom: humano, leve, brasileiro. Como uma vendedora real escreveria no WhatsApp. Nada de formalidade corporativa.
2. Tamanho: 1 a 3 frases curtas. NUNCA passa de 280 caracteres.
3. Use o nome do lead se você souber (informado no contexto). Não invente nome.
4. Faça referência sutil ao que já foi dito na conversa (ou ao nicho dele, se conhecido). Se a conversa só tem o disparo inicial e nenhuma resposta real, faça referência ao tema do disparo sem repetir.
5. Termine com uma pergunta curta ou abertura para resposta — NUNCA imperativo de venda ("agende já", "fale comigo agora").
6. NUNCA escreva markdown, bullets, **negrito**, listas. Texto corrido.
7. NUNCA escreva saudações com vírgula dupla ("Oi João, tudo bem,") — escolha uma e siga.
8. NUNCA repita literalmente o que já foi dito no histórico.
9. NÃO ofereça desconto, prazo, garantia ou qualquer condição comercial inventada.
10. NÃO escreva nada de mensagem de encerramento — esse follow-up é o de reengajamento (+1h). Outro follow-up de encerramento sai depois automaticamente.

Responda APENAS com o texto da mensagem, sem aspas, sem prefixos, sem explicações."""


def _build_user_prompt(nome: str, nicho: str, resumo: str, history_text: str) -> str:
    parts = ["Contexto do lead:"]
    if nome:
        parts.append(f"- Nome: {nome}")
    else:
        parts.append("- Nome: (desconhecido — não use nome inventado)")
    if nicho:
        parts.append(f"- Nicho/área: {nicho}")
    if resumo:
        parts.append(f"- Resumo da conversa até agora: {resumo}")
    parts.append("")
    parts.append("Histórico das últimas mensagens (humano=lead, ai=mya):")
    parts.append(history_text or "(apenas o disparo inicial, sem resposta do lead)")
    parts.append("")
    parts.append("Escreva agora o follow-up de +1h para retomar essa conversa.")
    return "\n".join(parts)


def _format_history(phone_number: str, instance_id, max_msgs: int = 10) -> str:
    try:
        msgs = get_history(phone_number, instance_id) or []
    except Exception:
        return ""
    if not msgs:
        return ""
    tail = msgs[-max_msgs:]
    lines = []
    for m in tail:
        role = m.get("type", "?")
        content = (m.get("data") or {}).get("content", "") or ""
        content = content.strip()
        if not content:
            continue
        if len(content) > 400:
            content = content[:400] + "…"
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def generate_followup_text(
    phone_number: str,
    instance_id,
    nome: str = "",
    nicho: str = "",
    resumo: str = "",
    fallback: str = "",
) -> str:
    """Gera o texto do follow-up +1h. Retorna fallback em caso de falha."""
    if not _client:
        return fallback or "Oi, conseguiu ver a mensagem que te mandei?"

    history_text = _format_history(phone_number, instance_id)
    user_prompt = _build_user_prompt(nome, nicho, resumo, history_text)

    try:
        response = _client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=user_prompt,
            config=gtypes.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=200,
                thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
            ),
        )
        text = (response.text or "").strip()
        # Remove aspas extras se vierem
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        if not text:
            return fallback or "Oi, conseguiu ver a mensagem que te mandei?"
        if len(text) > 500:
            text = text[:497] + "..."
        return text
    except Exception as e:
        print(f"[FOLLOWUP_LLM] Falha ao gerar texto contextual para {phone_number} [inst {instance_id}]: {e}")
        return fallback or "Oi, conseguiu ver a mensagem que te mandei?"
