#!/usr/bin/env python3
"""MACD Crossover Alert Bot — standalone Telegram bot for group chats.
Runs on Railway (or any Python host). No API keys needed for market data.

Commands:
  /macd           — check MACD crossover now (default ETHUSDT)
  /macd BTCUSDT   — check specific pair
  /macd watch BTCUSDT — add pair to hourly watch
  /macd stop BTCUSDT  — remove pair from watch
  /macd list      — show active watches
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WATCHLIST_FILE = os.environ.get("WATCHLIST_FILE", "/tmp/macd_watchlist.json")
MAX_WATCHES = 10
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


# === MACD calculation ===

def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def compute_macd(closes: list[float]) -> dict | None:
    """Compute MACD(12,26,9) from closing prices. Returns last 2 bars."""
    if len(closes) < 35:  # Need at least 26 + 9 for signal line
        return None

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    # Align: ema12 starts at index 11, ema26 at index 25
    # MACD line = ema12 - ema26, aligned from index 25
    offset = 26 - 12  # = 14
    macd_line = [ema12[i + offset] - ema26[i] for i in range(len(ema26))]

    if len(macd_line) < 9:
        return None

    signal_line = ema(macd_line, 9)

    if len(signal_line) < 2:
        return None

    # Last 2 bars
    macd_prev = macd_line[-2]
    macd_curr = macd_line[-1]
    sig_prev = signal_line[-2]
    sig_curr = signal_line[-1]

    # Detect crossover
    cross = None
    if macd_prev <= sig_prev and macd_curr > sig_curr:
        cross = "BULLISH"
    elif macd_prev >= sig_prev and macd_curr < sig_curr:
        cross = "BEARISH"

    return {
        "macd": round(macd_curr, 4),
        "signal": round(sig_curr, 4),
        "diff": round(macd_curr - sig_curr, 4),
        "cross": cross,
    }


# === Binance API ===

async def fetch_macd(pair: str) -> dict | None:
    """Fetch 1h candles from Binance and compute MACD."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(BINANCE_KLINES, params={
            "symbol": pair.upper(),
            "interval": "1h",
            "limit": 100,
        })
        if resp.status_code != 200:
            return None
        data = resp.json()

    closes = [float(c[4]) for c in data]  # index 4 = close price
    price = closes[-1]

    result = compute_macd(closes)
    if result:
        result["price"] = price
        result["pair"] = pair.upper()
    return result


# === Watchlist ===

def load_watchlist() -> dict:
    """Load watchlist: {chat_id: [pairs]}"""
    if Path(WATCHLIST_FILE).exists():
        try:
            return json.loads(Path(WATCHLIST_FILE).read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def save_watchlist(wl: dict):
    Path(WATCHLIST_FILE).write_text(json.dumps(wl, indent=2))


# === Bot handlers ===

async def macd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /macd command."""
    args = context.args or []
    chat_id = str(update.effective_chat.id)

    # /macd list
    if args and args[0].lower() == "list":
        wl = load_watchlist()
        pairs = wl.get(chat_id, [])
        if not pairs:
            await update.message.reply_text("No active watches. Use /macd watch PAIR to add one.")
        else:
            await update.message.reply_text(f"Active watches ({len(pairs)}/{MAX_WATCHES}):\n" + "\n".join(f"• {p}" for p in pairs))
        return

    # /macd watch PAIR
    if args and args[0].lower() == "watch":
        if len(args) < 2:
            await update.message.reply_text("Usage: /macd watch BTCUSDT")
            return
        pair = args[1].upper()
        wl = load_watchlist()
        chat_pairs = wl.get(chat_id, [])
        if pair in chat_pairs:
            await update.message.reply_text(f"{pair} already watched.")
            return
        if len(chat_pairs) >= MAX_WATCHES:
            await update.message.reply_text(f"Max {MAX_WATCHES} watches. Remove one first.")
            return
        # Validate pair exists
        result = await fetch_macd(pair)
        if result is None:
            await update.message.reply_text(f"Can't fetch {pair}. Check the pair name (e.g. BTCUSDT, ETHUSDT).")
            return
        chat_pairs.append(pair)
        wl[chat_id] = chat_pairs
        save_watchlist(wl)
        await update.message.reply_text(f"Now watching {pair} (hourly). {len(chat_pairs)}/{MAX_WATCHES} slots used.")
        return

    # /macd stop PAIR
    if args and args[0].lower() == "stop":
        if len(args) < 2:
            await update.message.reply_text("Usage: /macd stop BTCUSDT")
            return
        pair = args[1].upper()
        wl = load_watchlist()
        chat_pairs = wl.get(chat_id, [])
        if pair not in chat_pairs:
            await update.message.reply_text(f"{pair} not in watchlist.")
            return
        chat_pairs.remove(pair)
        wl[chat_id] = chat_pairs
        save_watchlist(wl)
        await update.message.reply_text(f"Stopped watching {pair}. {len(chat_pairs)}/{MAX_WATCHES} slots used.")
        return

    # /macd [PAIR] — check now
    pair = args[0].upper() if args else "ETHUSDT"
    result = await fetch_macd(pair)

    if result is None:
        await update.message.reply_text(f"Can't fetch {pair}. Check the pair name.")
        return

    if result["cross"]:
        emoji = "🟢" if result["cross"] == "BULLISH" else "🔴"
        msg = (
            f"{emoji} {result['pair']} 1h MACD {result['cross']} cross detected\n"
            f"MACD: {result['macd']} | Signal: {result['signal']}\n"
            f"Price: ${result['price']:,.2f}"
        )
    else:
        msg = (
            f"{result['pair']} — no cross\n"
            f"MACD: {result['macd']} | Signal: {result['signal']} | Diff: {result['diff']}\n"
            f"Price: ${result['price']:,.2f}"
        )

    await update.message.reply_text(msg)


# === Hourly cron check ===

async def hourly_check(context: ContextTypes.DEFAULT_TYPE):
    """Check all watched pairs for crossovers. Silent unless cross detected."""
    wl = load_watchlist()
    for chat_id, pairs in wl.items():
        for pair in pairs:
            try:
                result = await fetch_macd(pair)
                if result and result["cross"]:
                    emoji = "🟢" if result["cross"] == "BULLISH" else "🔴"
                    msg = (
                        f"{emoji} {result['pair']} 1h MACD {result['cross']} cross detected\n"
                        f"MACD: {result['macd']} | Signal: {result['signal']}\n"
                        f"Price: ${result['price']:,.2f}"
                    )
                    await context.bot.send_message(chat_id=int(chat_id), text=msg)
            except Exception as e:
                log.error(f"Error checking {pair} for {chat_id}: {e}")


# === Main ===

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("macd", macd_command))
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text(
        "MACD Crossover Bot\n\n"
        "/macd — check ETHUSDT now\n"
        "/macd BTCUSDT — check specific pair\n"
        "/macd watch BTCUSDT — hourly alerts\n"
        "/macd stop BTCUSDT — remove alerts\n"
        "/macd list — show watches\n\n"
        "Uses Binance 1h candles. MACD(12,26,9)."
    )))

    # Schedule hourly check
    job_queue = app.job_queue
    job_queue.run_repeating(hourly_check, interval=3600, first=10)

    log.info("MACD bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
