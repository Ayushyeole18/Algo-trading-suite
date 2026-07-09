# 📈 Algo-Trading & Market Scanner Suite (Zerodha Kite Connect)

A collection of Python-based automated trading, scanning, and monitoring bots built on the **Zerodha Kite Connect API**, with real-time alerts delivered via **Telegram Bots**. The suite covers everything from F&O scanning and Nifty/Midcap/Smallcap analysis to a fully automated intraday Bollinger Band + RSI strategy and an OTM option-selling bot for extreme divergence setups.

> ⚠️ **Disclaimer:** This project is for educational and personal-use purposes only. Trading in the stock market and derivatives carries substantial risk of loss. Nothing in this repository constitutes financial advice. Use at your own risk, and always test with a paper/sandbox account before deploying real capital.

---

## 🗂️ Project Structure

```
├── 01_EQFNO.py                # Equity + F&O options monitor with Telegram bot control
├── 02_Scanner.py               # Multi-factor stock scanner (pivots, BB, max pain, fundamentals, AI analysis)
├── 03_Ni50_M150_Nx50.py        # Nifty50 / Midcap150 / Nextcap50 universe scanner with news sentiment
├── 52_OTM_Selling.py           # Extreme divergence OTM option-selling strategy engine
├── .env.example                 # Environment variable template (no credentials included)
└── README.md
```

---

## 🚀 Features

### 1. `01_EQFNO.py` — Equity & F&O Options Monitor
- Auto-authenticates with Zerodha (Kite Connect) and refreshes access tokens daily.
- Scans NFO instruments and builds a live F&O options map for instant lookups.
- Telegram bot integration for remote control — set bullish/bearish thresholds on the fly.
- Broadcasts alerts to multiple Telegram chat IDs simultaneously.

### 2. `02_Scanner.py` — Multi-Factor Market Scanner
- Calculates **Fibonacci Pivot Points**, **Bollinger Bands (25-period)**, and LTP positioning relative to both (daily + weekly).
- Computes **Max Pain** for F&O stocks directly from the options chain.
- Pulls **fundamental data** — Market Cap, Face Value, Sales Growth, Debt-to-Equity, Dividend Yield, and Net Profit trends — via Screener.in parsing.
- Tracks **Shareholding Pattern**: FII, DII, and Promoter holding % with quarter-on-quarter change.
- Computes **50/200-period Daily & Weekly EMA (DEMA/WEMA)** with visual position bars showing where LTP sits relative to both.
- Visual **52-week range bar** showing LTP's position between the 52W low and high.
- Fetches recent **news headlines** via Google News RSS.
- Integrates **Google Gemini AI** for automated technical/fundamental commentary.
- Generates simple **statistical price projections** (1M / 6M / 1Y) blending historical drift with 200-EMA mean reversion — labelled "Hybrid ML" but intended as an illustrative estimate, not a validated predictive model.
- On-demand stock analysis triggered directly through Telegram commands.

### 3. `03_Ni50_M150_Nx50.py` — Index Universe Scanner
- Covers Nifty 50, Midcap 150, and Nextcap 50 stock universes.
- Combines pivot/BB technical analysis with live news sentiment per stock.
- Sends consolidated reports to multiple Telegram recipients.

### 4. `52_OTM_Selling.py` — Extreme Divergence OTM Option Selling Bot
- Dynamically builds the entire F&O universe from Kite's instrument master.
- Pre-scans stocks for large price moves, then evaluates **Max Pain divergence** and **Bollinger Band divergence** to identify option-selling opportunities.
- Filters OTM strikes by **liquidity, bid-ask spread, and minimum premium**.
- Config-driven (thresholds, timeframes, rate limits) via a single `Config` class.
- Built-in rate-limiting to respect Kite's API limits (3 requests/sec).
- Robust exception handling for Kite's `NetworkException`, `TokenException`, and `DataException`.

---

## 🛠️ Tech Stack

| Category | Tools / Libraries |
|---|---|
| Broker API | [Zerodha Kite Connect](https://kite.trade/) (`kiteconnect`) |
| Language | Python 3.x |
| Data Handling | `pandas`, `pandas_ta` |
| Notifications | Telegram Bot API |
| AI Integration | Google Gemini API (`google-genai`) |
| Config Management | `python-dotenv` |
| Networking | `requests` |

---

## ⚙️ Setup & Installation

### 1. Clone the repository
```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd <your-repo-name>
```

### 2. Create a virtual environment (recommended)
```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install kiteconnect pandas pandas_ta python-dotenv requests google-genai python-dateutil
```
*(Consider freezing these into a `requirements.txt` — see note below.)*

### 4. Configure environment variables
Copy the template and fill in your own credentials — **never commit your real `.env` file**:
```bash
cp .env.example .env
```

Then edit `.env` with your actual Zerodha API key/secret, Telegram bot tokens, and chat IDs (see structure below).

### 5. Run any script
```bash
python 02_Scanner__1_.py
```
On first run, you'll be prompted to complete the Zerodha login flow (paste the redirect URL); the access token is then cached in `.env` for the trading day.

---

## 🔐 Environment Variables (`.env.example`)

```env
ZERODHA_API_KEY=
ZERODHA_API_SECRET=
ZERODHA_USER_ID=
ZERODHA_PASSWORD=
ZERODHA_TOTP_SECRET=
ZERODHA_ACCESS_TOKEN=

GG_API_KEY=

# Telegram Bots (one token per script)
01_TELEGRAM_BOT_TOKEN=
02_TELEGRAM_BOT_TOKEN=
03_TELEGRAM_BOT_TOKEN=

# Telegram Chat IDs (recipients for alerts)
TELEGRAM_CHAT_IDS_01=
TELEGRAM_CHAT_IDS_02=
...
```

> No real credentials are included in this repository. Populate your own `.env` locally, and ensure it is listed in `.gitignore`.

---

## 📲 Sample Output — Telegram Alerts

Below are sample screenshots of live alerts generated by these bots in production:

<!-- Add your screenshots here, e.g.: -->
<!-- ![Scanner Alert](screenshots/scanner_alert.png) -->
<!-- ![Trade Execution Alert](screenshots/trade_execution.png) -->

*(Screenshots showcase real-time scanner output, technical alerts, and trade execution notifications sent via Telegram.)*

---

## 📌 Notes

- All scripts use `python-dotenv` for secure credential management — no hardcoded secrets.
- Designed for headless/server deployment with persistent logging (`.log` files).
- Modular structure allows each bot to run independently or be scheduled via cron / a process manager (e.g. `pm2`, `systemd`, or Windows Task Scheduler).

## 👤 Author

Built and maintained as a personal algorithmic trading research project.

## 📄 License

This project is shared for demonstration/portfolio purposes. Add a license (e.g. MIT) if you intend to open-source it fully, or mark it "All Rights Reserved" if it's for showcasing only.
