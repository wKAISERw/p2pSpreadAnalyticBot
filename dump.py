"""
dump.py — Збирач коду проекту для Google AI Studio / ChatGPT
Запуск: python dump.py
Результат: project_dump.txt
"""
import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
OUTPUT = ROOT / "project_dump.txt"

INCLUDE_EXTENSIONS = {".py", ".md", ".env.example"}
INCLUDE_JSON = {
    "tools/antifrod_concepts.json",
    "tools/antifrod_reviews.json",
    "tools/antifrod_trade_terms.json",
}

SKIP_DIRS = {
    ".venv", "__pycache__", ".git", ".idea",
    "logs", "data", "migrations",
    ".pytest_cache",
}
SKIP_DIR_PREFIXES = ("_backup",)
SKIP_FILES = {
    "dump.py",
    "project_dump.txt",
    "migrate_to_v2.py",
    "cryptobot_session.session",
    "inspector_session.session",
}

PRIORITY_FILES = [
    # ── Конфіг
    "config/settings.py",
    "config/defaults.py",
    "config/runtime.py",
    "config/banks.py",
    # ── Точки входу
    "scanner.py",
    "main.py",
    "state.py",
    # ── Core Engine
    "core/engine/risk_engine.py",
    "core/engine/cross_matcher.py",
    "core/engine/stability.py",
    # ── Торговий двигун
    "core/engine/trade_worker.py",
    "core/engine/route_executor.py",
    "core/engine/single_leg_executor.py",
    "core/engine/order_monitor.py",
    "core/engine/ad_repricer.py",
    "core/engine/network_fee_engine.py",
    "core/engine/exchange_manager.py",
    "core/engine/startup.py",
    # ── Storage
    "core/storage/merchant_db.py",
    # ── Analysis
    "core/analysis/regex_analyzer.py",
    "core/analysis/behavioral_analyzer.py",
    "core/analysis/identity_analyzer.py",
    "core/analysis/review_analyzer.py",
    "core/analysis/rules.py",
    # ── Analytics
    "core/analytics/merchant_profile.py",
    "core/analytics/stats_engine.py",
    # ── Workers
    "core/workers/llm_worker.py",
    "core/workers/review_fetcher.py",
    "core/workers/session_manager.py",
    # ── Utils
    "core/utils/circuit_breaker.py",
    "core/utils/cache.py",
    "core/utils/crypto.py",
    "core/utils/dedup_cache.py",
    "core/utils/fees.py",
    "core/utils/rate_limiter.py",
    # ── Bot
    "bot/notifier.py",
    "bot/formatters.py",
    "bot/commands.py",
    "bot/keyboards.py",
    # ── Exchanges
    "exchanges/base.py",
    "exchanges/binance.py",
    "exchanges/bybit.py",
    "exchanges/okx.py",
    "exchanges/wallet.py",
    "exchanges/mexc.py",
    "exchanges/cryptobot_userbot.py",
    # ── Infrastructure
    "infrastructure/http/base_client.py",
    "infrastructure/http/binance_client.py",
    "infrastructure/http/bybit_p2p_client.py",
    "infrastructure/http/okx_client.py",
    "infrastructure/http/mexc_client.py",
    "infrastructure/http/wallet_client.py",
    "infrastructure/api/mexc_account.py",
    "infrastructure/api/binance_account.py",
    "infrastructure/api/bybit_account.py",
    "infrastructure/api/okx_account.py",
    # ── Filters
    "filters/merchant_filter.py",
    "filters/bank_filter.py",
    "filters/limit_filter.py",
    "filters/anomaly_filter.py",
    # ── Scripts / Tools
    "script/session_interceptor.py",
    "scripts/test_p2p_trade_api.py",
    "scripts/test_trade_worker.py",
    "scripts/test_ad_repricer.py",
    "scripts/test_session.py",
    "tools/migrate_multiuser.py",
    "tools/cleanup_settings.py",
]


def should_skip(path: Path) -> bool:
    parts = path.parts
    if any(skip in parts for skip in SKIP_DIRS):
        return True
    if any(part.startswith(SKIP_DIR_PREFIXES) for part in parts):
        return True
    if path.name in SKIP_FILES:
        return True
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    if path.suffix == ".json" and rel not in INCLUDE_JSON:
        return True
    return False


def collect_files() -> list[Path]:
    all_files: list[Path] = []
    seen = set()

    for rel in PRIORITY_FILES:
        p = ROOT / rel.replace("/", os.sep)
        if p.exists() and not should_skip(p):
            all_files.append(p)
            seen.add(p)

    for p in sorted(ROOT.rglob("*")):
        if p.is_file() and p not in seen:
            if should_skip(p):
                continue
            if p.suffix in INCLUDE_EXTENSIONS:
                all_files.append(p)
                seen.add(p)
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            if rel in INCLUDE_JSON:
                all_files.append(p)
                seen.add(p)

    return all_files


def write_dump(files: list[Path]) -> int:
    total_lines = 0
    with open(OUTPUT, "w", encoding="utf-8") as out:
        out.write(f"# PROJECT DUMP: p2p_scanner\n")
        out.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        out.write(f"# Files: {len(files)}\n#\n# STRUCTURE:\n")
        for f in files:
            out.write(f"#   {f.relative_to(ROOT)}\n")
        out.write("\n")

        for file_path in files:
            rel = file_path.relative_to(ROOT)
            sep = "=" * 60
            out.write(f"\n{sep}\nFILE: {str(rel).replace(chr(92), '/')}\n{sep}\n\n")
            try:
                content = file_path.read_text(encoding="utf-8")
                out.write(content)
                total_lines += content.count("\n")
                if not content.endswith("\n"):
                    out.write("\n")
            except Exception as e:
                out.write(f"# ERROR reading file: {e}\n")

    return total_lines


def main():
    print("🔍 Збираю файли проекту...")
    files = collect_files()
    print(f"📄 Знайдено файлів: {len(files)}")
    for f in files:
        size = f.stat().st_size
        print(f"   {'✅' if size > 0 else '⚠️ EMPTY'} {f.relative_to(ROOT)}")

    print(f"\n📦 Записую дамп...")
    total_lines = write_dump(files)
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"\n✅ Готово!\n   Файл:   project_dump.txt\n   Розмір: {size_kb:.1f} KB\n   Рядків: {total_lines:,}")
    print(f"\n💡 Завантаж project_dump.txt у Google AI Studio через кнопку '+' в чаті")


if __name__ == "__main__":
    main()