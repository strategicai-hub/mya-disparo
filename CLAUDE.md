# Mya Disparo Bot — Instruções do projeto

Guia de como trabalhar neste projeto Mya Disparo Bot (SDR automatizado via WhatsApp).

---

## Regra obrigatória: commit, push e deploy

**Antes de qualquer operação de commit, push ou redeploy, SEMPRE perguntar:**

> "Quer que eu faça commit, push e redeploy agora?"

Aguardar confirmação explícita antes de executar. Nunca fazer essas operações de forma automática ou sem aprovação, mesmo que o código esteja pronto.

Isso inclui:
- `git commit`
- `git push`
- Redeploy via Portainer (force-update do serviço Swarm)
- Build de imagem Docker com `nocache=true`

## Deploy

O processo de redeploy deste projeto é sempre:
1. Criar o tarball: `tar -czf /tmp/build-context.tar.gz --exclude='.git' --exclude='node_modules' --exclude='.env' .`
2. Build via Portainer API (`https://91.98.64.92:9443`, tag `ghcr.io/gustavocastilho-hub/mya-disparo:latest`)
3. Force-update do serviço Swarm (`mya-disparo_api`, `mya-disparo_worker`, `mya-disparo_scheduler`) com o spec completo incrementando `ForceUpdate`
4. Verificar HTTP 200 em `https://webhook-whatsapp.strategicai.com.br/mya-disparo/`
5. Verificar se os containers estão rodando via `docker service ps <SERVICE_ID>` ou Portainer API
   - Se algum container estiver com estado diferente de `running`, ler os logs (`docker service logs <SERVICE_ID> --tail 50`) e corrigir o erro antes de encerrar.

Credenciais necessárias estão em `.env` na raiz do projeto (nunca commitado): `PORTAINER_URL`, `PORTAINER_TOKEN`, `RABBITMQ_*`, `REDIS_URL`, `UAZAPI_*`, `LLM_API_KEY`, `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_CALENDAR_ID`, `GOOGLE_IMPERSONATE_USER`.

## Tom e idioma

- Responder sempre em português brasileiro.
- Respostas curtas e diretas, sem filler ou explicações desnecessárias.
- Não usar emojis a menos que solicitado.
- Leve com a informação, não restate o que foi pedido.
- Máximo 2-3 frases por parágrafo.

## Comportamentos Esperados

### Antes de Ações Destrutivas
Sempre pergunte antes de:
- `git push --force` ou rebase em branch compartilhada
- Deletar branches, arquivos ou dados
- Limpar Redis ou banco de dados
- Modificar docker-compose.yml em produção

### Commits
- **Formato:** `tipo(escopo): mensagem curta`
- **Tipos:** feat, fix, refactor, docs, debug
- **Exemplos válidos:**
  - `feat(calendar): sempre ofereça 3 próximos horários`
  - `fix(scheduler): salva follow-ups no histórico`
  - `docs(workflow): atualiza instrução de resumo acumulativo`
- sempre faça `push` junto com `commit`

### Mudanças de Código
- Use **Edit** para modificar arquivos existentes
- Use **Write** para criar novos arquivos
- Não adicione comentários/docstrings em código que não alterou
- Leia o arquivo primeiro antes de editar

## Regras de Negócio (Mya SDR)

### Google Calendar
- Service account usa `sendUpdates="none"` (sem attendees)
- Telefone é formatado com "55" prefixo
- Event ID deve ser salvo em Redis após `criar_evento` suceder
- Título padrão: `Reunião SAI - {nome}`
- Descrição: `Whatsapp: {telefone}\nNicho: {nicho}\nEmpresa: {wa_name}\nEmail: {email}`

### Workflow SDR
- **NUNCA prometa o que não fez:** avisei equipe → só após `<ATENDIMENTO_HUMANO>`, agendei → só após event_id válido
- **Resumo cumulativo:** expande informações anteriores, nunca descarta
- **Auto-reply detectado:** retorna `<IGNORAR_AUTO>motivo</IGNORAR_AUTO>` apenas
- **IA detection:** admite com leveza, pivota para o valor da solução

### Histórico e CRM
- Histórico: `disparo:history:{phone}` (lista de mensagens)
- CRM: `disparo:lead:{phone}` (JSON: nome, nicho, resumo, event_id, whatsapp)
- Disparo inicial (n8n) é salvo no histórico
- Follow-ups são salvos no histórico após envio
- Resumo é lido como "Resumo acumulado", não "última conversa"

## Arquivos Críticos

- `worker.py` — LLM + function calling + CRM/histórico
- `api.py` — webhook recebidor, debounce, agendador de follow-ups
- `scheduler.py` — envia follow-ups no horário certo
- `workflows/sdr_mya.md` — system prompt do bot
- `tools/manage_calendar.py` — Google Calendar API
- `tools/manage_history.py` — Redis history
- `tools/manage_leads.py` — Redis CRM

## Infraestrutura

- VPS/Portainer, Docker Swarm, Traefik
- Fila: RabbitMQ (queue `mya_disparo`)
- Redis: compartilhado, isolamento via prefixo `disparo:`
- LLM: Google GenAI (Gemini)
- WhatsApp: UAZAPI (`https://strategicai.uazapi.com`)
- CI/CD: GitHub Actions → `ghcr.io/gustavocastilho-hub/mya-disparo:latest`
- Após push, redeploy manualmente no Portainer

---

Dúvidas? Pergunte durante o trabalho.
