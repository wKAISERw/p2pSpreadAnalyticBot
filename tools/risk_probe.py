#!/usr/bin/env python3
# tools/risk_probe.py
"""
Проба ріск-енджину на РЕАЛЬНИХ даних.

Навіщо. Кожна зміна в сигналах міняє те, що бот скаже про мерчантів, — але
побачити це на очі нема де: тести перевіряють десяток вигаданих рядків, а
бойові тексти лежать у базі й ніхто на них не дивиться. Цей модуль дає
подивитись, і саме на тому, що справді приходить із бірж.

Читає тільки. Нічого не пише, нікуди не ходить по мережі.

    python tools/risk_probe.py                    # повний звіт
    python tools/risk_probe.py --signals          # які сигнали спрацьовують
    python tools/risk_probe.py --silent           # чого движок НЕ бачить
    python tools/risk_probe.py --negations        # що заглушили заперечення
    python tools/risk_probe.py --drift            # розбіжність із тим, що в базі
    python tools/risk_probe.py --verdicts         # розподіл вердиктів у базі
    python tools/risk_probe.py --text "кидаю на монобанку"   # розбір одного тексту

Порядок роботи після зміни сигналів:

    1. `--drift`   — що саме змінилось проти збереженого;
    2. `--silent`  — чи не осліпли на чомусь важливому;
    3. `--negations` — чи не глушимо зайвого;
    4. `--signals` — чи не з'явився сигнал, що ловить усе підряд.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.risk.matcher import match_text, normalize          # noqa: E402
from core.risk.registry import CATEGORY_TITLES, builtin_registry  # noqa: E402
from core.risk.signals import (                              # noqa: E402
    LAYER_HARD, LAYER_SAFE, LAYER_SOFT, LAYER_WARN,
    SCOPE_REVIEWS, SCOPE_TERMS,
)

DB_PATH = ROOT / "data" / "merchants.db"

BAR = "─" * 78


def _head(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


def _short(text: str, width: int = 88) -> str:
    one = " ".join(str(text).split())
    return one[:width] + ("…" if len(one) > width else "")


# ─────────────────────────────────────────────────────────────────────────────
# Джерела живого тексту
# ─────────────────────────────────────────────────────────────────────────────

def load_terms(db: sqlite3.Connection) -> list[str]:
    """
    Умови мерчантів. Єдине місце, де лежить сирий текст, — серіалізовані
    алерти: `merchant_verdict` зберігає лише хеш умов, не самі умови.
    """
    out: set[str] = set()
    try:
        rows = db.execute("SELECT alert_json FROM sent_alerts").fetchall()
    except sqlite3.Error:
        return []
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for key in ("buy_order", "sell_order", "order"):
            order = data.get(key)
            if isinstance(order, dict):
                text = (order.get("trade_terms") or "").strip()
                if text:
                    out.add(text)
    return sorted(out)


def load_reviews(db: sqlite3.Connection) -> list[dict]:
    """Тексти негативних відгуків разом із тим, що движок думав про них."""
    out: list[dict] = []
    try:
        rows = db.execute(
            "SELECT bad_texts_json FROM merchant_reviews "
            "WHERE bad_texts_json NOT IN ('[]', '')"
        ).fetchall()
    except sqlite3.Error:
        return []
    for (raw,) in rows:
        try:
            items = json.loads(raw)
        except Exception:
            continue
        for item in items:
            if isinstance(item, dict) and item.get("text"):
                out.append(item)
            elif isinstance(item, str) and item.strip():
                out.append({"text": item, "categories": [], "score": 0})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Звіти
# ─────────────────────────────────────────────────────────────────────────────

def report_signals(terms: list[str], reviews: list[dict]) -> None:
    """
    Скільки разів кожен сигнал спрацював на живому тексті.

    Два кінці шкали однаково цікаві. Нуль означає, що патерн або нічого не
    ловить, або ловить те, чого в потоці не буває, — і в обох випадках він
    створює хибне відчуття покриття. Дуже велике число на м'якому сигналі
    зазвичай значить, що він чіпляється за звичайні слова.
    """
    reg = builtin_registry()
    hits: Counter = Counter()
    samples: dict[str, list[str]] = defaultdict(list)

    for scope, texts in ((SCOPE_TERMS, terms), (SCOPE_REVIEWS, [r["text"] for r in reviews])):
        for text in texts:
            for m in match_text(text, scope).matches:
                hits[m.signal.key] += 1
                if len(samples[m.signal.key]) < 3:
                    samples[m.signal.key].append(m.excerpt)

    _head(f"СИГНАЛИ: {len(terms)} текстів умов + {len(reviews)} відгуків")
    order = {LAYER_HARD: 0, LAYER_SOFT: 1, LAYER_WARN: 2, LAYER_SAFE: 3}
    for signal in sorted(reg.signals, key=lambda s: (order.get(s.layer, 9), -hits[s.key], s.key)):
        n = hits[signal.key]
        mark = "  " if n else "··"   # ·· — жодного спрацювання на живих даних
        print(f"{mark} {signal.key:22} {signal.layer:5} {signal.scope:7} "
              f"w={signal.weight:<5} {n:>5}  {signal.category}")
        for s in samples[signal.key]:
            print(f"       ↳ {_short(s, 66)}")

    dead = [s.key for s in reg.signals if not hits[s.key]]
    if dead:
        print(f"\n·· жодного разу не спрацювали ({len(dead)}):")
        print("   " + ", ".join(dead))
        print("   Це не обов'язково погано: частина патернів пише про рідкісні")
        print("   схеми. Але перевірити варто — саме тут живуть мертві правила.")


def report_silent(terms: list[str], reviews: list[dict], limit: int = 12) -> None:
    """
    Тексти, на яких движок мовчить.

    Найкорисніший звіт із усіх: показує не те, що ми ловимо, а те, чого не
    бачимо. Саме звідси беруться нові сигнали.
    """
    _head("МОВЧАННЯ: тексти без жодного сигналу")

    quiet_terms = [t for t in terms if not match_text(t, SCOPE_TERMS).matches]
    print(f"Умови: {len(quiet_terms)} із {len(terms)} без сигналів")
    for text in quiet_terms[:limit]:
        print(f"   · {_short(text)}")

    quiet_reviews = [
        r for r in reviews if not match_text(r["text"], SCOPE_REVIEWS).matches
    ]
    print(f"\nВідгуки: {len(quiet_reviews)} із {len(reviews)} без сигналів")
    for r in quiet_reviews[:limit]:
        print(f"   · {_short(r['text'])}")
    print("\nЦе негативні відгуки — тобто люди скаржились. Якщо тут щось")
    print("серйозне, а движок мовчить, це прогалина в реєстрі.")


def report_negations(terms: list[str], reviews: list[dict], limit: int = 10) -> None:
    """
    Що саме заглушили заперечення.

    Заперечення — найтонше місце реєстру: воно рятує від «не приймаю обнал»,
    але тим самим механізмом може проковтнути справжній ризик. Тому кожне
    спрацювання варто вміти подивитись очима.
    """
    _head("ЗАПЕРЕЧЕННЯ: сигнали, які були заглушені")
    counts: Counter = Counter()
    samples: dict[str, list[str]] = defaultdict(list)

    for scope, texts in ((SCOPE_TERMS, terms), (SCOPE_REVIEWS, [r["text"] for r in reviews])):
        for text in texts:
            for key in match_text(text, scope).negated:
                counts[key] += 1
                if len(samples[key]) < limit:
                    samples[key].append(text)

    if not counts:
        print("Жодного заперечення на цих даних.")
        return

    for key, n in counts.most_common():
        print(f"\n  {key}: заглушено {n}×")
        for text in samples[key][:3]:
            print(f"     · {_short(text)}")
    print("\nПеревіряйте очима: якщо серед прикладів є справжній ризик —")
    print("заперечення надто широке.")


def report_drift(reviews: list[dict], limit: int = 15) -> None:
    """
    Розбіжність між тим, що лежить у базі, і тим, що движок каже зараз.

    Категорії відгуків рахувались у момент збору й лежать поруч із текстом.
    Порівняння з поточним результатом показує ціну зміни: що здобули, що
    втратили — на справжніх текстах, а не на вигаданих.
    """
    _head("ДРЕЙФ: збережені категорії проти поточних")
    gained, lost, same = [], [], 0

    for r in reviews:
        old = set(r.get("categories") or [])
        new = {m.category for m in match_text(r["text"], SCOPE_REVIEWS).matches if m.weight > 0}
        if old == new:
            same += 1
            continue
        # Тексти зберігаються обрізаними до 300 символів, а категорії
        # рахувались на повному. Розбіжність на межі — артефакт порівняння.
        truncated = len(r["text"]) >= 299
        (gained if new - old else lost).append((r, old, new, truncated))

    print(f"збіглось: {same}   здобули: {len(gained)}   втратили: {len(lost)}")
    real_lost = [x for x in lost if not x[3]]
    print(f"з утрачених {len(lost) - len(real_lost)} — артефакт обрізки на 300 символів")

    for title, rows in (("ЗДОБУЛИ", gained), ("ВТРАТИЛИ", real_lost)):
        if not rows:
            continue
        print(f"\n{title}:")
        for r, old, new, _ in rows[:limit]:
            print(f"   {sorted(old) or '—'} → {sorted(new) or '—'}")
            print(f"      {_short(r['text'], 70)}")


def report_verdicts(db: sqlite3.Connection) -> None:
    """Розподіл вердиктів у базі — та сама частка BLOCK, яку ми міряємо."""
    _head("ВЕРДИКТИ В БАЗІ")
    try:
        total = db.execute("SELECT COUNT(*) FROM merchant_verdict").fetchone()[0]
    except sqlite3.Error as e:
        print(f"Таблиця недоступна: {e}")
        return
    if not total:
        print("Порожньо.")
        return

    for column in ("verdict", "risk_type", "trade_recommendation", "llm_decision"):
        rows = db.execute(
            f"SELECT {column}, COUNT(*) n FROM merchant_verdict "
            f"GROUP BY 1 ORDER BY n DESC LIMIT 8"
        ).fetchall()
        parts = [f"{v or '—'}={n} ({n * 100 // total}%)" for v, n in rows]
        print(f"  {column:22} {', '.join(parts)}")
    print(f"  {'усього':22} {total}")

    stale = db.execute(
        "SELECT COUNT(*) FROM merchant_verdict WHERE COALESCE(updated_at, 0) = 0"
    ).fetchone()[0]
    if stale:
        print(f"\n  інвалідовано (updated_at=0): {stale} — чекають на перерахунок")


def explain(text: str) -> None:
    """Розбір одного тексту: що спрацювало, що заглушено і чому."""
    reg = builtin_registry()
    _head("РОЗБІР ТЕКСТУ")
    print(f"вхід:        {_short(text, 70)}")
    print(f"нормалізовано: {_short(normalize(text), 70)}")

    for scope in (SCOPE_TERMS, SCOPE_REVIEWS):
        found = match_text(text, scope)
        print(f"\n[{scope}]  score={found.score}  категорії={found.categories or '—'}")
        if found.hard:
            print(f"   ⛔ БЛОК: {found.hard.signal.key} ({found.hard.category})")
        for m in found.matches:
            print(f"   ✓ {m.signal.key:22} {m.signal.layer:5} w={m.weight:<5} {m.category}")
            print(f"        цитата: «{_short(m.excerpt, 60)}»")
            if m.signal.why:
                print(f"        чому:   {_short(m.signal.why, 60)}")
        for key in found.negated:
            signal = reg.by_key(key)
            print(f"   ⊘ заглушено запереченням: {key} ({signal.category if signal else '?'})")
        if found.suppressed:
            print(f"   ⊘ категорії під SAFE: {sorted(found.suppressed)}")
        if not found.matches and not found.negated:
            print("   — жодного сигналу")


def report_registry() -> None:
    """Склад реєстру — що взагалі вміє движок."""
    reg = builtin_registry()
    _head(f"РЕЄСТР: {len(reg.signals)} сигналів, {len(reg.categories())} категорій")
    by_cat: dict[str, list] = defaultdict(list)
    for s in reg.signals:
        by_cat[s.category].append(s)
    for cat in sorted(by_cat):
        signals = by_cat[cat]
        scopes = ",".join(sorted({s.scope for s in signals}))
        negations = sum(len(s.negations) for s in signals)
        print(f"  {cat:18} {len(signals):>2} сигн.  {scopes:12} заперечень: {negations:>2}"
              f"  {CATEGORY_TITLES.get(cat, '')}")
    no_why = [s.key for s in reg.signals if not s.why]
    if no_why:
        print(f"\n  без пояснення «чому це ризик» ({len(no_why)}): {', '.join(no_why)}")
        print("  Такий сигнал нічого не скаже ні людині в алерті, ні моделі.")


# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Проба ріск-енджину на реальних даних")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--text", help="розібрати один текст і вийти")
    ap.add_argument("--signals", action="store_true")
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--negations", action="store_true")
    ap.add_argument("--drift", action="store_true")
    ap.add_argument("--verdicts", action="store_true")
    ap.add_argument("--registry", action="store_true")
    args = ap.parse_args()

    if args.text:
        explain(args.text)
        return 0

    path = Path(args.db)
    if not path.exists():
        print(f"Бази немає: {path}")
        return 1
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    chosen = any((args.signals, args.silent, args.negations,
                  args.drift, args.verdicts, args.registry))
    everything = not chosen

    terms = reviews = None
    def _data():
        nonlocal terms, reviews
        if terms is None:
            terms, reviews = load_terms(db), load_reviews(db)
        return terms, reviews

    try:
        if everything or args.registry:
            report_registry()
        if everything or args.verdicts:
            report_verdicts(db)
        if everything or args.signals:
            report_signals(*_data())
        if everything or args.silent:
            report_silent(*_data())
        if everything or args.negations:
            report_negations(*_data())
        if everything or args.drift:
            report_drift(_data()[1])
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
