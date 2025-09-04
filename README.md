
# 1. Criar README.md inicial
@"
# EVbabel

Bot de tradução para Discord (fork do módulo de tradução do EVlogger).

## Requisitos

- Python 3.10+
- Discord Bot Token
- Supabase (para quotas e controle)

## Como rodar local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# rodar o bot
python main.py


## Variáveis de ambiente úteis
- `CONCURRENCY` (padrão 6)
- `HTTP_TIMEOUT` (padrão 15)
- `RETRIES` (padrão 4)
- `BACKOFF_BASE` (padrão 0.5)
- `CHANNEL_COOLDOWN` (padrão 0.15)
- `USER_COOLDOWN` (padrão 2.0)
- `TEST_GUILD_ID` para sync de slash imediato no servidor de teste
