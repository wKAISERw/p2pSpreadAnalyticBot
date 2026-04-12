# Arbix Quantum - Gemini CLI Project Mandates

## Project Overview
**Arbix Quantum** is an asynchronous P2P cryptocurrency scanner built on Python. It identifies arbitrage spreads across major exchanges: **Binance, Bybit, OKX, MEXC, and Wallet**.

### Tech Stack:
- **Core:** Python 3.x (Asynchronous)
- **Networking/Concurrency:** `aiohttp`, `asyncio`
- **Bot Interface:** `Aiogram`
- **Database:** `SQLite` with **WAL (Write-Ahead Logging) mode** enabled (`aiosqlite`)
- **Intelligence:** `RiskEngine` utilizing Regex and LLM integration (**OpenAI, Gemini, Groq**) for fraud detection (anti-scam, anti-"triangle", finmonitoring).

---

## Core Mandates

1.  **Async-First Development:**
    *   ALWAYS write non-blocking, asynchronous code.
    *   Strictly avoid synchronous blocking calls (e.g., `time.sleep`, `requests`). Use `asyncio.sleep` and `aiohttp`.

2.  **Security & Privacy:**
    *   NEVER log, print, or commit API keys, secrets, or sensitive P2P trade data.
    *   Protect `.env` files and browser profile data rigorously.

3.  **Architectural Integrity (Arbix Quantum Structure):**
    *   `exchanges/`: Async exchange API wrappers inheriting from `base.py`.
    *   `core/engine/`: Logic for `ad_repricer.py`, `route_executor.py`, and `RiskEngine`.
    *   `core/analysis/`: Analyzers for behavioral, identity, and LLM-based risk assessments.
    *   `filters/`: Standardized filters for banks, limits, and merchants.

4.  **Coding Standards:**
    *   **Typing:** MANDATORY type hints for all function signatures and complex variables.
    *   **Logging:** Use the standard `logging` module for all tracking. Do not use `print()`.
    *   **Naming:** Follow PEP 8 (snake_case for functions/variables, PascalCase for classes).

5.  **Risk Management (RiskEngine):**
    *   The `RiskEngine` is the project's core differentiator. Ensure all merchant analysis logic is robust against P2P scams and financial monitoring.
    *   Maintain safety bounds and circuit breakers in all execution logic.

6.  **Testing & Validation:**
    *   ALL changes must include tests or be verified with specialized scripts (e.g., `scripts/test_p2p_trade_api.py`).
    *   Ensure database migrations are handled safely within `aiosqlite`.

7.  **Documentation:**
    *   Update `requirements.txt` for any new dependencies.
    *   Use descriptive docstrings for complex logic.

8.  **Maker-Taker Operations:**
    *   **Rate Limiting & Queueing:** All requests to `AdRepricer` and price update operations must pass through `ExchangeManager` to prevent API rate limiting (Burst limiting).
    *   **Idempotency:** All order actions (Create/Update/Cancel) must be idempotent. Use unique Client Order IDs (UUIDs) to track state in `state.py`.
    *   **Circuit Breakers:** `MakerAdMonitor` must immediately halt ad activity if `RiskEngine` detects anomalous activity or `PriceAdvisor` receives inconsistent order book data.

9.  **State & Concurrency:**
    *   **Atomic Updates:** Updates to ad states and balances in SQLite must occur only via transactions (use `async with db.execute(...)` with required commits).
    *   **Race Conditions:** Avoid direct reading/writing of order data across different workers. Use `asyncio.Queue` for inter-worker communication between `MakerAdMonitor` and `TradeWorker`.

10. **Monitoring & Alerting:**
    *   **Automated Heartbeat:** Every active Maker bot must regularly update its heartbeat in the database. If absent for >60 seconds, `Bot/Notifier` must send a "stalled maker" alert.

11. **Volatility Stop-Loss (RiskEngine):**
    *   The system must monitor abrupt spikes in average market price (or spot). If the price shifts by >X% within Y minutes, all Maker-ads must automatically transition to Offline status until market stabilization.

