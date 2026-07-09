from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"

app = FastAPI(title="DEMA v7 IQ Backend", version="2026.07.09-fixed")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

lock = Lock()
IQ = None
last_error: str | None = None
connected_at: float | None = None
autotrade = False
pnl_day = 0.0
trades_today = 0
account_type_runtime: str | None = None


def load_config() -> dict[str, Any]:
    file_cfg: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            file_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"config.json inválido: {exc}")

    def pick(env_name: str, json_name: str, default: Any = None) -> Any:
        value = os.getenv(env_name)
        if value not in (None, ""):
            return value
        return file_cfg.get(json_name, default)

    return {
        "email": pick("IQ_EMAIL", "email", ""),
        "password": pick("IQ_PASSWORD", "password", ""),
        "account_type": str(pick("IQ_ACCOUNT_TYPE", "account_type", "PRACTICE")).upper(),
        "stake": float(pick("STAKE", "stake", 1) or 1),
        "asset": pick("ASSET", "asset", "EURUSD-OTC"),
        "expiration_minutes": int(pick("EXPIRATION_MINUTES", "expiration_minutes", 1) or 1),
        "max_trades_day": int(pick("MAX_TRADES_DAY", "max_trades_day", 20) or 20),
        "stop_win": float(pick("STOP_WIN", "stop_win", 20) or 20),
        "stop_loss": float(pick("STOP_LOSS", "stop_loss", 10) or 10),
    }


def import_iq_option():
    try:
        from iqoptionapi.stable_api import IQ_Option

        return IQ_Option, None
    except Exception as exc:
        return None, f"Falha ao carregar iqoptionapi local: {exc}"


def api_connected() -> bool:
    global IQ
    if IQ is None:
        return False
    try:
        if hasattr(IQ, "check_connect"):
            return bool(IQ.check_connect())
    except Exception:
        return False
    return True


def safe_balance() -> float | None:
    if IQ is None or not api_connected():
        return None
    try:
        value = IQ.get_balance()
        return float(value) if value is not None else None
    except Exception:
        return None


class SwitchRequest(BaseModel):
    account_type: Literal["PRACTICE", "REAL"]


class TradeRequest(BaseModel):
    action: Literal["call", "put", "CALL", "PUT"]
    asset: str | None = None
    stake: float | None = Field(default=None, gt=0)
    expiration_minutes: int | None = Field(default=None, ge=1, le=60)


@app.get("/")
def root():
    return {"ok": True, "service": "DEMA v7 IQ Backend", "docs": "/docs"}


@app.get("/health")
def health():
    IQ_Option, import_error = import_iq_option()
    return {
        "ok": True,
        "iq_api_available": IQ_Option is not None,
        "iq_api_error": import_error,
        "time": int(time.time()),
    }


@app.get("/status")
def status():
    cfg = load_config()
    IQ_Option, import_error = import_iq_option()
    return {
        "connected": api_connected(),
        "account_type": account_type_runtime or cfg["account_type"],
        "balance": safe_balance(),
        "autotrade": autotrade,
        "pnl_day": pnl_day,
        "trades_today": trades_today,
        "configured": bool(cfg["email"] and cfg["password"]),
        "stake": cfg["stake"],
        "asset": cfg["asset"],
        "iq_api_available": IQ_Option is not None,
        "iq_api_error": import_error,
        "last_error": last_error,
        "connected_at": connected_at,
    }


@app.post("/connect")
def connect():
    global IQ, last_error, connected_at, account_type_runtime
    cfg = load_config()
    if not cfg["email"] or not cfg["password"]:
        raise HTTPException(
            status_code=400,
            detail="Configure IQ_EMAIL e IQ_PASSWORD no Environment do Render, ou use config.json.",
        )

    IQ_Option, import_error = import_iq_option()
    if IQ_Option is None:
        raise HTTPException(status_code=500, detail=import_error)

    with lock:
        try:
            client = IQ_Option(cfg["email"], cfg["password"])
            result = client.connect()
            ok = False
            reason = result
            if isinstance(result, tuple):
                ok = bool(result[0])
                reason = result[1] if len(result) > 1 else result[0]
            else:
                ok = bool(result)
            if not ok:
                last_error = f"IQ não conectou: {reason}"
                raise HTTPException(status_code=401, detail=last_error)
            client.change_balance(cfg["account_type"])
            IQ = client
            account_type_runtime = cfg["account_type"]
            connected_at = time.time()
            last_error = None
            return {"ok": True, "connected": True, "account_type": account_type_runtime, "balance": safe_balance()}
        except HTTPException:
            raise
        except Exception as exc:
            last_error = f"Erro ao conectar: {exc}"
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=last_error)


@app.post("/switch")
def switch_account(payload: SwitchRequest):
    global account_type_runtime, last_error
    if IQ is None or not api_connected():
        raise HTTPException(status_code=400, detail="Conecte na IQ primeiro.")
    try:
        IQ.change_balance(payload.account_type)
        account_type_runtime = payload.account_type
        last_error = None
        return {"ok": True, "account_type": account_type_runtime, "balance": safe_balance()}
    except Exception as exc:
        last_error = f"Erro ao trocar conta: {exc}"
        raise HTTPException(status_code=500, detail=last_error)


@app.post("/autotrade/start")
def start_autotrade():
    global autotrade
    if IQ is None or not api_connected():
        raise HTTPException(status_code=400, detail="Conecte na IQ primeiro.")
    autotrade = True
    return {"ok": True, "autotrade": autotrade}


@app.post("/autotrade/stop")
def stop_autotrade():
    global autotrade
    autotrade = False
    return {"ok": True, "autotrade": autotrade}


@app.post("/trade")
def trade(payload: TradeRequest):
    global trades_today, pnl_day, last_error
    cfg = load_config()
    if not autotrade:
        raise HTTPException(status_code=400, detail="Auto-trade está desligado.")
    if IQ is None or not api_connected():
        raise HTTPException(status_code=400, detail="Conecte na IQ primeiro.")
    if trades_today >= cfg["max_trades_day"]:
        raise HTTPException(status_code=400, detail="Limite diário de trades atingido.")
    if pnl_day >= cfg["stop_win"]:
        raise HTTPException(status_code=400, detail="Stop win atingido.")
    if pnl_day <= -abs(cfg["stop_loss"]):
        raise HTTPException(status_code=400, detail="Stop loss atingido.")

    asset = payload.asset or cfg["asset"]
    stake = payload.stake or cfg["stake"]
    direction = payload.action.lower()
    expiration = payload.expiration_minutes or cfg["expiration_minutes"]

    try:
        ok, order_id = IQ.buy(stake, asset, direction, expiration)
        if not ok:
            last_error = f"Ordem recusada pela IQ: {order_id}"
            raise HTTPException(status_code=400, detail=last_error)
        trades_today += 1
        last_error = None
        return {"ok": True, "order_id": order_id, "asset": asset, "action": direction, "stake": stake}
    except HTTPException:
        raise
    except Exception as exc:
        last_error = f"Erro ao enviar ordem: {exc}"
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=last_error)
