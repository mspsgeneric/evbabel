
# EVlogger Translator (Modular)

Tradutor simples PT↔EN para Discord, com espelho por canal e envio via webhook (apelido/avatares).

## Requisitos
- Python 3.10+
- `pip install -r requirements.txt`
- `.env` com `DISCORD_TOKEN=...`

## Rodar
```bash
python main.py
```

## Estrutura
- `evtranslator/config.py` — env, tunables e constantes
- `evtranslator/db.py` — SQLite (pares de canais)
- `evtranslator/translate.py` — cliente Google Web (gtx) com retry/backoff
- `evtranslator/webhook.py` — envio como autor via webhook
- `evtranslator/bot.py` — inicialização do bot e cogs
- `evtranslator/cogs/links.py` — comandos `/link_pt_en`, `/unlink`, `/unlink_all`, `/links`
- `evtranslator/cogs/relay.py` — handler de mensagens e tradução
- `main.py` — ponto de entrada

## Variáveis de ambiente úteis
- `CONCURRENCY` (padrão 6)
- `HTTP_TIMEOUT` (padrão 15)
- `RETRIES` (padrão 4)
- `BACKOFF_BASE` (padrão 0.5)
- `CHANNEL_COOLDOWN` (padrão 0.15)
- `USER_COOLDOWN` (padrão 2.0)
- `TEST_GUILD_ID` para sync de slash imediato no servidor de teste
