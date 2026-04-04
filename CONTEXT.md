# Mya Disparo Bot - Arquitetura e Contexto do Projeto

## Visão Geral
Este projeto é um bot de WhatsApp (SDR) chamado **Mya Disparo**, derivado do projeto base Mya SDR. 
Compartilha a mesma infraestrutura Redis/RabbitMQ do projeto original, com isolamento via prefixo `disparo:` nas chaves Redis e queue `mya_disparo` no RabbitMQ.

## Stack Tecnológica
- **Linguagem:** Python 3.11+
- **Framework Web (Webhook):** FastAPI / Uvicorn
- **Orquestração de Fila:** RabbitMQ (Pika) — queue: `mya_disparo`
- **Banco de Dados em Memória:** Redis (prefixo `disparo:` para isolamento)
- **Agente LLM:** Google GenAI (`google-genai` package)
- **Infra e CI/CD:** Docker Swarm (Portainer) + GitHub Actions
- **Integração Externa:** Google Sheets (via `gspread`)

## Isolamento de Dados (Redis)
Todas as chaves Redis usam o prefixo `disparo:` para não conflitar com outros projetos no mesmo servidor:
- `disparo:history:{phone}` — Histórico de conversas
- `disparo:lead:{phone}` — CRM do Lead
- `disparo:burst:{phone}` / `disparo:burst_time:{phone}` — Buffer de rajadas
- `disparo:followups` — Sorted set de follow-ups
- `disparo:followup:members:{phone}` — Membros de follow-up por lead
- `disparo:followup:active:{phone}` — Flag de follow-up ativo

## Rota Traefik
- `https://webhook-whatsapp.strategicai.com.br/mya-disparo`

## Comandos
- `/reset` — Limpa histórico e CRM do lead no Redis
