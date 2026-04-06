#!/usr/bin/env python3
"""Crypto trading bot for Telegram group chats.
Runs on Railway (or any Python host). No API keys needed for market data.

Commands:
  /macd [PAIR]         — MACD(12,26,9) crossover check
  /macd watch PAIR     — hourly crossover alerts
  /macd stop PAIR      — remove alerts
  /macd list           — show active watches
  /price [PAIR]        — quick price + 24h change
  /rsi [PAIR]          — RSI(14) overbought/oversold
  /fng                 — Fear & Greed index
  /dom                 — BTC dominance
  /start               — show all commands
"""

import os
import json
import logging
from datetime import datetime, time
from pathlib import Path

import httpx
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WATCHLIST_FILE = os.environ.get("WATCHLIST_FILE", "/tmp/macd_watchlist.json")
DAILY_FILE = os.environ.get("DAILY_FILE", "/tmp/macd_daily.json")
MAX_WATCHES = 10
DAILY_HOUR_UTC = int(os.environ.get("DAILY_HOUR_UTC", "2"))   # 02:00 UTC = 09:00 BKK
DAILY_MINUTE_UTC = int(os.environ.get("DAILY_MINUTE_UTC", "0"))
DAILY_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "INIT"]

BINANCE_ENDPOINTS = [
    "https://data-api.binance.vision/api/v3",
    "https://api.binance.us/api/v3",
    "https://api.binance.com/api/v3",
    "https://api1.binance.com/api/v3",
    "https://api2.binance.com/api/v3",
    "https://api3.binance.com/api/v3",
    "https://api4.binance.com/api/v3",
]

# Map friendly names to Binance symbols
PAIR_ALIASES = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "BNB": "BNBUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT", "AVAX": "AVAXUSDT", "DOT": "DOTUSDT",
    "LINK": "LINKUSDT", "MATIC": "MATICUSDT", "ARB": "ARBUSDT",
    "OP": "OPUSDT", "ATOM": "ATOMUSDT", "NEAR": "NEARUSDT",
    "APT": "APTUSDT", "SUI": "SUIUSDT", "INIT": "INITUSDT",
    "TIA": "TIAUSDT", "INJ": "INJUSDT", "SEI": "SEIUSDT",
}


def resolve_pair(text: str) -> str:
    """Resolve 'BTC' → 'BTCUSDT', or pass through if already a pair."""
    upper = text.upper()
    return PAIR_ALIASES.get(upper, upper)


# === Technical indicators ===

def ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def compute_macd(closes: list[float]) -> dict | None:
    if len(closes) < 35:
        return None
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    offset = 26 - 12
    macd_line = [ema12[i + offset] - ema26[i] for i in range(len(ema26))]
    if len(macd_line) < 9:
        return None
    signal_line = ema(macd_line, 9)
    if len(signal_line) < 2:
        return None

    macd_prev, macd_curr = macd_line[-2], macd_line[-1]
    sig_prev, sig_curr = signal_line[-2], signal_line[-1]

    cross = None
    if macd_prev <= sig_prev and macd_curr > sig_curr:
        cross = "BULLISH"
    elif macd_prev >= sig_prev and macd_curr < sig_curr:
        cross = "BEARISH"

    return {
        "macd": round(macd_curr, 4),
        "signal": round(sig_curr, 4),
        "diff": round(macd_curr - sig_curr, 4),
        "histogram": round(macd_curr - sig_curr, 4),
        "cross": cross,
    }


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# === Binance API ===

async def binance_get(path: str, params: dict) -> dict | list | None:
    async with httpx.AsyncClient(timeout=10) as client:
        for base in BINANCE_ENDPOINTS:
            try:
                resp = await client.get(f"{base}/{path}", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        return data
            except (httpx.TimeoutException, httpx.ConnectError):
                continue
    return None


async def fetch_klines(pair: str, interval: str = "1h", limit: int = 100) -> list | None:
    return await binance_get("klines", {
        "symbol": pair.upper(),
        "interval": interval,
        "limit": limit,
    })


async def fetch_ticker(pair: str) -> dict | None:
    return await binance_get("ticker/24hr", {"symbol": pair.upper()})


# === Watchlist ===

def load_watchlist() -> dict:
    if Path(WATCHLIST_FILE).exists():
        try:
            return json.loads(Path(WATCHLIST_FILE).read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def save_watchlist(wl: dict):
    Path(WATCHLIST_FILE).write_text(json.dumps(wl, indent=2))


# === Daily config ===

def load_daily_chats() -> list[int]:
    if Path(DAILY_FILE).exists():
        try:
            return json.loads(Path(DAILY_FILE).read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def save_daily_chats(chats: list[int]):
    Path(DAILY_FILE).write_text(json.dumps(chats))


# === Format helpers ===

def fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"


def ascii_chart(closes: list[float], width: int = 24, height: int = 8) -> str:
    """Render ASCII price chart using line-drawing characters. Last `width` candles."""
    data = closes[-width:]
    if len(data) < 2:
        return ""
    lo, hi = min(data), max(data)
    spread = hi - lo
    if spread == 0:
        return "`" + "─" * width + "`"

    # Map each data point to a row (0 = top = high, height-1 = bottom = low)
    scaled = [round((hi - v) / spread * (height - 1)) for v in data]

    rows = []
    for row in range(height):
        line = ""
        for col in range(len(data)):
            level = scaled[col]
            if level == row:
                # This data point sits on this row — draw the dot
                line += "•"
            elif col > 0:
                # Draw connecting lines between adjacent points
                prev_level = scaled[col - 1]
                curr_level = scaled[col]
                top = min(prev_level, curr_level)
                bot = max(prev_level, curr_level)
                if top < row < bot:
                    line += "│"
                else:
                    line += " "
            else:
                line += " "
        rows.append(line)

    hi_label = fmt_price(hi)
    lo_label = fmt_price(lo)
    pad = max(len(hi_label), len(lo_label))
    chart_lines = []
    for i, row in enumerate(rows):
        if i == 0:
            chart_lines.append(f"{hi_label:>{pad}} ┤{row}")
        elif i == height - 1:
            chart_lines.append(f"{lo_label:>{pad}} ┤{row}")
        else:
            chart_lines.append(f"{' ' * pad} ┤{row}")

    return "```\n" + "\n".join(chart_lines) + "\n```"


def trend_emoji(change: float) -> str:
    if change > 5:
        return "🚀"
    elif change > 2:
        return "📈"
    elif change > 0:
        return "↗️"
    elif change > -2:
        return "↘️"
    elif change > -5:
        return "📉"
    else:
        return "💀"


# === Inline coin picker ===

PICKER_COINS = [
    ["BTC", "ETH", "SOL", "BNB", "XRP"],
    ["AVAX", "DOT", "ATOM", "NEAR", "LINK"],
    ["APT", "SUI", "SEI", "ARB", "OP"],
    ["INJ", "TIA", "INIT", "DOGE", "ADA"],
]


def coin_picker_keyboard(command: str) -> InlineKeyboardMarkup:
    """Build inline button grid for a command. callback_data = 'cmd:PAIR'."""
    rows = []
    for row in PICKER_COINS:
        rows.append([
            InlineKeyboardButton(coin, callback_data=f"{command}:{coin}")
            for coin in row
        ])
    return InlineKeyboardMarkup(rows)


async def coin_picker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button taps like 'macd:BTC', 'rsi:ETH', 'price:SOL'."""
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("macd", "rsi", "price"):
        return

    cmd, coin = parts
    pair = resolve_pair(coin)
    await query.answer()

    if cmd == "macd":
        data = await fetch_klines(pair)
        if not data:
            await query.message.reply_text(f"❌ Can't fetch {pair}.")
            return
        closes = [float(c[4]) for c in data]
        price = closes[-1]
        chart = ascii_chart(closes)
        result = compute_macd(closes)
        if not result:
            await query.message.reply_text(f"❌ Not enough data for {pair}.")
            return
        if result["cross"]:
            emoji = "🟢" if result["cross"] == "BULLISH" else "🔴"
            msg = (
                f"{emoji} *{pair}* 1h MACD *{result['cross']}* cross\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📊 MACD: `{result['macd']}` | Signal: `{result['signal']}`\n"
                f"💰 Price: {fmt_price(price)}\n\n"
                f"{chart}"
            )
        else:
            bar = "▓" if result["diff"] > 0 else "░"
            msg = (
                f"⚡ *{pair}* — no cross\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📊 MACD: `{result['macd']}`\n"
                f"📈 Signal: `{result['signal']}`\n"
                f"{bar} Histogram: `{result['histogram']}`\n"
                f"💰 Price: {fmt_price(price)}\n\n"
                f"{chart}"
            )
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif cmd == "rsi":
        data = await fetch_klines(pair, interval="1h", limit=100)
        if not data:
            await query.message.reply_text(f"❌ Can't fetch {pair}.")
            return
        closes = [float(c[4]) for c in data]
        price = closes[-1]
        chart = ascii_chart(closes)
        rsi = compute_rsi(closes)
        if rsi is None:
            await query.message.reply_text(f"❌ Not enough data for {pair}.")
            return
        if rsi >= 70:
            zone = "🔴 OVERBOUGHT"
        elif rsi >= 60:
            zone = "🟡 warm"
        elif rsi >= 40:
            zone = "⚪ neutral"
        elif rsi >= 30:
            zone = "🟡 cool"
        else:
            zone = "🟢 OVERSOLD"
        bar_filled = int(rsi / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        msg = (
            f"📉 *{pair}* RSI(14) 1h\n"
            f"━━━━━━━━━━━━━━━\n"
            f"RSI: *{rsi}* — {zone}\n"
            f"`[{bar}]`\n"
            f"💰 Price: {fmt_price(price)}\n\n"
            f"{chart}"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif cmd == "price":
        ticker = await fetch_ticker(pair)
        if not ticker:
            await query.message.reply_text(f"❌ Can't fetch {pair}.")
            return
        # Fetch klines for chart
        kline_data = await fetch_klines(pair)
        chart = ascii_chart([float(c[4]) for c in kline_data]) if kline_data else ""
        price = float(ticker["lastPrice"])
        change = float(ticker["priceChangePercent"])
        high = float(ticker["highPrice"])
        low = float(ticker["lowPrice"])
        vol = float(ticker["quoteVolume"])
        emoji = trend_emoji(change)
        sign = "+" if change > 0 else ""
        vol_str = f"${vol / 1e6:.1f}M" if vol > 1e6 else f"${vol:,.0f}"
        msg = (
            f"{emoji} *{pair}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Price: {fmt_price(price)} ({sign}{change:.2f}%)\n"
            f"📈 24h High: {fmt_price(high)}\n"
            f"📉 24h Low: {fmt_price(low)}\n"
            f"📊 24h Volume: {vol_str}\n\n"
            f"{chart}"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")


# === /macd ===

async def macd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    args = context.args or []
    chat_id = str(update.effective_chat.id)

    # /macd list
    if args and args[0].lower() == "list":
        wl = load_watchlist()
        pairs = wl.get(chat_id, [])
        if not pairs:
            await update.message.reply_text("📭 No active watches.\nUse /macd watch PAIR to add one.")
        else:
            lines = "\n".join(f"  ⚡ {p}" for p in pairs)
            await update.message.reply_text(f"👁️ Active watches ({len(pairs)}/{MAX_WATCHES}):\n{lines}")
        return

    # /macd watch PAIR
    if args and args[0].lower() == "watch":
        if len(args) < 2:
            await update.message.reply_text("Usage: /macd watch BTC")
            return
        pair = resolve_pair(args[1])
        wl = load_watchlist()
        chat_pairs = wl.get(chat_id, [])
        if pair in chat_pairs:
            await update.message.reply_text(f"⚡ {pair} already watched.")
            return
        if len(chat_pairs) >= MAX_WATCHES:
            await update.message.reply_text(f"🚫 Max {MAX_WATCHES} watches. /macd stop PAIR to free a slot.")
            return
        data = await fetch_klines(pair)
        if not data:
            await update.message.reply_text(f"❌ Can't fetch {pair}. Try: BTC, ETH, SOL, etc.")
            return
        chat_pairs.append(pair)
        wl[chat_id] = chat_pairs
        save_watchlist(wl)
        await update.message.reply_text(f"👁️ Now watching {pair} hourly.\n📊 {len(chat_pairs)}/{MAX_WATCHES} slots used.")
        return

    # /macd stop PAIR
    if args and args[0].lower() == "stop":
        if len(args) < 2:
            await update.message.reply_text("Usage: /macd stop BTC")
            return
        pair = resolve_pair(args[1])
        wl = load_watchlist()
        chat_pairs = wl.get(chat_id, [])
        if pair not in chat_pairs:
            await update.message.reply_text(f"🤷 {pair} not in watchlist.")
            return
        chat_pairs.remove(pair)
        wl[chat_id] = chat_pairs
        save_watchlist(wl)
        await update.message.reply_text(f"🔕 Stopped watching {pair}.\n📊 {len(chat_pairs)}/{MAX_WATCHES} slots used.")
        return

    # /macd (no pair) → show picker
    if not args:
        await update.message.reply_text(
            "📊 *MACD* — pick a coin:",
            parse_mode="Markdown",
            reply_markup=coin_picker_keyboard("macd"),
        )
        return

    # /macd PAIR
    pair = resolve_pair(args[0])
    data = await fetch_klines(pair)
    if not data:
        await update.message.reply_text(f"❌ Can't fetch {pair}. Try: BTC, ETH, SOL, etc.")
        return

    closes = [float(c[4]) for c in data]
    price = closes[-1]
    chart = ascii_chart(closes)
    result = compute_macd(closes)

    if not result:
        await update.message.reply_text(f"❌ Not enough data for {pair}.")
        return

    if result["cross"]:
        emoji = "🟢" if result["cross"] == "BULLISH" else "🔴"
        msg = (
            f"{emoji} *{pair}* 1h MACD *{result['cross']}* cross\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 MACD: `{result['macd']}` | Signal: `{result['signal']}`\n"
            f"💰 Price: {fmt_price(price)}\n\n"
            f"{chart}"
        )
    else:
        bar = "▓" if result["diff"] > 0 else "░"
        msg = (
            f"⚡ *{pair}* — no cross\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 MACD: `{result['macd']}`\n"
            f"📈 Signal: `{result['signal']}`\n"
            f"{bar} Histogram: `{result['histogram']}`\n"
            f"💰 Price: {fmt_price(price)}\n\n"
            f"{chart}"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


# === /coins ===

async def coins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    # Group aliases by category
    categories = {
        "🟠 Major": ["BTC", "ETH", "SOL", "BNB", "XRP"],
        "🔵 L1/L2": ["AVAX", "DOT", "ATOM", "NEAR", "APT", "SUI", "SEI"],
        "⚡ DeFi/Infra": ["LINK", "ARB", "OP", "INJ", "TIA", "INIT"],
        "🐕 Meme": ["DOGE"],
        "🪙 Legacy": ["ADA", "MATIC"],
    }

    lines = ["🪙 *Supported Coins*", "━━━━━━━━━━━━━━━"]
    for cat, coins in categories.items():
        coin_str = "  ".join(f"`{c}`" for c in coins)
        lines.append(f"\n{cat}\n{coin_str}")

    lines.append(f"\n📊 {len(PAIR_ALIASES)} shortcuts total")
    lines.append("Any Binance pair also works: `PEPEUSDT`, `WIFUSDT`, etc.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# === /price ===

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    args = context.args or []

    if not args:
        await update.message.reply_text(
            "💰 *Price* — pick a coin:",
            parse_mode="Markdown",
            reply_markup=coin_picker_keyboard("price"),
        )
        return

    pair = resolve_pair(args[0])
    ticker = await fetch_ticker(pair)
    if not ticker:
        await update.message.reply_text(f"❌ Can't fetch {pair}. Try: BTC, ETH, SOL, etc.")
        return

    kline_data = await fetch_klines(pair)
    chart = ascii_chart([float(c[4]) for c in kline_data]) if kline_data else ""

    price = float(ticker["lastPrice"])
    change = float(ticker["priceChangePercent"])
    high = float(ticker["highPrice"])
    low = float(ticker["lowPrice"])
    vol = float(ticker["quoteVolume"])

    emoji = trend_emoji(change)
    sign = "+" if change > 0 else ""

    vol_str = f"${vol / 1e6:.1f}M" if vol > 1e6 else f"${vol:,.0f}"

    msg = (
        f"{emoji} *{pair}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Price: {fmt_price(price)} ({sign}{change:.2f}%)\n"
        f"📈 24h High: {fmt_price(high)}\n"
        f"📉 24h Low: {fmt_price(low)}\n"
        f"📊 24h Volume: {vol_str}\n\n"
        f"{chart}"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


# === /rsi ===

async def rsi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    args = context.args or []

    if not args:
        await update.message.reply_text(
            "📉 *RSI* — pick a coin:",
            parse_mode="Markdown",
            reply_markup=coin_picker_keyboard("rsi"),
        )
        return

    pair = resolve_pair(args[0])
    data = await fetch_klines(pair, interval="1h", limit=100)
    if not data:
        await update.message.reply_text(f"❌ Can't fetch {pair}. Try: BTC, ETH, SOL, etc.")
        return

    closes = [float(c[4]) for c in data]
    price = closes[-1]
    chart = ascii_chart(closes)
    rsi = compute_rsi(closes)

    if rsi is None:
        await update.message.reply_text(f"❌ Not enough data for {pair}.")
        return

    if rsi >= 70:
        zone = "🔴 OVERBOUGHT"
    elif rsi >= 60:
        zone = "🟡 warm"
    elif rsi >= 40:
        zone = "⚪ neutral"
    elif rsi >= 30:
        zone = "🟡 cool"
    else:
        zone = "🟢 OVERSOLD"

    bar_filled = int(rsi / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    msg = (
        f"📉 *{pair}* RSI(14) 1h\n"
        f"━━━━━━━━━━━━━━━\n"
        f"RSI: *{rsi}* — {zone}\n"
        f"`[{bar}]`\n"
        f"💰 Price: {fmt_price(price)}\n\n"
        f"{chart}"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


# === /fng ===

async def fng_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get("https://api.alternative.me/fco/fear-and-greed-index/")
            data = resp.json()["data"][0]
        except Exception:
            await update.message.reply_text("❌ Can't fetch Fear & Greed index.")
            return

    value = int(data["value"])
    label = data["value_classification"]

    if value >= 75:
        emoji = "🤑"
    elif value >= 55:
        emoji = "😏"
    elif value >= 45:
        emoji = "😐"
    elif value >= 25:
        emoji = "😰"
    else:
        emoji = "😱"

    bar_filled = int(value / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    msg = (
        f"{emoji} *Fear & Greed Index*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Score: *{value}* — {label}\n"
        f"`😱[{bar}]🤑`"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


# === /dom ===

async def dom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get("https://api.coingecko.com/api/v3/global")
            data = resp.json()["data"]
        except Exception:
            await update.message.reply_text("❌ Can't fetch dominance data.")
            return

    btc_dom = data["market_cap_percentage"]["btc"]
    eth_dom = data["market_cap_percentage"]["eth"]
    total_cap = data["total_market_cap"]["usd"]

    cap_str = f"${total_cap / 1e12:.2f}T" if total_cap > 1e12 else f"${total_cap / 1e9:.1f}B"

    msg = (
        f"🏛️ *Market Dominance*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🟠 BTC: *{btc_dom:.1f}%*\n"
        f"🔵 ETH: *{eth_dom:.1f}%*\n"
        f"🌍 Others: *{100 - btc_dom - eth_dom:.1f}%*\n"
        f"💰 Total Market Cap: {cap_str}"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


# === /daily ===

async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    args = context.args or []
    chat_id = update.effective_chat.id
    chats = load_daily_chats()

    if not args:
        status = "✅ ON" if chat_id in chats else "❌ OFF"
        await update.message.reply_text(
            f"📅 *Daily Snapshot* — {status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"  /daily on — enable\n"
            f"  /daily off — disable\n"
            f"  /daily now — send snapshot now\n\n"
            f"Fires daily at 09:00 BKK",
            parse_mode="Markdown",
        )
        return

    action = args[0].lower()

    if action == "on":
        if chat_id not in chats:
            chats.append(chat_id)
            save_daily_chats(chats)
        await update.message.reply_text("📅 Daily snapshot *enabled* for this chat.\n⏰ 09:00 BKK daily.", parse_mode="Markdown")

    elif action == "off":
        if chat_id in chats:
            chats.remove(chat_id)
            save_daily_chats(chats)
        await update.message.reply_text("🔕 Daily snapshot *disabled*.", parse_mode="Markdown")

    elif action == "now":
        await send_daily_snapshot(context, chat_id)

    else:
        await update.message.reply_text("Usage: /daily on | off | now")


async def send_daily_snapshot(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Build and send daily price snapshot for configured coins."""
    lines = [
        f"📅 *Daily Snapshot* — {datetime.utcnow().strftime('%b %d, %Y')}",
        "━━━━━━━━━━━━━━━",
    ]

    for coin in DAILY_COINS:
        pair = resolve_pair(coin)
        try:
            ticker = await fetch_ticker(pair)
            if not ticker:
                lines.append(f"\n❌ {coin} — unavailable")
                continue

            price = float(ticker["lastPrice"])
            change = float(ticker["priceChangePercent"])
            sign = "+" if change > 0 else ""
            emoji = trend_emoji(change)

            kline_data = await fetch_klines(pair)
            closes = [float(c[4]) for c in kline_data] if kline_data else []
            rsi = compute_rsi(closes) if len(closes) > 15 else None
            rsi_str = f" | RSI `{rsi}`" if rsi else ""

            lines.append(f"\n{emoji} *{coin}* — {fmt_price(price)} ({sign}{change:.1f}%){rsi_str}")

            # Compact chart for top 3 (BTC, ETH, SOL)
            if coin in ("BTC", "ETH", "SOL") and closes:
                chart = ascii_chart(closes, width=20, height=5)
                lines.append(chart)
        except Exception as e:
            log.error(f"Daily snapshot error for {coin}: {e}")
            lines.append(f"\n❌ {coin} — error")

    # Append FnG
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.alternative.me/fco/fear-and-greed-index/")
            fng_data = resp.json()["data"][0]
            fng_val = int(fng_data["value"])
            fng_label = fng_data["value_classification"]
            lines.append(f"\n🌡️ Fear & Greed: *{fng_val}* — {fng_label}")
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


async def daily_cron(context: ContextTypes.DEFAULT_TYPE):
    """Fires once daily, sends snapshot to all registered chats."""
    chats = load_daily_chats()
    for chat_id in chats:
        try:
            await send_daily_snapshot(context, chat_id)
        except Exception as e:
            log.error(f"Daily snapshot failed for {chat_id}: {e}")


# === /start ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "⚡ *Crypto Signal Bot*\n"
        "━━━━━━━━━━━━━━━\n\n"
        "📊 *Technical*\n"
        "  /macd `[PAIR]` — MACD crossover\n"
        "  /rsi `[PAIR]` — RSI overbought/oversold\n\n"
        "💰 *Price*\n"
        "  /price `[PAIR]` — price + 24h change\n"
        "  /coins — list supported coins\n\n"
        "🌡️ *Sentiment*\n"
        "  /fng — Fear & Greed index\n"
        "  /dom — BTC dominance\n\n"
        "📅 *Daily*\n"
        "  /daily on — enable daily snapshot\n"
        "  /daily off — disable\n"
        "  /daily now — send snapshot now\n\n"
        "👁️ *Alerts*\n"
        "  /macd watch `PAIR` — hourly MACD alerts\n"
        "  /macd stop `PAIR` — remove alerts\n"
        "  /macd list — show watches\n\n"
        "Pairs: BTC, ETH, SOL, etc. or BTCUSDT\n"
        "No API keys. All data from Binance + CoinGecko.",
        parse_mode="Markdown"
    )


# === Hourly cron ===

async def hourly_check(context: ContextTypes.DEFAULT_TYPE):
    wl = load_watchlist()
    for chat_id, pairs in wl.items():
        for pair in pairs:
            try:
                data = await fetch_klines(pair)
                if not data:
                    continue
                closes = [float(c[4]) for c in data]
                price = closes[-1]
                result = compute_macd(closes)
                if result and result["cross"]:
                    emoji = "🟢" if result["cross"] == "BULLISH" else "🔴"
                    msg = (
                        f"{emoji} *{pair}* 1h MACD *{result['cross']}* cross\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"📊 MACD: `{result['macd']}` | Signal: `{result['signal']}`\n"
                        f"💰 Price: {fmt_price(price)}"
                    )
                    await context.bot.send_message(
                        chat_id=int(chat_id), text=msg, parse_mode="Markdown"
                    )
            except Exception as e:
                log.error(f"Error checking {pair} for {chat_id}: {e}")


# === Post-init ===

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("macd", "📊 MACD crossover check"),
        BotCommand("price", "💰 Price + 24h change"),
        BotCommand("rsi", "📉 RSI overbought/oversold"),
        BotCommand("coins", "🪙 List supported coins"),
        BotCommand("fng", "😱 Fear & Greed index"),
        BotCommand("dom", "🏛️ BTC dominance"),
        BotCommand("daily", "📅 Daily snapshot on/off"),
        BotCommand("start", "⚡ Show all commands"),
    ])
    log.info("Bot commands registered.")


# === Main ===

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("macd", macd_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("rsi", rsi_command))
    app.add_handler(CommandHandler("coins", coins_command))
    app.add_handler(CommandHandler("fng", fng_command))
    app.add_handler(CommandHandler("dom", dom_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(coin_picker_callback))

    job_queue = app.job_queue
    job_queue.run_repeating(hourly_check, interval=3600, first=10)
    job_queue.run_daily(daily_cron, time=time(hour=DAILY_HOUR_UTC, minute=DAILY_MINUTE_UTC))

    log.info("⚡ Crypto Signal Bot starting...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
