"""
DEMA v7 — Backend FastAPI para IQ Option
=========================================
Backend ponte para conectar o painel web à conta IQ Option usando iqoptionapi.

Rotas principais:
  GET  /                -> confirmação de serviço online
  GET  /health          -> health check para Render
  POST /connect         -> conecta na IQ usando IQ_EMAIL/IQ_PASSWORD do ambiente
  GET  /status          -> conexão, saldo, conta, auto-trade e P&L
  POST /switch          -> troca PRACTICE/REAL
  POST /buy             -> abre ordem manual
  POST /autotrade/start -> liga auto-trade consumindo SIGNALS_URL
  POST /autotrade/stop  -> desliga auto-trade
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

IQ_Option = None
IQ_IMPORT_ERROR: str | None = None
try:
    from iqoptionapi.stable_api import IQ_Option  # type: ignore
except Exception as exc:  # pragma: no cover
    IQ_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

load_dotenv()


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


IQ_EMAIL = os.getenv("IQ_EMAIL", "").strip()
IQ_PASSWORD = os.getenv("IQ_PASSWORD", "").strip()
START_ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "PRACTICE").strip().upper()
DEFAULT_STAKE = env_float("DEFAULT_STAKE", 1.0)
DEFAULT_EXPIRATION = env_int("DEFAULT_EXPIRATION", 1)
MIN_PAYOUT = env_float("MIN_PAYOUT", 75.0)
MARTINGALE_LEVELS = env_int("MARTINGALE_LEVELS", 0)
MARTINGALE_FACTOR = env_float("MARTINGALE_FACTOR", 2.2)
STOP_WIN_DAILY = env_float("STOP_WIN_DAILY", 0.0)
STOP_LOSS_DAILY = env_float("STOP_LOSS_DAILY", 0.0)
SIGNALS_URL = os.getenv(
    "SIGNALS_URL",
    "https://cheerful-data-glimmer.lovable.app/api/public/signals",
).strip()
POLL_SECONDS = max(2, env_int("POLL_SECONDS", 5))
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]


@dataclass
class BotState:
    iq: Optional[Any] = None
    connected: bool = False
    account_type: str = START_ACCOUNT_TYPE if START_ACCOUNT_TYPE in {"PRACTICE", "REAL"} else "PRACTICE"
    autotrade: bool = False
    pnl_day: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)
    seen_signals: set[str] = field(default_factory=set)
    task: Optional[asyncio.Task] = None
    last_error: Optional[str] = None


S = BotState()


class BuyBody(BaseModel):
    asset: str = Field(..., min_length=2)
    direction: str = Field(..., description="call ou put")
    stake: Optional[float] = Field(default=None, gt=0)
    expiration: Optional[int] = Field(default=None, gt=0)


class SwitchBody(BaseModel):
    account_type: str = Field(..., description="PRACTICE ou REAL")


def _safe_balance() -> Optional[float]:
    if not S.connected or not S.iq:
        return None
    try:
        return float(S.iq.get_balance())
    except Exception as exc:
        S.last_error = f"Erro ao ler saldo: {exc}"
        return None


def _ensure_conn() -> Any:
    if not S.iq or not S.connected:
        raise HTTPException(status_code=401, detail="Não conectado. Clique em Conectar primeiro.")
    return S.iq


def _do_connect() -> tuple[bool, str]:
    if IQ_Option is None:
        return False, f"Biblioteca iqoptionapi indisponível no servidor ({IQ_IMPORT_ERROR}). Verifique requirements.txt."
    if not IQ_EMAIL or not IQ_PASSWORD:
        return False, "Configure IQ_EMAIL e IQ_PASSWORD nas variáveis de ambiente do Render."

    try:
        iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
        ok, reason = iq.connect()
    except Exception as exc:
        S.connected = False
        S.last_error = str(exc)
        return False, f"Falha ao conectar: {exc}"

    if not ok:
        S.connected = False
        S.last_error = str(reason)
        return False, f"Login recusado pela IQ Option: {reason}"

    try:
        iq.change_balance(S.account_type)
    except Exception as exc:
        S.connected = False
        S.last_error = str(exc)
        return False, f"Conectou, mas falhou ao selecionar {S.account_type}: {exc}"

    S.iq = iq
    S.connected = True
    S.last_error = None
    return True, "conectado"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tenta conectar sozinho se as credenciais estiverem configuradas.
    if IQ_EMAIL and IQ_PASSWORD:
        ok, msg = await asyncio.to_thread(_do_connect)
        if not ok:
            print(f"[warn] auto-connect falhou: {msg}")
    yield
    S.autotrade = False
    if S.task:
        S.task.cancel()


app = FastAPI(title="DEMA v7 IQ Backend", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "DEMA v7 IQ Backend",
        "docs": "/docs",
        "status": "/status",
    }


@app.get("/health")
def health():
    return {"ok": True, "connected": S.connected, "autotrade": S.autotrade}


@app.post("/connect")
def connect():
    ok, msg = _do_connect()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {
        "ok": True,
        "message": msg,
        "account_type": S.account_type,
        "balance": _safe_balance(),
    }


@app.get("/status")
def status():
    return {
        "connected": S.connected,
        "account_type": S.account_type if S.connected else None,
        "balance": _safe_balance(),
        "autotrade": S.autotrade,
        "pnl_day": round(S.pnl_day, 2),
        "trades_today": len(S.history),
        "last_error": S.last_error,
    }


@app.get("/balance")
def balance():
    _ensure_conn()
    return {"balance": _safe_balance(), "type": S.account_type}


@app.post("/switch")
def switch(body: SwitchBody):
    account_type = body.account_type.strip().upper()
    if account_type not in {"PRACTICE", "REAL"}:
        raise HTTPException(status_code=400, detail="account_type deve ser PRACTICE ou REAL")
    iq = _ensure_conn()
    try:
        iq.change_balance(account_type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Falha ao trocar conta: {exc}") from exc
    S.account_type = account_type
    return {"ok": True, "account_type": S.account_type, "balance": _safe_balance()}


@app.get("/assets")
def assets():
    iq = _ensure_conn()
    try:
        open_times = iq.get_all_open_time()
        profits = iq.get_all_profit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Falha ao buscar ativos: {exc}") from exc

    out: list[dict[str, Any]] = []
    for asset, info in open_times.get("turbo", {}).items():
        if info.get("open"):
            payout = float(profits.get(asset, {}).get("turbo", 0) or 0) * 100
            if payout >= MIN_PAYOUT:
                out.append({"asset": asset, "payout": round(payout, 1)})
    return {"assets": sorted(out, key=lambda item: -item["payout"])}


def _place_order(asset: str, direction: str, stake: float, expiration: int):
    iq = _ensure_conn()
    try:
        ok, order_id = iq.buy(stake, asset, direction, expiration)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro ao abrir ordem: {exc}") from exc
    if not ok:
        raise HTTPException(status_code=400, detail=f"Falha ao abrir ordem em {asset}")
    return order_id


@app.post("/buy")
def buy(body: BuyBody):
    direction = body.direction.strip().lower()
    if direction not in {"call", "put"}:
        raise HTTPException(status_code=400, detail="direction deve ser call ou put")
    stake = float(body.stake or DEFAULT_STAKE)
    expiration = int(body.expiration or DEFAULT_EXPIRATION)
    order_id = _place_order(body.asset.strip().upper(), direction, stake, expiration)
    return {"ok": True, "order_id": order_id, "asset": body.asset, "direction": direction, "stake": stake}


@app.get("/result/{order_id}")
def result(order_id: int):
    iq = _ensure_conn()
    try:
        profit = iq.check_win_v4(order_id)
        pnl = profit[1] if isinstance(profit, (list, tuple)) else profit
        return {"order_id": order_id, "profit": float(pnl or 0)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Falha ao checar resultado: {exc}") from exc


def _normalize_signal(sig: dict[str, Any]) -> tuple[str, str, str] | None:
    asset = str(sig.get("asset") or sig.get("symbol") or "").replace("USDT", "").upper().strip()
    direction = str(sig.get("direction") or sig.get("signal") or sig.get("side") or "").lower().strip()
    if direction in {"buy", "long", "alta", "call"}:
        direction = "call"
    elif direction in {"sell", "short", "baixa", "put"}:
        direction = "put"
    else:
        return None
    ts = str(sig.get("timestamp") or sig.get("ts") or sig.get("time") or int(time.time()))
    if not asset:
        return None
    return asset, direction, ts


async def _check_payout(asset: str) -> bool:
    try:
        iq = _ensure_conn()
        profits = await asyncio.to_thread(iq.get_all_profit)
        payout = float(profits.get(asset, {}).get("turbo", 0) or 0) * 100
        if payout < MIN_PAYOUT:
            print(f"[skip] {asset} payout {payout:.1f}% < {MIN_PAYOUT:.1f}%")
            return False
    except Exception as exc:
        print(f"[warn] não consegui checar payout de {asset}: {exc}")
    return True


async def _autotrade_loop():
    print("[autotrade] iniciado")
    async with httpx.AsyncClient(timeout=12) as client:
        while S.autotrade:
            try:
                if STOP_WIN_DAILY and S.pnl_day >= STOP_WIN_DAILY:
                    S.last_error = f"Stop win diário atingido: {S.pnl_day:.2f}"
                    S.autotrade = False
                    break
                if STOP_LOSS_DAILY and S.pnl_day <= -abs(STOP_LOSS_DAILY):
                    S.last_error = f"Stop loss diário atingido: {S.pnl_day:.2f}"
                    S.autotrade = False
                    break

                response = await client.get(SIGNALS_URL)
                response.raise_for_status()
                data = response.json()
                signals = data.get("signals", []) if isinstance(data, dict) else data
                if not isinstance(signals, list):
                    signals = []

                for raw_sig in signals:
                    if not isinstance(raw_sig, dict):
                        continue
                    normalized = _normalize_signal(raw_sig)
                    if not normalized:
                        continue
                    asset, direction, ts = normalized
                    key = f"{asset}-{direction}-{ts}"
                    if key in S.seen_signals:
                        continue
                    S.seen_signals.add(key)

                    if not await _check_payout(asset):
                        continue

                    stake = DEFAULT_STAKE
                    for _level in range(MARTINGALE_LEVELS + 1):
                        try:
                            order_id = await asyncio.to_thread(
                                _place_order,
                                asset,
                                direction,
                                stake,
                                DEFAULT_EXPIRATION,
                            )
                            print(f"[order] {asset} {direction} ${stake} exp={DEFAULT_EXPIRATION}m id={order_id}")
                            await asyncio.sleep(DEFAULT_EXPIRATION * 60 + 3)
                            iq = _ensure_conn()
                            profit = await asyncio.to_thread(iq.check_win_v4, order_id)
                            pnl = profit[1] if isinstance(profit, (list, tuple)) else profit
                            pnl = float(pnl or 0)
                            S.pnl_day += pnl
                            S.history.append(
                                {
                                    "ts": int(time.time()),
                                    "asset": asset,
                                    "dir": direction,
                                    "stake": stake,
                                    "profit": round(pnl, 2),
                                    "result": "WIN" if pnl > 0 else "LOSS",
                                }
                            )
                            if pnl > 0:
                                break
                            stake = round(stake * MARTINGALE_FACTOR, 2)
                        except Exception as exc:
                            S.last_error = f"Erro em ordem {asset}: {exc}"
                            print(f"[order-err] {S.last_error}")
                            break
            except Exception as exc:
                S.last_error = f"Erro no auto-trade: {exc}"
                print(f"[autotrade] erro: {exc}")

            await asyncio.sleep(POLL_SECONDS)
    print("[autotrade] parado")


@app.post("/autotrade/start")
async def autotrade_start():
    if not S.connected:
        ok, msg = await asyncio.to_thread(_do_connect)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    if S.autotrade:
        return {"ok": True, "already": True, "signals_url": SIGNALS_URL}
    S.autotrade = True
    S.task = asyncio.create_task(_autotrade_loop())
    return {"ok": True, "signals_url": SIGNALS_URL, "poll_seconds": POLL_SECONDS}


@app.post("/autotrade/stop")
def autotrade_stop():
    S.autotrade = False
    return {"ok": True}


@app.get("/autotrade/status")
def autotrade_status():
    wins = sum(1 for item in S.history if item.get("result") == "WIN")
    losses = sum(1 for item in S.history if item.get("result") == "LOSS")
    total = wins + losses
    return {
        "running": S.autotrade,
        "pnl_day": round(S.pnl_day, 2),
        "wins": wins,
        "losses": losses,
        "winrate": round((wins / total) * 100, 1) if total else 0,
        "last_10": S.history[-10:],
        "last_error": S.last_error,
    }
