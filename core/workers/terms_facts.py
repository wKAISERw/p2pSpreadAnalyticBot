# core/workers/terms_facts.py
"""
Вижимка умов як перелік фактів із доказами — замість вільного абзацу.

Скарга, з якої це почалось: «аі вижимка трішки упускає важливі деталі в
умовах». Причина структурна, а не в моделі. Коли просиш «коротко опиши
умови двома реченнями», модель мусить обирати, що викинути, — і викидає те,
що вважає другорядним. Мерчант написав п'ять вимог, у вижимку влізло дві.

Формат, який не дає згорнути п'ять пунктів в один: перелік, де кожен пункт
несе **цитату з оригіналу**. Це водночас і найдешевша перевірка на вигадку —
цитату можна звірити з текстом механічно, без жодної моделі.

Друга скарга звідти ж: «кидаю на монобанку і конверт приват» переказано як
«кидає на монобанк і приватбанк». Проти цього працює глосарій у промпті
(див. `core/risk/vocabulary.py`), а тут — вимога цитувати: щоб написати
«приватбанк», модель мусила б процитувати слово, якого в тексті немає.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("TermsFacts")

MAX_FACTS = 8
MAX_LEN = 200

# Скільки символів цитати мають збігтися, щоб вважати її справжньою.
# Коротші фрагменти («на», «15») трапляються в будь-якому тексті випадково.
MIN_QUOTE_LEN = 6

_SPACES = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Порівнюємо без урахування регістру й розкладки пробілів."""
    return _SPACES.sub(" ", (text or "").lower()).strip()


@dataclass(frozen=True, slots=True)
class TermsFact:
    """Один факт про умови: про що, дослівна цитата, що це означає."""
    topic: str
    quote: str
    meaning: str
    verified: bool = False

    def as_dict(self) -> dict:
        return {
            "topic": self.topic, "quote": self.quote,
            "meaning": self.meaning, "verified": self.verified,
        }

    def render(self) -> str:
        mark = "" if self.verified else " ⚠️"
        line = f"• {self.topic}: {self.meaning}{mark}"
        if self.quote:
            line += f"\n  «{self.quote}»"
        return line


def parse_facts(raw, source_terms: str) -> list[TermsFact]:
    """
    Розбирає перелік фактів із відповіді моделі й звіряє цитати з оригіналом.

    Непідтверджена цитата не викидається, а позначається. Викидати було б
    самовпевнено: модель могла перефразувати відмінок або зняти емодзі, і
    факт лишається слушним. Але й мовчати не можна — саме тут живуть
    вигадані подробиці, а людина має бачити, чому саме цьому пункту вірити
    менше.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []

    haystack = _norm(source_terms)
    out: list[TermsFact] = []
    for item in raw[:MAX_FACTS]:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()[:60]
        quote = str(item.get("quote") or "").strip()[:MAX_LEN]
        meaning = str(item.get("meaning") or "").strip()[:MAX_LEN]
        if not topic and not meaning:
            continue

        needle = _norm(quote)
        verified = bool(
            needle and len(needle) >= MIN_QUOTE_LEN and haystack and needle in haystack
        )
        if quote and not verified:
            logger.debug("Цитата не знайдена в умовах: %r", quote[:80])
        out.append(TermsFact(topic or "Умова", quote, meaning, verified))
    return out


def to_json(facts: list[TermsFact]) -> str:
    return json.dumps([f.as_dict() for f in facts], ensure_ascii=False)


def from_json(raw: str) -> list[TermsFact]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [
        TermsFact(
            topic=str(d.get("topic", "")), quote=str(d.get("quote", "")),
            meaning=str(d.get("meaning", "")), verified=bool(d.get("verified")),
        )
        for d in data if isinstance(d, dict)
    ]


def to_summary(facts: list[TermsFact]) -> str:
    """
    Один рядок для тих, хто чекає старий `terms_summary`.

    Дашборд — окремий репозиторій, і міняти під ним формат поля на льоту
    означало б зламати його мовчки. Тому структура їде в нову колонку, а
    сюди кладемо звичний текст, зібраний із неї.
    """
    if not facts:
        return ""
    parts = [f"{f.topic}: {f.meaning}" if f.meaning else f.topic for f in facts]
    return "; ".join(parts)[:300]


def render_block(facts: list[TermsFact]) -> str:
    """Перелік для алерта. Порожній рядок, якщо фактів немає."""
    if not facts:
        return ""
    lines = [f.render() for f in facts]
    unverified = sum(1 for f in facts if not f.verified)
    if unverified:
        lines.append(
            f"⚠️ {unverified} з {len(facts)} — цитату не знайдено в умовах дослівно"
        )
    return "\n".join(lines)
