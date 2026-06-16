# MACD Crossover Bot

Telegram bot for crypto signals, built around MACD(12,26,9) crossovers on Binance
candles and grown into a multi-indicator toolkit. No API keys needed for market data.

## Commands

- `/macd [PAIR]` — MACD(12,26,9) crossover check
- `/macd watch PAIR [15m|30m|1h|4h|1d] [up|down|both]` — add to alerts
- `/macd stop PAIR [INTERVAL]` — remove from alerts
- `/macd list` — show active watches
- `/price [PAIR]` — price + 24h change
- `/rsi [PAIR]` — RSI(14) overbought/oversold
- `/bb [PAIR]` — Bollinger Bands(20,2) position
- `/coins` — list supported coins
- `/scan [PAIR]` — multi-indicator composite verdict (`/scan all` for full market)
- `/fng` — Fear & Greed index
- `/dom` — BTC dominance
- `/kelly WIN_RATE REWARD_RISK [BANKROLL]` — Kelly Criterion position sizing
- `/daily on|off|now` — daily snapshot
- `/portfolio COIN PRICE` — add paper trade (`/portfolio` to show P&L, `/portfolio remove COIN [N]`)
- `/status` — bot health + job status
- `/start` — show all commands

## Indicators

- **MACD(12,26,9)** — crossover detection, also weighted in the composite scan
- **RSI(14)** — overbought/oversold, weighted in the composite scan
- **Bollinger Bands(20,2)** — band position, context in the composite scan
- **EMA20/50 trend** + **volume confirmation** — weighted in the composite scan
- **Stochastic Oscillator** — fast %K/%D (14,3). Context-only signal in the composite
  `/scan`: overbought when %K > 80, oversold when %K < 20, midrange otherwise. It does
  not adjust the score and there is no standalone command for it.
- **Kelly Criterion** — risk sizing helper via `/kelly`; accepts win rate as `55`,
  `55%`, or `0.55`, reward:risk as net R multiple, and optional bankroll.

## Deploy to Railway

1. Fork or push this repo to GitHub
2. Connect to Railway
3. Add env var: `TELEGRAM_BOT_TOKEN` (from @BotFather)
4. Deploy

The bot uses Binance public API — no keys needed.

## Setup

1. Create a bot via [@BotFather](https://t.me/botfather)
2. Add the bot to your group
3. Send `/macd` to test

## How it works

- Fetches 100 hourly candles from Binance
- Computes EMA(12), EMA(26), MACD line, Signal line(9)
- Detects crossover on last 2 bars
- Hourly cron checks all watched pairs, alerts only on crossover
