"""
Контракт налаштувань.

Тут ловиться цілий клас мовчазних дефектів: код читає `getattr(settings,
"щось", дефолт)`, поля з таким іменем у Settings немає, і значення з .env
просто не діє. Виглядає це найгірше з можливого — конфіг ніби є, ніхто не
скаржиться, а поведінка завжди дефолтна.

Саме так роками не працювали STABILITY_REQUIRED_HITS і STABILITY_TTL_SECONDS:
scanner.py питав `stability_hits` і `stability_ttl`, а Settings оголошував
`stability_required_hits` і `stability_ttl_seconds`.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from config import settings

ROOT = Path(__file__).resolve().parent.parent

# Каталоги бойового коду. Тести й бекапи не чіпаємо: там getattr на
# неіснуючі поля буває навмисним.
SOURCE_DIRS = ("core", "bot", "api", "exchanges", "infrastructure", "filters", "config")
SOURCE_FILES = ("scanner.py", "main.py", "state.py")

# Поля, яких у Settings свідомо немає: їх кладуть у settings ззовні або
# читають із дефолтом як необов'язкові.
KNOWN_DYNAMIC: set[str] = set()


def _python_files():
    for name in SOURCE_FILES:
        path = ROOT / name
        if path.exists():
            yield path
    for directory in SOURCE_DIRS:
        base = ROOT / directory
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" not in str(path):
                yield path


class TestSettingsAreActuallyReadable(unittest.TestCase):
    def test_every_getattr_names_a_real_field(self):
        """
        `getattr(settings, "x", default)` має влучати в наявне поле.

        Інакше значення з .env мовчки ігнорується: pydantic не знає про
        поле, getattr не знаходить атрибута і повертає дефолт із коду.
        """
        pattern = re.compile(r'getattr\(\s*settings\s*,\s*["\']([a-z_][a-z0-9_]*)["\']')

        missing: dict[str, list[str]] = {}
        for path in _python_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name in pattern.findall(text):
                if name in KNOWN_DYNAMIC or hasattr(settings, name):
                    continue
                missing.setdefault(name, []).append(str(path.relative_to(ROOT)))

        self.assertEqual(
            missing, {},
            "getattr читає поля, яких немає в Settings — значення з .env не подіє:\n"
            + "\n".join(f"  {n}: {', '.join(files)}" for n, files in sorted(missing.items())),
        )

    def test_stability_filter_reads_its_settings(self):
        # Регресія на конкретний випадок, з якого почався цей тест.
        source = (ROOT / "scanner.py").read_text(encoding="utf-8")
        self.assertIn("settings.stability_required_hits", source)
        self.assertIn("settings.stability_ttl_seconds", source)
        self.assertNotIn('getattr(settings, "stability_hits"', source)
        self.assertNotIn('getattr(settings, "stability_ttl"', source)


if __name__ == "__main__":
    unittest.main()
