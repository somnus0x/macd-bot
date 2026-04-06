# MACD Crossover Bot

Telegram bot that detects MACD(12,26,9) crossovers on 1h candles from Binance.

## Commands

- `/macd` — check ETHUSDT now
- `/macd BTCUSDT` — check specific pair
- `/macd watch BTCUSDT` — add to hourly alerts
- `/macd stop BTCUSDT` — remove from alerts
- `/macd list` — show active watches

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
