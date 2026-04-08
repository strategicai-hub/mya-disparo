# Instruções para Claude Code

Guia de como trabalhar neste projeto Mya Disparo Bot.

---

## 📋 Padrão de Resposta

- **Idioma:** Português (Brasil)
- **Tom:** Conciso e direto, sem filler ou explicações desnecessárias
- **Estrutura:** Leve com a informação, não restate o que foi pedido
- Máximo 2-3 frases por parágrafo

## 🔧 Comportamentos Esperados

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
- sempre faça 'push' junto com 'commit'


### Mudanças de Código
- Use **Edit** para modificar arquivos existentes
- Use **Write** para criar novos arquivos
- Não adicione comentários/docstrings em código que não alterou
- Leia o arquivo primeiro antes de editar

## 🎯 Regras de Negócio (Mya SDR)

### Google Calendar
- Service account usa `sendUpdates="none"` (sem attendees)
- Telefone é formatado com "55" prefixo
- Event ID deve ser salvo em Redis após criar_evento suceder
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

## 📂 Arquivos Críticos

- `worker.py` — LLM + function calling + CRM/histórico
- `api.py` — webhook recebidor, debounce, agendador de follow-ups
- `scheduler.py` — envia follow-ups no horário certo
- `workflows/sdr_mya.md` — system prompt do bot
- `tools/manage_calendar.py` — Google Calendar API
- `tools/manage_history.py` — Redis history
- `tools/manage_leads.py` — Redis CRM

## 🚀 Deployment

- VPS/Portainer, docker-compose
- Credenciais via variáveis de ambiente (GOOGLE_CREDENTIALS_JSON, REDIS_URL, etc)
- Imagem: `ghcr.io/gustavocastilho-hub/mya-disparo:latest`
- Após push, redeploy manualmente no Portainer

---

Dúvidas? Pergunte durante o trabalho.
