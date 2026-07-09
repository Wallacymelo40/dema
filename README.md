# DEMA v7 — IQ Option Backend (FastAPI)

Ponte entre o painel Lovable e sua conta IQ Option, usando o fork
[`n1nj4z33/iqoptionapi`](https://github.com/n1nj4z33/iqoptionapi).

## 1. Rodar local

```bash
pip install -r requirements.txt
cp .env.example .env      # edita IQ_EMAIL / IQ_PASSWORD
uvicorn main:app --reload --port 8000
```

Testa:
```bash
curl -X POST http://localhost:8000/connect
curl http://localhost:8000/status
curl http://localhost:8000/assets
curl -X POST http://localhost:8000/autotrade/start
```

## 2. Deploy no Render (grátis)

1. Sobe essa pasta num repo GitHub.
2. render.com → New → Web Service → conecta o repo.
3. Runtime: **Python 3.11**. Build: `pip install -r requirements.txt`. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
4. Environment → cola as vars do `.env` (IQ_EMAIL, IQ_PASSWORD, etc).
5. Deploy → pega a URL `https://seu-app.onrender.com`.

## 3. Endpoints

| Método | Rota | Descrição |
|---|---|---|
| POST | `/connect` | Loga na IQ |
| GET  | `/status` | Estado geral |
| GET  | `/balance` | Saldo atual |
| POST | `/switch` | `{ "account_type": "REAL" }` |
| GET  | `/assets` | Ativos abertos + payout |
| POST | `/buy` | `{ "asset":"EURUSD","direction":"call","stake":1,"expiration":1 }` |
| GET  | `/result/{id}` | Resultado da ordem |
| POST | `/autotrade/start` | Liga consumo dos sinais DEMA v7 |
| POST | `/autotrade/stop` | Desliga |
| GET  | `/autotrade/status` | P&L + histórico |

## 4. Segurança

- **Nunca** commite o `.env`. Só o `.env.example`.
- Use `ACCOUNT_TYPE=PRACTICE` até validar tudo.
- `STOP_WIN_DAILY` / `STOP_LOSS_DAILY` protegem a banca.
- `ALLOWED_ORIGINS` restringe CORS: cole a URL do seu painel em produção.

## 5. Integração com o painel

O painel Lovable já expõe `/api/public/signals`. Basta o auto-trade
apontar pra essa URL (já vem default no `.env.example`).
