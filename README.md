# Arbix Quantum 🚀

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-Aiogram%203.x-orange.svg)](https://docs.aiogram.dev/)
[![Database](https://img.shields.io/badge/database-SQLite%20WAL-lightgrey.svg)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Arbix Quantum** is a high-performance, asynchronous P2P cryptocurrency arbitrage scanner and automation bot. It is designed to identify profitable spreads (both cross-exchange and intra-exchange) across leading P2P platforms, featuring deep LLM integration for anti-fraud risk assessment, smart bank card/limit management, and automated order execution.

---

## 🛠 Tech Stack

* **Core Language:** Python 3.x (fully asynchronous architecture built on `asyncio`).
* **Networking:** `aiohttp` for non-blocking HTTP requests to exchange APIs and webhooks.
* **Telegram Interface:** `Aiogram` (v3.x) with interactive menus, FSM state handling, and a custom UI.
* **Database:** `SQLite` with **WAL (Write-Ahead Logging)** mode enabled via `aiosqlite` for high concurrency.
* **AI & Anti-Scam Intelligence:** Integration with LLMs (**Gemini, OpenAI, Groq**) for fraud prevention, text sentiment analysis, and anti-triangle verification.
* **API & Monitoring:** `FastAPI` with Prometheus metric export (`/metrics`) and bank webhook endpoints.
* **Developer Utilities:** `PyQt6` for interactive desktop utilities.

---

## 📱 Core Features

### 1. Multi-Exchange P2P Scanning
* Simultaneous parallel scanning of order books on **Binance, Bybit, OKX, MEXC, and Telegram Wallet** (via userbot).
* **Circuit Breaker Pattern:** Automatically isolates failing or rate-limiting exchange APIs to prevent lags in the scanning cycle.
* **Spread Stability Filter:** Suppresses phantom orders; alerts are only sent if a spread persists across multiple consecutive scans.

### 2. RiskEngine & Fraud Detection (Anti-Scam)
The `RiskEngine` is the core defensive layer of Arbix Quantum, shielding operations from triangular scams, chargebacks, money laundering, and financial monitoring:
* **CompositeScorer:** Evaluates risk factors and aggregates them into a unified hazard score (0 to 100).
* **Analyzers:**
  1. **Regex Analyzer:** Scans merchant trade terms for suspicious words (e.g., card photo requests, external chat links, delay warnings).
  2. **Behavioral Analyzer:** Detects bot activity in the order book by tracking instant order replenishment (`API_REPLENISH`), static limit behaviors, and rapid ad relisting (`Flicker-relist`).
  3. **Identity Analyzer:** Matches clone profiles across different exchanges by examining identical nicknames and behavior.
  4. **Review Analyzer:** Categorizes negative merchant reviews into fraud types (`TRIANGLE`, `CHARGEBACK`, `FINCRIME`, `CASINO`) and extracts specific complaints.
* **LLM Verification:** If a merchant triggers warning combinations, the system sends the full context to a language model (Gemini, OpenAI, or Groq) to get an automated verdict (`OK`, `SUSPICIOUS`, or `BLOCK`).

### 3. Automated Order Execution & AdRepricer
* Supports four trading strategies:
  * **Taker-to-Taker (TT):** Instant buy as taker on one exchange, instant sell as taker on another.
  * **Taker-to-Maker (TM):** Quick entry via a taker order, exit via a custom maker ad.
  * **Maker-to-Taker (MT):** Buy via a maker ad, sell instantly via a taker order.
  * **Maker-to-Maker (MM):** Operating completely as a maker on both ends of the spread.
* **AdRepricer:** Automatically monitors the order book and shifts your maker ad pricing on Binance/Bybit to retain the top position while respecting a hardcoded price floor.
* **NetworkFeeEngine:** Dynamically calculates the cheapest withdrawal network (TRC20, BEP20, ERC20, TON) between exchanges and deducts the network fee from the expected spread profit.

### 4. Smart Bank Card Management (`/cards`)
* **Add Cards via FSM:** Step-by-step master to input 16-digit card numbers (Luhn checked), set starting balances, and drop labels (e.g., "Ivan Drop").
* **Monobank API Integration:** Automatically fetches real-time balances using secure `X-Token` headers and registers webhooks for instant transaction tracking.
* **Order Splitting:**
  * **Internal Split:** Breaks large orders down to fit a card's single transaction limit (`max_single_tx`).
  * **Multi-Card Split:** Distributes a single large order across up to three active bank cards simultaneously.
* **Card Diagnostics:** Scans cards for potential issues: zero balances, cooldown limits, or cards utilizing over 80% of their daily limits.

### 5. Defensive Safeguards
* **Volatility Stop-Loss:** Automatically takes all Maker ads offline if spot or average market prices change by more than $X\%$ in $Y$ minutes.
* **Idempotency Protection:** Assigns unique Client Order IDs (UUIDs) to all orders, preventing double-execution of API commands.
* **Automated Heartbeats:** Monitors active Maker instances; triggers Telegram alerts if any worker stalls for more than 60 seconds.
* **Dry Run Mode:** Allows full execution simulation on mock data without sending real orders or money to the exchanges.

---

## 📂 Project Structure

```
├── api/                  # FastAPI routers, schemas, and metrics endpoint
├── bot/                  # Telegram Bot (Aiogram handlers, keyboards, formatters)
│   ├── handlers/         # Message handlers (cards, trading, settings, etc.)
│   └── keyboards/        # Inline keyboard builders
├── config/               # Default settings, banks list, and environment configurations
├── core/
│   ├── analysis/         # Risk analysis modules (Regex, Behavioral, Review, LLM)
│   ├── analytics/        # Merchant profiles, stats engine
│   ├── engine/           # Main logic (RiskEngine, AdRepricer, RouteExecutor, TakerScanner)
│   ├── security/         # Crypto utilities for encrypting/decrypting sensitive keys
│   └── storage/          # Database repositories (aiosqlite SQLite WAL layers)
├── exchanges/            # Exchange API wrappers (Binance, Bybit, OKX, MEXC, Wallet)
├── filters/              # Fast pre-filters (Limits, Anomalies, Merchants)
├── infrastructure/       # HTTP Clients for exchanges and Monobank
├── main.py               # Main application entry point
├── scanner.py            # Main scanner background loop
├── state.py              # Application state and shared variables
└── requirements.txt      # Python dependencies
```

---

## 🚀 Getting Started

### Prerequisites
* Python 3.8 or higher
* SQLite3

### 1. Installation
Clone the repository and install the dependencies:
```bash
pip install -r requirements.txt
```

### 2. Configuration
Create a `.env` file in the root directory based on the `.env.example` file:
```bash
cp .env.example .env
```
Fill in the configuration details in `.env`:
* **TELEGRAM_BOT_TOKEN:** Your Telegram bot token (from BotFather).
* **EXCHANGE CONFIG:** API keys, secrets, and optional passphrases.
* **LLM CONFIG:** OpenAI, Gemini, or Groq API keys if LLM risk verification is enabled.
* **SECURITY_SECRET_KEY:** AES-256 key for local encryption of API keys stored in SQLite database.

### 3. Run the Application
Start the main runner to initialize databases, web servers, and the Telegram bot:
```bash
python main.py
```

To run tests:
```bash
pytest
```

---

## 🔒 Security & Best Practices

1. **Local Database Encryption:** All exchange API keys, secrets, and Monobank tokens stored in the SQLite database are encrypted using AES-256 with a unique local key (`SECURITY_SECRET_KEY`).
2. **Never Commit Secrets:** The `.env` file, database files, and JSON credentials are explicitly excluded via `.gitignore`.
3. **Sandbox Testing:** When developing new strategies, always run the scanner in Dry Run mode to prevent accidental live execution.

---

## 🇺🇦 Коротко про проект (Ukrainian Summary)

**Arbix Quantum** — це асинхронний P2P-сканер та автоматизований помічник для криптовалютного арбітражу. 
* **Сканування P2P:** знаходження крос- та внутрішньобіржових спредів на Binance, Bybit, OKX, MEXC, Telegram Wallet.
* **RiskEngine:** аналіз контрагентів на предмет шахрайства за допомогою Regex-фільтрів, поведінкових бот-сигналів, аналізу негативних відгуків та інтеграції з ШІ (Gemini/OpenAI/Groq).
* **Автоматизація:** розумне сплітування лімітів карток, авто-репрайсинг (AdRepricer), врахування мережевих комісій (NetworkFeeEngine) та Volatility Stop-Loss.
* **Безпека:** локальне шифрування API-ключів та Monobank токенів у базі даних SQLite (режим WAL).
