# FATB — Financial Advisor Trading Bot (Hermes Agent)

> **Autonomous forex & crypto signal engine powered by Google Antigravity / Gemini**

---

## Overview

FATB (**F**inancial **A**dvisor **T**rading **B**ot) is a modular, autonomous trading-signal framework. The **Hermes Agent** data layer fetches live market data, computes technical indicators, and emits structured JSON signals that can be consumed by downstream execution agents or dashboards.

```
hermes-trading-bot/
├── config/
│   └── trading_pairs.json      # Asset universe — crypto & forex pairs
├── tools/
│   ├── data/
│   │   ├── crypto_data.py      # CCXT-based crypto OHLCV + indicators
│   │   └── forex_data.py       # yfinance-based forex OHLCV + indicators
│   └── execution/              # (reserved) Order execution & risk management
├── storage/                    # Persistence layer (signals, logs)
├── requirements.txt
└── README.md
```

---

## Setup

### Prerequisites
- Python 3.10+
- pip

### Install dependencies

```bash
pip install -r requirements.txt
```

---

## Usage

### Crypto Data Module

Fetches OHLCV candles from **Binance** (KuCoin fallback) via `ccxt` — **no API key required**.

```bash
# Default 1h timeframe
python3 tools/data/crypto_data.py BTC/USDT

# Custom timeframe
python3 tools/data/crypto_data.py ETH/USDT --timeframe 4h
python3 tools/data/crypto_data.py SOL/USDT --timeframe 15m
```

**Output schema:**
```json
{
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "price": 67432.10,
  "volume_24h": 142893.4512,
  "rsi_14": 54.32,
  "sma_20": 66100.45,
  "sma_50": 64200.88,
  "bollinger_upper": 69500.12,
  "bollinger_lower": 62700.78,
  "signal": "BULLISH",
  "timestamp": "2026-08-08T12:00:00+00:00"
}
```

**Signal logic:**
| Signal | Condition |
|--------|-----------|
| `BULLISH` | RSI < 70 AND price > SMA-20 AND price > SMA-50 AND price < BB-upper |
| `BEARISH` | RSI > 70 OR price < BB-lower OR (SMA-20 < SMA-50 AND price < SMA-20) |
| `NEUTRAL` | All other conditions |

---

### Forex Data Module

Fetches OHLCV price history from **Yahoo Finance** via `yfinance`. Auto-appends `=X` suffix.

```bash
# Auto-appends =X
python3 tools/data/forex_data.py EURUSD

# Or with explicit suffix
python3 tools/data/forex_data.py USDZAR=X

# Custom history window
python3 tools/data/forex_data.py GBPUSD --period 90d
```

**Output schema:**
```json
{
  "pair": "EUR/USD",
  "ticker": "EURUSD=X",
  "bid_ask_close": 1.08412,
  "change_pct": 0.1234,
  "rsi_14": 47.81,
  "sma_20": 1.08100,
  "sma_50": 1.07550,
  "atr_14": 0.00412,
  "signal": "BULLISH",
  "timestamp": "2026-08-08T12:00:00+00:00"
}
```

**Signal logic:**
| Signal | Condition |
|--------|-----------|
| `BULLISH` | RSI < 65 AND SMA-20 > SMA-50 AND price > SMA-20 |
| `BEARISH` | RSI > 65 OR (SMA-20 < SMA-50 AND price < SMA-20) |
| `NEUTRAL` | All other conditions |

---

## Supported Assets

### Crypto (Binance / KuCoin)
| Symbol | Exchange |
|--------|----------|
| BTC/USDT | Binance |
| ETH/USDT | Binance |
| SOL/USDT | Binance |

### Forex (Yahoo Finance)
| Ticker | Pair |
|--------|------|
| EURUSD=X | EUR/USD |
| GBPUSD=X | GBP/USD |
| USDJPY=X | USD/JPY |
| USDZAR=X | USD/ZAR |

---

## Technical Indicators

| Indicator | Period | Module |
|-----------|--------|--------|
| RSI | 14 | Both |
| SMA | 20, 50 | Both |
| Bollinger Bands | 20, ±2σ | Crypto |
| ATR | 14 | Forex |

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              Hermes Agent (FATB)            │
├───────────────────┬─────────────────────────┤
│  tools/data/      │  tools/execution/        │
│  ┌─────────────┐  │  ┌───────────────────┐  │
│  │crypto_data  │  │  │ Order Execution   │  │
│  │  + ccxt     │  │  │ (future)          │  │
│  └─────────────┘  │  └───────────────────┘  │
│  ┌─────────────┐  │                         │
│  │forex_data   │  │                         │
│  │  + yfinance │  │                         │
│  └─────────────┘  │                         │
└───────────────────┴─────────────────────────┘
         │                      │
         ▼                      ▼
   JSON Signal Output     storage/ (logs)
```

---

## Roadmap
- [ ] `tools/execution/` — Live order execution (Binance, OANDA)
- [ ] Risk management & position sizing
- [ ] Telegram / Slack signal alerts
- [ ] Web dashboard (real-time signal feed)
- [ ] Backtesting engine

---

## License
MIT — Built with ❤️ on Google Antigravity
