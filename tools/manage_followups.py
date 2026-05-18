import os
import time
import json
import random
import redis
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from config.instances import redis_prefix, OWNER_NUMBER, INSTANCES

load_dotenv()

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
except Exception as e:
    print(f"Erro ao conectar ao Redis para Follow-ups: {e}")
    redis_client = None

FOLLOWUP_IMAGE_URL = "https://webhook-whatsapp.strategicai.com.br/mya-disparo/resultado"

# Intervalos em segundos
INTERVALS_NORMAL = [86400, 259200, 604800]    # 1d, 3d, 7d (UAZAPI legacy)
INTERVALS_OWNER  = [86400, 259200, 604800]    # 1d, 3d, 7d (UAZAPI legacy)

# Meta API Oficial: 1h (follow-up contextual via LLM) e 4h (mensagem de encerramento)
META_FOLLOWUP_DELAYS = [3600, 14400]

CLOSURE_MESSAGE_VARIANTS = [
    "Sem problemas! Entendo que esse não é o melhor momento. Vou parar por aqui pra não te incomodar. Quando fizer sentido, é só me chamar — fico à disposição!",
    "Tudo bem! Imagino que o timing agora não esteja ideal. Vou encerrar por aqui pra não te atrapalhar. Se um dia quiser conversar sobre IA no seu negócio, é só mandar oi.",
    "Sem stress! Vejo que talvez agora não seja a hora certa. Vou parar de te chamar pra não incomodar. Qualquer coisa no futuro, meu contato fica salvo aqui. Sucesso!",
    "Tranquilo! Percebo que esse momento não é o melhor pra você. Vou recolher por aqui pra não atrapalhar sua rotina. Quando fizer sentido, me chama!",
]

SAO_PAULO_TZ = timezone(timedelta(hours=-3))


def _skip_weekend(ts: float) -> float:
    """Se o timestamp cair em sábado (+2d) ou domingo (+1d), avança para segunda-feira."""
    dt = datetime.fromtimestamp(ts, tz=SAO_PAULO_TZ)
    wd = dt.weekday()  # 5=sábado, 6=domingo
    if wd == 5:
        dt = dt + timedelta(days=2)
    elif wd == 6:
        dt = dt + timedelta(days=1)
    return dt.timestamp()


def _next_morning_timestamp() -> float:
    """Retorna um timestamp aleatório dentro do dia útil seguinte (8h-18h, horário de SP).

    Distribui ao longo de todo o expediente para evitar rajada de follow-ups
    concentrada em 8h-9h (risco de bloqueio pela Meta em chip de disparo).
    """
    now = datetime.now(SAO_PAULO_TZ)
    amanha = (now + timedelta(days=1)).replace(
        hour=random.randint(8, 17),
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        microsecond=0,
    )
    return _skip_weekend(amanha.timestamp())


def mark_closure_sent(phone_number: str, instance_id) -> None:
    """Marca que o follow-up de encerramento (4h) já foi enviado.

    Depois disso, nenhum follow-up é reagendado para esse lead até
    haver um novo disparo (que zera o estado via reset_followup_cycle ou
    o operador resetar manualmente).
    """
    if not redis_client:
        return
    redis_client.set(f"{redis_prefix(instance_id)}:followup:closure_sent:{phone_number}", "1")


def is_closure_sent(phone_number: str, instance_id) -> bool:
    if not redis_client:
        return False
    return redis_client.exists(f"{redis_prefix(instance_id)}:followup:closure_sent:{phone_number}") == 1


def clear_closure_flag(phone_number: str, instance_id) -> None:
    if not redis_client:
        return
    redis_client.delete(f"{redis_prefix(instance_id)}:followup:closure_sent:{phone_number}")


def reset_followup_timer(phone_number: str, instance_id):
    """Reseta o timer dos follow-ups — cancela os antigos e zera o ciclo (lead respondeu)."""
    if not redis_client:
        return
    if has_active_followups(phone_number, instance_id):
        cancel_followups(phone_number, instance_id)
        print(f"[FOLLOWUP] Timer resetado para {phone_number} (lead respondeu) [inst {instance_id}]")


def reset_followup_cycle(phone_number: str, instance_id):
    """Zera o ciclo de follow-up do lead (usado no /reset)."""
    if not redis_client:
        return
    cancel_followups(phone_number, instance_id)
    redis_client.delete(f"{redis_prefix(instance_id)}:followup:cycle:{phone_number}")
    clear_closure_flag(phone_number, instance_id)
    print(f"[FOLLOWUP] Ciclo zerado para {phone_number} [inst {instance_id}]")


def _get_followup_cycle(phone_number: str, instance_id) -> int:
    if not redis_client:
        return 0
    cycle = redis_client.get(f"{redis_prefix(instance_id)}:followup:cycle:{phone_number}")
    return int(cycle) if cycle else 0


def _advance_followup_cycle(phone_number: str, instance_id) -> int:
    if not redis_client:
        return 0
    return redis_client.incr(f"{redis_prefix(instance_id)}:followup:cycle:{phone_number}") - 1


def _build_followup_messages(phone_number: str, nome: str, nicho: str, resumo: str, cycle: int = 0) -> list:
    """Gera 3 follow-ups variados e contextuais, usando ciclos diferentes a cada rodada."""
    saudacao = f"Oi {nome}, " if nome else "Oi, "

    if cycle == 0:
        step1_variants = [
            f"{saudacao}imagino que seu dia esteja corrido. Conseguiu dar uma olhada no que te mandei?",
            f"{saudacao}sei que a rotina é puxada. Só passando pra ver se conseguiu ver aquela proposta que te enviei!",
            f"{saudacao}tudo certo? Queria saber se teve chance de ver o que te mandei sobre a automação por IA",
        ]
        step2_variants = [
            "Ah, esqueci de comentar: semana passada a gente instalou a IA em um negócio parecido com o seu e o dono ficou impressionado com a velocidade das respostas",
            "Só pra te dar um contexto: nossos clientes estão conseguindo atender leads até de madrugada e fim de semana sem precisar de mais ninguém na equipe",
            "Sabia que a maioria dos negócios perde até 60% dos leads só por demora na resposta? A IA resolve isso de forma instantânea",
        ]
        if nicho:
            step2_variants.append(
                f"A gente tem cases bem legais na área de {nicho}. Posso te mostrar em 15 minutinhos como funciona na prática!"
            )
        step3_variants = [
            f"{saudacao}entendo que talvez não seja o melhor momento. Vou deixar aqui um resultado que tivemos recentemente, caso mude de ideia é só me chamar!",
            f"{saudacao}sei que cada um tem seu tempo. Te mando aqui um case pra guardar, e qualquer coisa no futuro estou por aqui!",
            f"Sem problemas! Vou te deixar com esse resultado que tivemos e fico à disposição quando fizer sentido pra você. Sucesso!",
        ]
        return [
            {"phone": phone_number, "step": 1, "type": "text", "message": random.choice(step1_variants)},
            {"phone": phone_number, "step": 2, "type": "text", "message": random.choice(step2_variants)},
            {"phone": phone_number, "step": 3, "type": "image", "image_url": FOLLOWUP_IMAGE_URL, "message": random.choice(step3_variants)},
        ]

    elif cycle == 1:
        step1_variants = [
            f"{saudacao}vi que ainda não tivemos chance de conversar. Tem alguma dúvida sobre como a IA funcionaria no seu negócio?",
            f"{saudacao}passando pra ver se surgiu alguma dúvida. Fico à disposição pra explicar qualquer coisa!",
        ]
        step2_variants = [
            "Uma coisa que os donos de negócio mais gostam: a IA não esquece, não atrasa e não pede folga. Funciona 24h certinho",
            "Só pra compartilhar: essa semana um cliente nosso disse que a IA já pagou o investimento só com os leads que atendia de madrugada",
        ]
        if nicho:
            step2_variants.append(
                f"Temos clientes de {nicho} que já não conseguem imaginar o atendimento sem a IA. Quer ver como ficaria no seu caso?"
            )
        step3_variants = [
            f"{saudacao}entendo que o momento pode não ser ideal. Quando fizer sentido, é só chamar que a gente retoma de onde parou!",
            f"Sem problemas! Quando sentir que é hora, estou por aqui. Sucesso no seu negócio!",
        ]
        return [
            {"phone": phone_number, "step": 1, "type": "text", "message": random.choice(step1_variants)},
            {"phone": phone_number, "step": 2, "type": "text", "message": random.choice(step2_variants)},
            {"phone": phone_number, "step": 3, "type": "text", "message": random.choice(step3_variants)},
        ]

    else:
        step1_variants = [
            f"{saudacao}não quero ser inconveniente! Só queria garantir que você sabe que estou à disposição caso precise",
            f"{saudacao}última passada por aqui! Se um dia quiser conhecer a IA, é só mandar um oi",
        ]
        step2_variants = [
            "A gente acredita que o timing é tudo. Quando for o momento certo pra você, estou aqui",
            "Vou parar de mandar mensagem pra não atrapalhar. Mas se precisar, é só chamar!",
        ]
        step3_variants = [
            "Te desejo muito sucesso! Qualquer coisa no futuro, meu contato fica salvo aqui. Até mais!",
            "Sucesso no seu negócio! Fico por aqui caso precise. Um abraço!",
        ]
        return [
            {"phone": phone_number, "step": 1, "type": "text", "message": random.choice(step1_variants)},
            {"phone": phone_number, "step": 2, "type": "text", "message": random.choice(step2_variants)},
            {"phone": phone_number, "step": 3, "type": "text", "message": random.choice(step3_variants)},
        ]


def schedule_followups(phone_number: str, instance_id, nome: str = "", nicho: str = "", resumo: str = ""):
    """Agenda 3 follow-ups variados no Redis (sorted set), avançando o ciclo a cada chamada.

    Bloqueia silenciosamente se o lead tem reunião agendada (event_id preenchido) —
    após agendamento, follow-up é PROIBIDO para aquele número.
    """
    if not redis_client:
        return

    from tools.manage_leads import get_lead_info
    if get_lead_info(phone_number, instance_id).get("event_id"):
        print(f"[FOLLOWUP] Bloqueado: {phone_number} tem reunião agendada [inst {instance_id}]")
        return

    if has_active_followups(phone_number, instance_id):
        cancel_followups(phone_number, instance_id)

    cycle = _advance_followup_cycle(phone_number, instance_id)

    if cycle > 2:
        print(f"[FOLLOWUP] Lead {phone_number} já passou por todos os ciclos. Não reagendando.")
        return

    intervals = INTERVALS_OWNER if phone_number == OWNER_NUMBER else INTERVALS_NORMAL

    t1 = _next_morning_timestamp()
    t2 = _skip_weekend(t1 + intervals[1] - intervals[0])
    t3 = _skip_weekend(t1 + intervals[2] - intervals[0])
    timestamps = [t1, t2, t3]

    messages = _build_followup_messages(phone_number, nome, nicho, resumo, cycle=cycle)

    prefix = redis_prefix(instance_id)
    followups_key = f"{prefix}:followups"
    for i, msg in enumerate(messages):
        # Embute instance_id no item para o scheduler escolher o token correto
        msg["instance_id"] = str(instance_id)
        timestamp = timestamps[i]
        raw = json.dumps(msg, ensure_ascii=False)
        redis_client.zadd(followups_key, {raw: timestamp})
        redis_client.sadd(f"{prefix}:followup:members:{phone_number}", raw)

    redis_client.set(f"{prefix}:followup:active:{phone_number}", "1")
    print(f"[FOLLOWUP] 3 follow-ups agendados para {phone_number} [inst {instance_id}]")


def _build_meta_followup_steps(phone_number: str, nome: str, nicho: str, resumo: str) -> list:
    """Monta os 2 steps da sequência Meta API Oficial:
    - step 1 (+1h): mensagem contextual gerada por LLM no momento do envio
      (scheduler.py chama tools.followup_llm para gerar o texto).
    - step 2 (+4h): mensagem fixa de encerramento.
    """
    return [
        {
            "phone": phone_number,
            "step": 1,
            "type": "text",
            "use_llm": True,
            "context": {
                "nome": nome or "",
                "nicho": nicho or "",
                "resumo": resumo or "",
            },
            "fallback": (
                f"Oi {nome}, " if nome else "Oi, "
            ) + "imagino que seu dia esteja corrido. Conseguiu dar uma olhada na mensagem que te mandei?",
        },
        {
            "phone": phone_number,
            "step": 2,
            "type": "text",
            "closure": True,
            "message": random.choice(CLOSURE_MESSAGE_VARIANTS),
        },
    ]


def schedule_meta_outbound_followups(phone_number: str, instance_id, nome: str = "", nicho: str = "", resumo: str = ""):
    """Agenda 2 follow-ups após disparo outbound via API Oficial Meta:
    - Step 1 (+1h): mensagem contextual via LLM com o histórico da conversa.
    - Step 2 (+4h): mensagem de encerramento ("não é o momento, vou parar").

    Regras:
    - Bloqueia se lead tem reunião agendada (event_id) ou se closure já foi enviado.
    - Não sobrescreve follow-ups já ativos (no-op idempotente).
    """
    if not redis_client:
        return

    from tools.manage_leads import get_lead_info
    if get_lead_info(phone_number, instance_id).get("event_id"):
        print(f"[FOLLOWUP] Bloqueado: {phone_number} tem reunião agendada [inst {instance_id}]")
        return

    if is_closure_sent(phone_number, instance_id):
        print(f"[FOLLOWUP] Bloqueado: closure já enviado para {phone_number} [inst {instance_id}]")
        return

    if has_active_followups(phone_number, instance_id):
        print(f"[FOLLOWUP] {phone_number} já tem follow-ups ativos — não sobrescrevendo [inst {instance_id}]")
        return

    steps = _build_meta_followup_steps(phone_number, nome, nicho, resumo)

    now = time.time()
    timestamps = [now + d for d in META_FOLLOWUP_DELAYS]

    prefix = redis_prefix(instance_id)
    followups_key = f"{prefix}:followups"
    for i, msg in enumerate(steps):
        msg["instance_id"] = str(instance_id)
        raw = json.dumps(msg, ensure_ascii=False)
        redis_client.zadd(followups_key, {raw: timestamps[i]})
        redis_client.sadd(f"{prefix}:followup:members:{phone_number}", raw)

    redis_client.set(f"{prefix}:followup:active:{phone_number}", "1")

    sp = SAO_PAULO_TZ
    dt1 = datetime.fromtimestamp(timestamps[0], tz=sp).strftime("%d/%m %H:%M")
    dt2 = datetime.fromtimestamp(timestamps[1], tz=sp).strftime("%d/%m %H:%M")
    print(f"[FOLLOWUP] 2 follow-ups (meta-outbound 1h/4h) agendados para {phone_number}: [{dt1}] e [{dt2}] [inst {instance_id}]")


def schedule_meta_reply_followups(phone_number: str, instance_id, nome: str = "", nicho: str = "", resumo: str = ""):
    """Reagenda 2 follow-ups após cada resposta do lead na API Oficial Meta:
    - Step 1 (+1h a partir de agora): contextual via LLM.
    - Step 2 (+4h a partir de agora): mensagem de encerramento.

    Cancela follow-ups anteriores e reseta o timer.
    Bloqueia se lead tem reunião agendada ou se closure já foi enviado.
    Sem cycle cap: enquanto o lead engaja, o follow-up segue ativo.
    """
    if not redis_client:
        return

    from tools.manage_leads import get_lead_info
    if get_lead_info(phone_number, instance_id).get("event_id"):
        print(f"[FOLLOWUP] Bloqueado: {phone_number} tem reunião agendada [inst {instance_id}]")
        return

    if is_closure_sent(phone_number, instance_id):
        print(f"[FOLLOWUP] Bloqueado: closure já enviado para {phone_number} [inst {instance_id}]")
        return

    cancel_followups(phone_number, instance_id)

    steps = _build_meta_followup_steps(phone_number, nome, nicho, resumo)

    now = time.time()
    timestamps = [now + d for d in META_FOLLOWUP_DELAYS]

    prefix = redis_prefix(instance_id)
    followups_key = f"{prefix}:followups"
    for i, msg in enumerate(steps):
        msg["instance_id"] = str(instance_id)
        raw = json.dumps(msg, ensure_ascii=False)
        redis_client.zadd(followups_key, {raw: timestamps[i]})
        redis_client.sadd(f"{prefix}:followup:members:{phone_number}", raw)

    redis_client.set(f"{prefix}:followup:active:{phone_number}", "1")

    sp = SAO_PAULO_TZ
    dt1 = datetime.fromtimestamp(timestamps[0], tz=sp).strftime("%d/%m %H:%M")
    dt2 = datetime.fromtimestamp(timestamps[1], tz=sp).strftime("%d/%m %H:%M")
    print(f"[FOLLOWUP] 2 follow-ups (meta-reply 1h/4h) reagendados para {phone_number}: [{dt1}] e [{dt2}] [inst {instance_id}]")


def permanently_block_followups(phone_number: str, instance_id):
    """Bloqueia permanentemente follow-ups para um número (lead pediu humano na API Oficial).

    Cancela os follow-ups ativos e seta ciclo=99, impedindo qualquer reagendamento futuro.
    """
    if not redis_client:
        return
    cancel_followups(phone_number, instance_id)
    redis_client.set(f"{redis_prefix(instance_id)}:followup:cycle:{phone_number}", 99)
    print(f"[FOLLOWUP] Bloqueio permanente aplicado para {phone_number} [inst {instance_id}]")


def cancel_followups(phone_number: str, instance_id):
    """Remove todos os follow-ups pendentes de um lead."""
    if not redis_client:
        return

    prefix = redis_prefix(instance_id)
    members_key = f"{prefix}:followup:members:{phone_number}"
    members = redis_client.smembers(members_key)

    followups_key = f"{prefix}:followups"
    if members:
        for raw in members:
            redis_client.zrem(followups_key, raw)
        redis_client.delete(members_key)

    redis_client.delete(f"{prefix}:followup:active:{phone_number}")
    print(f"[FOLLOWUP] Follow-ups cancelados para {phone_number} [inst {instance_id}]")


def has_active_followups(phone_number: str, instance_id) -> bool:
    """Verifica se o lead tem follow-ups pendentes (O(1))."""
    if not redis_client:
        return False
    return redis_client.exists(f"{redis_prefix(instance_id)}:followup:active:{phone_number}") == 1


def get_due_followups(now_timestamp: float, instance_id) -> list:
    """Retorna follow-ups com timestamp <= now (prontos para envio) de uma instância."""
    if not redis_client:
        return []

    followups_key = f"{redis_prefix(instance_id)}:followups"
    raw_items = redis_client.zrangebyscore(followups_key, 0, now_timestamp)
    results = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
            item["_raw"] = raw
            # Defensivo: garante que o item saiba sua instância mesmo se foi migrado sem o campo
            item.setdefault("instance_id", str(instance_id))
            results.append(item)
        except json.JSONDecodeError:
            redis_client.zrem(followups_key, raw)
    return results


def get_all_instance_ids() -> list:
    """Retorna lista de instance_ids configurados (para o scheduler iterar)."""
    return list(INSTANCES.keys())


def remove_followup(raw_json: str, phone_number: str, instance_id):
    """Remove um follow-up específico após envio."""
    if not redis_client:
        return
    prefix = redis_prefix(instance_id)
    followups_key = f"{prefix}:followups"
    redis_client.zrem(followups_key, raw_json)
    redis_client.srem(f"{prefix}:followup:members:{phone_number}", raw_json)

    remaining = redis_client.scard(f"{prefix}:followup:members:{phone_number}")
    if remaining == 0:
        redis_client.delete(f"{prefix}:followup:active:{phone_number}")


def reschedule_followup(raw_json: str, new_timestamp: float, instance_id):
    """Reagenda um follow-up para novo horário (ex: horário comercial)."""
    if not redis_client:
        return
    followups_key = f"{redis_prefix(instance_id)}:followups"
    redis_client.zadd(followups_key, {raw_json: new_timestamp})
