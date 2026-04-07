import os
import time
import json
import random
import redis
from dotenv import load_dotenv

load_dotenv()

# Usa o mesmo Redis DB, isolamento via prefixo nas chaves
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
except Exception as e:
    print(f"Erro ao conectar ao Redis para Follow-ups: {e}")
    redis_client = None

OWNER_NUMBER = "5511989887525"
FOLLOWUP_IMAGE_URL = "https://webhook-whatsapp.strategicai.com.br/mya-disparo/resultado"

# Prefixo para isolar dados deste projeto no Redis compartilhado
KEY_PREFIX = "disparo"

# Intervalos em segundos
INTERVALS_NORMAL = [86400, 259200, 604800]    # 1d, 3d, 7d
INTERVALS_OWNER  = [86400, 259200, 604800]    # 1d, 3d, 7d

#[60, 120, 180]              # 1min, 2min, 3min (teste)

def reset_followup_timer(phone_number: str):
    """Reseta o timer dos follow-ups — cancela os antigos e zera o ciclo (lead respondeu)."""
    if not redis_client:
        return
    if has_active_followups(phone_number):
        cancel_followups(phone_number)
        print(f"[FOLLOWUP] Timer resetado para {phone_number} (lead respondeu)")


def _get_followup_cycle(phone_number: str) -> int:
    """Retorna o ciclo atual de follow-up do lead (0 = primeiro ciclo)."""
    if not redis_client:
        return 0
    cycle = redis_client.get(f"{KEY_PREFIX}:followup:cycle:{phone_number}")
    return int(cycle) if cycle else 0


def _advance_followup_cycle(phone_number: str) -> int:
    """Avança para o próximo ciclo e retorna o novo valor."""
    if not redis_client:
        return 0
    return redis_client.incr(f"{KEY_PREFIX}:followup:cycle:{phone_number}") - 1


def _build_followup_messages(phone_number: str, nome: str, nicho: str, resumo: str, cycle: int = 0) -> list:
    """Gera 3 follow-ups variados e contextuais, usando ciclos diferentes a cada rodada."""
    saudacao = f"Oi {nome}, " if nome else "Oi, "

    # Ciclos de mensagens — cada ciclo é uma experiência diferente
    if cycle == 0:
        # --- CICLO 1: Abordagem inicial ---
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
        # Ciclo 1: envia imagem de resultado no step 3
        return [
            {"phone": phone_number, "step": 1, "type": "text", "message": random.choice(step1_variants)},
            {"phone": phone_number, "step": 2, "type": "text", "message": random.choice(step2_variants)},
            {"phone": phone_number, "step": 3, "type": "image", "image_url": FOLLOWUP_IMAGE_URL, "message": random.choice(step3_variants)},
        ]

    elif cycle == 1:
        # --- CICLO 2: Curiosidade + urgência leve ---
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
        # Ciclo 2+: NÃO envia imagem (só texto)
        return [
            {"phone": phone_number, "step": 1, "type": "text", "message": random.choice(step1_variants)},
            {"phone": phone_number, "step": 2, "type": "text", "message": random.choice(step2_variants)},
            {"phone": phone_number, "step": 3, "type": "text", "message": random.choice(step3_variants)},
        ]

    else:
        # --- CICLO 3+: Despedida final (último ciclo, sem repetição) ---
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


def schedule_followups(phone_number: str, nome: str = "", nicho: str = "", resumo: str = ""):
    """Agenda 3 follow-ups variados no Redis (sorted set), avançando o ciclo a cada chamada."""
    if not redis_client:
        return

    # Cancela anteriores e reagenda do zero
    if has_active_followups(phone_number):
        cancel_followups(phone_number)

    # Avança o ciclo (0 → 1 → 2). Ciclo 2+ = último ciclo, não repete mais
    cycle = _advance_followup_cycle(phone_number)

    # Ciclo 3+ = já enviou despedida final, não agenda mais
    if cycle > 2:
        print(f"[FOLLOWUP] Lead {phone_number} já passou por todos os ciclos. Não reagendando.")
        return

    intervals = INTERVALS_OWNER if phone_number == OWNER_NUMBER else INTERVALS_NORMAL
    now = time.time()

    messages = _build_followup_messages(phone_number, nome, nicho, resumo, cycle=cycle)

    followups_key = f"{KEY_PREFIX}:followups"
    for i, msg in enumerate(messages):
        timestamp = now + intervals[i]
        raw = json.dumps(msg, ensure_ascii=False)
        redis_client.zadd(followups_key, {raw: timestamp})
        redis_client.sadd(f"{KEY_PREFIX}:followup:members:{phone_number}", raw)

    redis_client.set(f"{KEY_PREFIX}:followup:active:{phone_number}", "1")
    print(f"[FOLLOWUP] 3 follow-ups agendados para {phone_number}")


def cancel_followups(phone_number: str):
    """Remove todos os follow-ups pendentes de um lead."""
    if not redis_client:
        return

    members_key = f"{KEY_PREFIX}:followup:members:{phone_number}"
    members = redis_client.smembers(members_key)

    followups_key = f"{KEY_PREFIX}:followups"
    if members:
        for raw in members:
            redis_client.zrem(followups_key, raw)
        redis_client.delete(members_key)

    redis_client.delete(f"{KEY_PREFIX}:followup:active:{phone_number}")
    print(f"[FOLLOWUP] Follow-ups cancelados para {phone_number}")


def has_active_followups(phone_number: str) -> bool:
    """Verifica se o lead tem follow-ups pendentes (O(1))."""
    if not redis_client:
        return False
    return redis_client.exists(f"{KEY_PREFIX}:followup:active:{phone_number}") == 1


def get_due_followups(now_timestamp: float) -> list:
    """Retorna follow-ups com timestamp <= now (prontos para envio)."""
    if not redis_client:
        return []

    followups_key = f"{KEY_PREFIX}:followups"
    raw_items = redis_client.zrangebyscore(followups_key, 0, now_timestamp)
    results = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
            item["_raw"] = raw  # Guarda o JSON original para remoção
            results.append(item)
        except json.JSONDecodeError:
            redis_client.zrem(followups_key, raw)
    return results


def remove_followup(raw_json: str, phone_number: str):
    """Remove um follow-up específico após envio."""
    if not redis_client:
        return
    followups_key = f"{KEY_PREFIX}:followups"
    redis_client.zrem(followups_key, raw_json)
    redis_client.srem(f"{KEY_PREFIX}:followup:members:{phone_number}", raw_json)

    # Se não restam mais membros, limpa o flag active
    remaining = redis_client.scard(f"{KEY_PREFIX}:followup:members:{phone_number}")
    if remaining == 0:
        redis_client.delete(f"{KEY_PREFIX}:followup:active:{phone_number}")


def reschedule_followup(raw_json: str, new_timestamp: float):
    """Reagenda um follow-up para novo horário (ex: horário comercial)."""
    if not redis_client:
        return
    followups_key = f"{KEY_PREFIX}:followups"
    redis_client.zadd(followups_key, {raw_json: new_timestamp})
