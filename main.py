"""
DEMA v7 — Backend FastAPI para IQ Option
=========================================
Ponte entre o painel web (Lovable) e sua conta IQ Option.

Endpoints:
  POST /connect          -> loga na IQ (email/senha do .env)
  GET  /status           -> conectado? saldo? tipo de conta?
  GET  /balance          -> saldo atual
  POST /switch           -> troca PRACTICE <-> REAL
  GET  /assets           -> ativos abertos + payout
  POST /buy              -> abre ordem manual
  GET  /result/{id}      -> checa resultado da ordem
  POST /autotrade/start  -> liga auto-trade (consome /api/public/signals do painel)
  POST /autotrade/stop   -> desliga
  GET  /autotrade/status -> estado + histórico + P&L

Rodar local:
  pip install -r requirements.txt
  cp .env.example .env   # preenche IQ_EMAIL / IQ_PASSWORD
  uvicorn main:app --reload --port 8000

Deploy Render/Railway: usa o Procfile.
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from iqoptionapi.stable_api import IQ_Option  # type: ignore
except ImportError as e:  # pragma: no cover
    raise RuntimeError(
        "Instale: pip install git+https://github.com/n1nj4z33/iqoptionapi.git"
    ) from e

load_dotenv()

# ---------- Config via .env ----------
IQ_EMAIL = os.getenv("IQ_EMAIL", "")
IQ_PASSWORD = os.getenv("IQ_PASSWORD", "")
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "PRACTICE").upper()   # PRACTICE | REAL
DEFAULT_STAKE = float(os.getenv("DEFAULT_STAKE", "1"))
DEFAULT_EXPIRATION = int(os.getenv("DEFAULT_EXPIRATION", "1"))  # minutos
MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "75"))
MARTINGALE_LEVELS = int(os.getenv("MARTINGALE_LEVELS", "0"))
MARTINGALE_FACTOR = float(os.getenv("MARTINGALE_FACTOR", "2.2"))
STOP_WIN = float(os.getenv("STOP_WIN_DAILY", "0"))    # 0 = desativado
STOP_LOSS = float(os.getenv("STOP_LOSS_DAILY", "0"))
SIGNALS_URL = os.getenv(
    "SIGNALS_URL",
    "https://cheerful-data-glimmer.lovable.app/api/public/signals",
)
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "5"))
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# ---------- Estado global ----------
class BotState:
    iq: Optional[IQ_Option] = None
    connected: bool = False
    autotrade: bool = False
    pnl_day: float = 0.0
    history: list = []           # [{ts, asset, dir, stake, result, profit}]
    seen_signals: set = set()    # dedupe por (asset+ts)
    task: Optional[asyncio.Task] = None

S = BotState()


# ---------- Modelos ----------
class BuyBody(BaseModel):
    asset: str
    direction: str          # "call" | "put"
    stake: Optional[float] = None
    expiration: Optional[int] = None

class SwitchBody(BaseModel):
    account_type: str       # PRACTICE | REAL


# ---------- Helpers IQ ----------
def _ensure_conn() -> IQ_Option:
    if not S.iq or not S.connected:
        raise HTTPException(401, "Não conectado. Chame POST /connect primeiro.")
    return S.iq

def _do_connect() -> tuple[bool, str]:
    if not IQ_EMAIL or not IQ_PASSWORD:
        return False, "IQ_EMAIL / IQ_PASSWORD ausentes no .env"
    iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
    ok, reason = iq.connect()
    if not ok:
        return False, f"Falha no login: {reason}"
    iq.change_balance(ACCOUNT_TYPE)
    S.iq = iq
    S.connected = True
    return True, "conectado"


# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    if IQ_EMAIL and IQ_PASSWORD:
        try:
            _do_connect()
        except Exception as e:
            print(f"[warn] auto-connect falhou: {e}")
    yield
    if S.task:
        S.task.cancel()


app = FastAPI(title="DEMA v7 IQ Bridge", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Endpoints básicos ----------
@app.post("/connect")
def connect():
    ok, msg = _do_connect()
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "account_type": ACCOUNT_TYPE, "balance": S.iq.get_balance()}

@app.get("/status")
def status():
    return {
        "connected": S.connected,
        "account_type": ACCOUNT_TYPE if S.connected else None,
        "balance": S.iq.get_balance() if S.connected and S.iq else None,
        "autotrade": S.autotrade,
        "pnl_day": round(S.pnl_day, 2),
        "trades_today": len(S.history),
    }

@app.get("/balance")
def balance():
    iq = _ensure_conn()
    return {"balance": iq.get_balance(), "type": ACCOUNT_TYPE}

@app.post("/switch")
def switch(body: SwitchBody):
    iq = _ensure_conn()
    if body.account_type not in ("PRACTICE", "REAL"):
        raise HTTPException(400, "account_type deve ser PRACTICE ou REAL")
    iq.change_balance(body.account_type)
    global ACCOUNT_TYPE
    ACCOUNT_TYPE = body.account_type
    return {"ok": True, "account_type": ACCOUNT_TYPE, "balance": iq.get_balance()}

@app.get("/assets")
def assets():
    iq = _ensure_conn()
    open_times = iq.get_all_open_time()
    profits = iq.get_all_profit()
    out = []
    for asset, info in open_times.get("turbo", {}).items():
        if info.get("open"):
            payout = profits.get(asset, {}).get("turbo", 0) * 100
            if payout >= MIN_PAYOUT:
                out.append({"asset": asset, "payout": round(payout, 1)})
    return {"assets": sorted(out, key=lambda x: -x["payout"])}


# ---------- Ordens ----------
def _place_order(asset: str, direction: str, stake: float, expiration: int):
    iq = _ensure_conn()
    ok, order_id = iq.buy(stake, asset, direction, expiration)
    if not ok:
        raise HTTPException(400, f"Falha ao abrir ordem em {asset}")
    return order_id

@app.post("/buy")
def buy(body: BuyBody):
    if body.direction not in ("call", "put"):
        raise HTTPException(400, "direction deve ser call ou put")
    stake = body.stake or DEFAULT_STAKE
    exp = body.expiration or DEFAULT_EXPIRATION
    order_id = _place_order(body.asset, body.direction, stake, exp)
    return {"ok": True, "order_id": order_id, "asset": body.asset, "stake": stake}

@app.get("/result/{order_id}")
def result(order_id: int):
    iq = _ensure_conn()
    profit = iq.check_win_v4(order_id)
    win = profit[1] if isinstance(profit, (list, tuple)) else profit
    return {"order_id": order_id, "profit": win}


# ---------- Auto-trade ----------
async def _autotrade_loop():
    print("[autotrade] iniciado")
    async with httpx.AsyncClient(timeout=10) as client:
        while S.autotrade:
            try:
                # stop-daily
                if STOP_WIN and S.pnl_day >= STOP_WIN:
                    print(f"[autotrade] stop-win atingido ({S.pnl_day})")
                    S.autotrade = False
                    break
                if STOP_LOSS and S.pnl_day <= -abs(STOP_LOSS):
                    print(f"[autotrade] stop-loss atingido ({S.pnl_day})")
                    S.autotrade = False
                    break

                r = await client.get(SIGNALS_URL)
                if r.status_code == 200:
                    data = r.json()
                    signals = data.get("signals", []) if isinstance(data, dict) else data
                    for sig in signals:
                        key = f"{sig.get('asset')}-{sig.get('timestamp') or sig.get('ts')}"
                        if key in S.seen_signals:
                            continue
                        S.seen_signals.add(key)

                        asset = sig.get("asset", "").replace("USDT", "").upper()
                        direction = (sig.get("direction") or sig.get("signal") or "").lower()
                        if direction in ("buy", "long"): direction = "call"
                        if direction in ("sell", "short"): direction = "put"
                        if direction not in ("call", "put"):
                            continue

                        # payout check
                        try:
                            profits = S.iq.get_all_profit()
                            payout = profits.get(asset, {}).get("turbo", 0) * 100
                            if payout < MIN_PAYOUT:
                                print(f"[skip] {asset} payout {payout}% < {MIN_PAYOUT}")
                                continue
                        except Exception:
                            pass

                        # martingale
                        stake = DEFAULT_STAKE
                        for lvl in range(MARTINGALE_LEVELS + 1):
                            try:
                                oid = _place_order(asset, direction, stake, DEFAULT_EXPIRATION)
                                print(f"[order] {asset} {direction} ${stake} exp{DEFAULT_EXPIRATION}m id={oid}")
                                await asyncio.sleep(DEFAULT_EXPIRATION * 60 + 3)
                                profit = S.iq.check_win_v4(oid)
                                pnl = profit[1] if isinstance(profit, (list, tuple)) else profit
                                pnl = float(pnl or 0)
                                S.pnl_day += pnl
                                S.history.append({
                                    "ts": int(time.time()),
                                    "asset": asset, "dir": direction,
                                    "stake": stake, "profit": pnl,
                                    "result": "WIN" if pnl > 0 else "LOSS",
                                })
                                if pnl > 0:
                                    break  # win -> sai do martingale
                                stake = round(stake * MARTINGALE_FACTOR, 2)
                            except HTTPException as e:
                                print(f"[order-err] {e.detail}")
                                break
            except Exception as e:
                print(f"[autotrade] erro: {e}")
            await asyncio.sleep(POLL_SECONDS)
    print("[autotrade] parado")

@app.post("/autotrade/start")
async def autotrade_start():
    _ensure_conn()
    if S.autotrade:
        return {"ok": True, "already": True}
    S.autotrade = True
    S.task = asyncio.create_task(_autotrade_loop())
    return {"ok": True, "signals_url": SIGNALS_URL, "poll_seconds": POLL_SECONDS}

@app.post("/autotrade/stop")
def autotrade_stop():
    S.autotrade = False
    return {"ok": True}

@app.get("/autotrade/status")
def autotrade_status():
    wins = sum(1 for h in S.history if h["result"] == "WIN")
    losses = sum(1 for h in S.history if h["result"] == "LOSS")
    return {
        "running": S.autotrade,
        "pnl_day": round(S.pnl_day, 2),
        "wins": wins, "losses": losses,
        "winrate": round(wins / max(1, wins + losses) * 100, 1),
        "last_10": S.history[-10:],
    }
