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
