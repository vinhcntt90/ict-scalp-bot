# ICT Scalp Bot 🚀

Standalone ICT (Inner Circle Trader) Scalp Trading Bot cho Binance Futures.

## Quick Start

```bash
# Cài dependencies
pip install -r requirements.txt

# Chạy live (BTC + ETH)
py -3 live_scalp.py --symbols BTCUSDT,ETHUSDT

# Chạy backtest
py -3 backtest_scalp.py --days 30 --capital 350
```

## Cấu trúc

```
ict-scalp-bot/
├── live_scalp.py          # Bot chạy real-time
├── backtest_scalp.py      # Backtesting engine
├── requirements.txt
├── .env                   # API keys (gitignored)
├── config/
│   └── credentials.json   # Google Sheets key
├── src/
│   ├── config.py          # Config & API keys
│   ├── data.py            # Binance data fetcher
│   ├── ict_core.py        # ICT: Swing, Sweep, OB, FVG, ATR
│   ├── scalp_strategy.py  # Strategy: Signal, SL/TP, Exit
│   ├── notifications.py   # Telegram alerts
│   ├── testnet_trader.py  # Binance Testnet orders
│   ├── ws_client.py       # WebSocket realtime price
│   └── sheets_logger.py   # Google Sheets logging
├── data/                  # State files (auto-generated)
└── logs/                  # Daily logs
```

## Chiến lược

```
1H Bias (HH/HL/LH/LL + EMA) → 15m Sweep → VSA Volume → 5m BOS → ENTRY
```

## CLI Arguments

| Arg | Default | Mô tả |
|---|---|---|
| `--symbols` | BTCUSDT | Comma-separated: BTCUSDT,ETHUSDT,SOLUSDT |
| `--capital` | 350 | Vốn ban đầu (USD) |
| `--risk` | 5.0 | Risk % mỗi lệnh |
| `--leverage` | 50 | Đòn bẩy |
| `--testnet` | off | Bật Binance Testnet trading |
