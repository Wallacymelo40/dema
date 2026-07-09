# DEMA v7 IQ Backend — corrigido para Render

Este pacote já corrige o problema do Render usando Python 3.14. Ele força Python **3.11.9** com `runtime.txt`, `.python-version`, `render.yaml` e também inclui `Dockerfile` caso você escolha Docker.

## O que subir no GitHub

Suba os arquivos **soltos** no repositório, não suba o ZIP fechado.

Arquivos obrigatórios:

- `main.py`
- `requirements.txt`
- `runtime.txt`
- `.python-version`
- `Procfile`
- `.env.example`
- `README.md`

Arquivos opcionais, mas recomendados:

- `Dockerfile`
- `render.yaml`
- `.gitignore`

## Configuração no Render — opção mais simples

Crie/edite o Web Service assim:

- **Runtime/Language:** Python 3
- **Build Command:**

```bash
python -m pip install --upgrade pip setuptools wheel && python -m pip install -r requirements.txt
```

- **Start Command:**

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

- **Environment Variables:**

```env
PYTHON_VERSION=3.11.9
IQ_EMAIL=seu_email_da_iq
IQ_PASSWORD=sua_senha_da_iq
ACCOUNT_TYPE=PRACTICE
DEFAULT_STAKE=1
DEFAULT_EXPIRATION=1
MIN_PAYOUT=75
MARTINGALE_LEVELS=0
MARTINGALE_FACTOR=2.2
STOP_WIN_DAILY=20
STOP_LOSS_DAILY=15
SIGNALS_URL=https://cheerful-data-glimmer.lovable.app/api/public/signals
POLL_SECONDS=5
ALLOWED_ORIGINS=*
```

Depois clique em **Manual Deploy → Clear build cache & deploy**.

## Se ainda aparecer Python 3.14

No Render, adicione obrigatoriamente esta variável:

```env
PYTHON_VERSION=3.11.9
```

Depois use **Manual Deploy → Clear build cache & deploy**. Não use só “Deploy latest commit”.

## Teste quando ficar Live

Abra no navegador:

```text
https://SEU-APP.onrender.com/
https://SEU-APP.onrender.com/health
https://SEU-APP.onrender.com/status
```

Se aparecer JSON, o backend está online. Depois cole essa URL no painel e clique em **Testar conexão**.

## Rotas

| Método | Rota | Função |
|---|---|---|
| GET | `/` | Serviço online |
| GET | `/health` | Teste rápido |
| POST | `/connect` | Conecta na IQ |
| GET | `/status` | Status, saldo e P&L |
| POST | `/switch` | Troca `PRACTICE`/`REAL` |
| GET | `/assets` | Ativos abertos com payout |
| POST | `/buy` | Ordem manual |
| GET | `/result/{order_id}` | Resultado da ordem |
| POST | `/autotrade/start` | Liga auto-trade |
| POST | `/autotrade/stop` | Desliga auto-trade |
| GET | `/autotrade/status` | Histórico e winrate |

## Aviso

Comece sempre em `ACCOUNT_TYPE=PRACTICE`. Só use `REAL` depois de validar conexão, sinais, stake e stops.
