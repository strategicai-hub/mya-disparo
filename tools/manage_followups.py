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
INTERVALS_OWNER  = [60, 120, 180]              # 1min, 2min, 3min (teste)


def reset_followup_timer(phone_number: str):
    """Reseta o timer dos follow-ups — cancela os antigos e reagenda do zero."""
    if not redis_client:
        return
    if has_active_followups(phone_number):
        cancel_followups(phone_number)
        print(f"[FOLLOWUP] Timer resetado para {phone_number} (lead respondeu)")


def _build_followup_messages(phone_number: str, nome: str, nicho: str, resumo: str) -> list:
    """Gera 3 follow-ups variados e contextuais, sorteando entre variantes."""
    saudacao = f"Oi {nome}, " if nome else "Oi, "
    nome_ou_voce = nome if nome else "você"

    # --- STEP 1: Lembrete leve (1d / 1min teste) ---
    step1_variants = [
        f"{saudacao}imagino que seu dia esteja corrido. Conseguiu dar uma olhada no que te mandei?",
        f"{saudacao}sei que a rotina é puxada. Só passando pra ver se conseguiu ver aquela proposta que te enviei!",
        f"{saudacao}tudo certo? Queria saber se teve chance de ver o que te mandei sobre a automação por IA",
        f"E aí {nome_ou_voce}, correria por aí né? Quando tiver um tempinho dá uma olhada no que te mandei, acho que vai curtir!",
        f"{saudacao}passando rapidinho aqui! Vi que não tivemos chance de conversar ainda sobre o que te enviei",
    ]

    # --- STEP 2: Prova social / valor (3d / 2min teste) ---
    step2_base = [
        f"Ah, esqueci de comentar: semana passada a gente instalou a IA em um negócio parecido com o seu e o dono ficou impressionado com a velocidade das respostas",
        f"Só pra te dar um contexto: nossos clientes estão conseguindo atender leads até de madrugada e fim de semana sem precisar de mais ninguém na equipe",
        f"Sabia que a maioria dos negócios perde até 60% dos leads só por demora na resposta? A IA resolve isso de forma instantânea",
        f"Um dado que achei interessante: os clientes que testaram a IA perceberam diferença já na primeira semana. Acho que vale a pena pelo menos ver funcionando!",
    ]
    # Se tem nicho, adiciona variantes específicas
    if nicho:
        step2_base.extend([
            f"{nome_ou_voce}, a gente tem cases bem legais na área de {nicho}. Posso te mostrar em 15 minutinhos como funciona na prática!",
            f"Temos clientes de {nicho} que já não conseguem imaginar o atendimento sem a IA. Quer ver como ficaria no seu caso?",
        ])

    # --- STEP 3: Despedida respeitosa + imagem (7d / 3min teste) ---
    step3_variants = [
        f"{saudacao}entendo que talvez não seja o melhor momento. Vou deixar aqui um resultado que tivemos recentemente, caso mude de ideia é só me chamar!",
        f"{saudacao}sei que cada um tem seu tempo. Te mando aqui um case pra você guardar, e qualquer coisa no futuro estou por aqui!",
        f"Sem problemas, {nome_ou_voce}! Vou te deixar com esse resultado que tivemos e fico à disposição quando fizer sentido pra você. Sucesso!",
    ]

    return [
        {
            "phone": phone_number,
            "step": 1,
            "type": "text",
            "message": random.choice(step1_variants),
        },
        {
            "phone": phone_number,
            "step": 2,
            "type": "text",
            "message": random.choice(step2_base),
        },
        {
            "phone": phone_number,
            "step": 3,
            "type": "image",
            "image_url": FOLLOWUP_IMAGE_URL,
            "message": random.choice(step3_variants),
        },
    ]


def schedule_followups(phone_number: str, nome: str = "", nicho: str = "", resumo: str = ""):
    """Agenda 3 follow-ups variados no Redis (sorted set)."""
    if not redis_client:
        return

    # Cancela anteriores e reagenda do zero
    if has_active_followups(phone_number):
        cancel_followups(phone_number)

    intervals = INTERVALS_OWNER if phone_number == OWNER_NUMBER else INTERVALS_NORMAL
    now = time.time()

    messages = _build_followup_messages(phone_number, nome, nicho, resumo)

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
