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

# ── Що включаємо ──────────────────────────────────────────────────────────
INCLUDE_EXTENSIONS = {".py", ".md", ".env.example"}
INCLUDE_JSON = {
    "tools/antifrod_concepts.json",
    "tools/antifrod_reviews.json",
    "tools/antifrod_trade_terms.json",
}

# ── Що виключаємо ─────────────────────────────────────────────────────────
SKIP_DIRS = {
    ".venv", "__pycache__", ".git", ".idea",
    "logs", "data", "migrations",
    ".pytest_cache",
}
# Виключаємо всі папки що починаються з _backup
SKIP_DIR_PREFIXES = ("_backup",)
SKIP_FILES = {
    "dump.py",           # сам себе не включаємо
    "project_dump.txt",
    "migrate_to_v2.py",  # міграційний скрипт — не бойовий код
    "cryptobot_session.session",
    "inspector_session.session",
}

# ── Пріоритетний порядок файлів (читаються першими) ───────────────────────
PRIORITY_FILES = [
    "config/settings.py",
    "config/defaults.py",
    "config/runtime.py",
    "config/banks.py",
    "scanner.py",
    "main.py",
    "core/engine/risk_engine.py",
    "core/storage/merchant_db.py",
    "core/analysis/regex_analyzer.py",
    "core/analysis/behavioral_analyzer.py",
    "core/analysis/identity_analyzer.py",
    "core/analysis/rules.py",
    "core/workers/llm_worker.py",
    "core/workers/review_fetcher.py",
    "core/engine/cross_matcher.py",
    "core/engine/stability.py",
    "core/utils/circuit_breaker.py",
    "core/utils/cache.py",
    "core/utils/crypto.py",
    "core/utils/dedup_cache.py",
    "core/utils/fees.py",
    "bot/notifier.py",
    "bot/formatters.py",
    "bot/commands.py",
    "bot/keyboards.py",
    "exchanges/base.py",
    "exchanges/binance.py",
    "exchanges/bybit.py",
    "exchanges/okx.py",
    "exchanges/wallet.py",
    "exchanges/mexc.py",
    "exchanges/cryptobot_userbot.py",
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
    "filters/merchant_filter.py",
    "filters/bank_filter.py",
    "filters/limit_filter.py",
    "filters/anomaly_filter.py",
]


def should_skip(path: Path) -> bool:
    parts = path.parts
    if any(skip in parts for skip in SKIP_DIRS):
        return True
    # Виключаємо папки з префіксом _backup (будь-яка дата)
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

    # Спочатку пріоритетні файли в потрібному порядку
    seen = set()
    for rel in PRIORITY_FILES:
        p = ROOT / rel.replace("/", os.sep)
        if p.exists() and not should_skip(p):
            all_files.append(p)
            seen.add(p)

    # Потім решта (що не потрапила в пріоритетний список)
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
        # Заголовок
        out.write(f"# PROJECT DUMP: p2p_scanner\n")
        out.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        out.write(f"# Files: {len(files)}\n")
        out.write("#\n")
        out.write("# STRUCTURE:\n")
        for f in files:
            rel = f.relative_to(ROOT)
            out.write(f"#   {rel}\n")
        out.write("\n")

        for file_path in files:
            rel = file_path.relative_to(ROOT)
            sep = "=" * 60

            out.write(f"\n{sep}\n")
            out.write(f"FILE: {str(rel).replace(chr(92), '/')}\n")
            out.write(f"{sep}\n\n")

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
    print(f"\n✅ Готово!")
    print(f"   Файл:   project_dump.txt")
    print(f"   Розмір: {size_kb:.1f} KB")
    print(f"   Рядків: {total_lines:,}")
    print(f"\n💡 Завантаж project_dump.txt у Google AI Studio через кнопку '+' в чаті")


if __name__ == "__main__":
    main()