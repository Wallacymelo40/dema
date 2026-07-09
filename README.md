# DEMA v7 IQ Backend

Backend FastAPI para conectar o painel online com a IQ Option.

## Render

Use estes campos no Render:

- **Language**: Python 3
- **Root Directory**: deixe vazio
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn main:app --host 0.0.0.0 --port 10000`

Depois configure as variáveis em **Environment**:

- `IQ_EMAIL`
- `IQ_PASSWORD`
- `IQ_ACCOUNT_TYPE` = `PRACTICE` ou `REAL`
- `STAKE` = valor da entrada

Teste no navegador:

- `/health`
- `/status`

Este pacote já inclui a pasta `iqoptionapi`, então não depende de instalar `iqoptionapi` pelo pip.
