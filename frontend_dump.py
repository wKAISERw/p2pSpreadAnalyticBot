import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
OUTPUT = ROOT / "frontend_dump.txt"

# ── Що включаємо ──────────────────────────────────────────────────────────
INCLUDE_EXTENSIONS = {".tsx", ".ts", ".css", ".html", ".json", ".md", ".example"}

# ── Що виключаємо ─────────────────────────────────────────────────────────
SKIP_DIRS = {
    "node_modules", "dist", ".git", ".vscode",
    "build", "coverage", "public"
}
SKIP_FILES = {
    "dump_front.py",
    "frontend_dump.txt",
    "package-lock.json",
    ".gitignore",
}

# ── Пріоритетний порядок (найважливіша логіка спочатку) ────────────────────
PRIORITY_FILES = [
    "package.json",
    "vite.config.ts",
    "tsconfig.json",
    "src/types.ts",
    "src/App.tsx",
    "src/main.tsx",
    "src/services/api.ts",
    "src/components/Dashboard.tsx",
    "src/components/ApiKeysPanel.tsx",
    "src/components/AutoTradePanel.tsx",
    "src/components/SettingsPanel.tsx",
    "src/components/AnalyticsPanel.tsx.tsx",
    "src/components/CommandPalette.tsx.tsx",
    "src/components/Sidebar.tsx.tsx",
    "src/data/mock.ts",
    "src/firebase.ts",
    "src/store.ts",


]

def should_skip(path: Path) -> bool:
    parts = path.parts
    if any(skip in parts for skip in SKIP_DIRS):
        return True
    if path.name in SKIP_FILES:
        return True
    # Не беремо важкі картинки чи шрифти, якщо затесалися
    if path.suffix not in INCLUDE_EXTENSIONS:
        return True
    return False

def collect_files() -> list[Path]:
    all_files: list[Path] = []
    seen = set()

    # 1. Пріоритетні файли
    for rel in PRIORITY_FILES:
        p = ROOT / rel.replace("/", os.sep)
        if p.exists() and not should_skip(p):
            all_files.append(p)
            seen.add(p)

    # 2. Решта файлів (src, root files тощо)
    for p in sorted(ROOT.rglob("*")):
        if p.is_file() and p not in seen:
            if not should_skip(p):
                all_files.append(p)
                seen.add(p)

    return all_files

def write_dump(files: list[Path]) -> int:
    total_lines = 0
    with open(OUTPUT, "w", encoding="utf-8") as out:
        out.write(f"# PROJECT FRONTEND DUMP: Arbix Quantum\n")
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
    print("🚀 Збираю фронтенд Arbix Quantum...")
    files = collect_files()
    print(f"📦 Файлів до пакування: {len(files)}")

    lines = write_dump(files)
    size_kb = OUTPUT.stat().st_size / 1024

    print(f"\n✅ Готово!")
    print(f"   Файл:   frontend_dump.txt")
    print(f"   Розмір: {size_kb:.1f} KB")
    print(f"   Рядків: {lines:,}")
    print(f"\n💡 Тепер просто кидай цей файл в AI Studio.")

if __name__ == "__main__":
    main()