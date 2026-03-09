# core/rules.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v1.0  (Крок 2: rules.py + suppressors)
# =============================================================================
"""
Централізований реєстр правил для RegexAnalyzer.

Архітектура шарів:
  HARD_RULES    — майже однозначний скам → BLOCK без LLM
  SOFT_RULES    — двозначні сигнали     → NEEDS_LLM (score накопичується)
  WARN_RULES    — слабкі попередження   → показуються в алерті, не блокують
  SAFE_RULES    — suppressors           → від'ємні бали, скасовують soft

Принципи:
  - У HARD тільки те що однозначно небезпечно НЕЗАЛЕЖНО від контексту.
  - У SOFT все двозначне: "без третіх осіб" може бути і заборона і вимога —
    LLM вирішить по контексту.
  - Suppressor "не приймаю від третіх осіб" знімає S6 та подібні сигнали.
  - WARN не блокує і не ескалує — тільки інформує трейдера.

Додавання нових правил:
  1. Нове слово/патерн? → додай RegexRule в потрібний список.
  2. Нова категорія?    → додай в RISK_CATEGORY_UA в telegram_notifier.py.
  3. Новий suppressor?  → додай у SAFE_RULES з від'ємним weight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# LLM підтверджує — контекст важливий
HARD_CONFIRM_REQUIRED = frozenset({"EXTERNAL_LINK", "TRIANGLE"})

# Прямий BLOCK — однозначно, LLM нічого не додасть
HARD_DIRECT_BLOCK = frozenset({"NO_COMMENTS", "CASINO"})

@dataclass(slots=True)
class RegexRule:
    id: str
    category: str
    pattern: re.Pattern
    weight: int
    action: str  # BLOCK / NEEDS_LLM / WARN / SAFE
    description: str
    suppress_hard: frozenset[str] = frozenset()
    # suppress_hard: які HARD категорії це правило скасовує.
    # Встановлюється явно в SAFE_RULES — не виводиться з weight.
    # Приклад: frozenset({"EXTERNAL_LINK"}) для ANTI_EXTERNAL_LINK.


# ─────────────────────────────────────────────────────────────────────────────
# HARD RULES — BLOCK без LLM
# Умова включення: патерн ОДНОЗНАЧНО небезпечний незалежно від контексту.
# "без коментарів" → завжди трикутник-прикриття.
# "t.me/" у вимозі → завжди зовнішній канал.
# ─────────────────────────────────────────────────────────────────────────────
HARD_RULES: list[RegexRule] = [
    RegexRule(
        "H1",
        "EXTERNAL_LINK",
        re.compile(
            # Явні посилання — завжди BLOCK
            r"t\.me/|telegram\.me/|viber://|"
            # @username у тексті умов (4+ символів)
            r"@\w{4,}|"
            # "пишіть/напишіть у telegram/viber ПЕРЕД угодою"
            r"(пишіть|напишіть|contact|write).{0,25}"
            r"(telegram|tg|viber|whatsapp|signal|вайбер|телеграм)|"
            # "telegram/viber ПЕРЕД оплатою"
            r"(?<!не пишіть у )(?<!не переходжу в )"
            r"(telegram|tg|viber|whatsapp|signal|вайбер|телеграм).{0,25}"
            r"(перед оплатою|перед угодою|before payment|before deal)",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Вимагає перейти в зовнішній месенджер",
    ),
    RegexRule(
        "H2",
        "TRIANGLE",
        re.compile(
            r"чуж[іаю]\s*(карт|рахун|особ)|"
            r"карта\s*(знайом|друг|дружин|брат|сестр|родич)|"
            # Дропи тільки в позитивному контексті (не "без дропів")
            r"дроп[иів]?\s*(вітаються|ок|ok|прийма|можна|допускаються)|"
            r"переказ\s*від\s*(знайом|друг)|"
            r"оплата\s*(від|через)\s*інш(ої|ого)\s*особ|"
            r"пересилання\s*через\s*(знайом|посередник)|"
            r"від\s*іншої\s*людини",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Явна вимога/дозвіл оплати від третьої особи або дропа",
    ),
    RegexRule(
        "H3",
        "NO_COMMENTS",
        re.compile(
            r"без\s*(коментарів?|комент|призначен)|"
            r"пусте\s*(поле|призначення)|"
            r"нічого\s*(не\s*)?пиш|"
            r"не\s*вказуйте\s*призначення|"
            r"поле\s*(залиш|лиш)\s*пустим",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Заборона коментарів — класичне прикриття трикутника",
    ),
    RegexRule(
        "H4",
        "CASINO",
        re.compile(
            r"казино|casino|"
            r"1xbet|1x\s*bet|melbet|mostbet|betway|parimatch|fonbet|"
            r"покер|poker|букмекер|bookie|"
            r"процесинг|processing|агрегатор|"
            r"ставки\s*(на\s*)?(спорт|спортивн)|"
            r"гральн|gambling",
            re.IGNORECASE,
        ),
        100,
        "BLOCK",
        "Казино, ставки, букмекери, процесинг",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# SOFT RULES — NEEDS_LLM (двозначні сигнали)
# LLM отримує score + excerpts і вирішує по повному контексту.
# ─────────────────────────────────────────────────────────────────────────────
SOFT_RULES: list[RegexRule] = [
    # S1 — ФОП/бізнес-рахунок (не завжди шахрайство, але підозріло)
    RegexRule(
        "S1",
        "SUSPICIOUS_BIZ",
        re.compile(
            r"фоп\s*оплат|фоп\s*рахун|"
            r"фізична\s*особа\s*підприємець|"
            r"рахунок\s*фоп|iban\s*фоп|"
            r"рахунок\s*підприємц",
            re.IGNORECASE,
        ),
        40,
        "NEEDS_LLM",
        "Оплата на рахунок ФОП",
    ),
    # S2 — Писати до оплати (підозріло але може бути норм)
    RegexRule(
        "S2",
        "CHAT_FIRST",
        re.compile(
            r"пишіть\s+(мені|спочатку|перед)|"
            r"contact\s*(me\s*)?before|"
            r"write\s*(me\s*)?first|"
            r"написати\s+до\s+оплат|"
            r"напишіть\s+(спочатку|перед)|"
            r"повідомте\s+мене\s+перед",
            re.IGNORECASE,
        ),
        30,
        "NEEDS_LLM",
        "Просить написати до оплати",
    ),
    # S3 — Тиск апеляцією
    RegexRule(
        "S3",
        "APPEAL_PRESSURE",
        re.compile(
            r"апеляція|апеляцію\s*(відкрию|відкриваю|подам)|"
            r"скарга|поскаржусь|"
            r"appeal|dispute\s*(open|will)|report\s+you",
            re.IGNORECASE,
        ),
        30,
        "NEEDS_LLM",
        "Тиск апеляцією або скаргою",
    ),
    # S4 — Таргет на новачків
    RegexRule(
        "S4",
        "NEW_USERS",
        re.compile(
            r"тільки\s*(для\s*)?нових|"
            r"new\s*users?\s*only|"
            r"перший\s*раз|вперше\s*(торгую|купую)|"
            r"новачкам|для\s*новачків",
            re.IGNORECASE,
        ),
        20,
        "NEEDS_LLM",
        "Таргет на новачків",
    ),
    # S5 — Анонімність / термінал
    RegexRule(
        "S5",
        "ANONYMOUS",
        re.compile(
            r"анонімн|anonymous|"
            r"без\s*перевірк|no\s*questions|"
            r"без\s*зайвих\s*питань|"
            r"термінал\s*(оплат|поповнен)|"
            r"cash.?in",
            re.IGNORECASE,
        ),
        30,
        "NEEDS_LLM",
        "Анонімність, «без питань» або готівковий термінал",
    ),
    # S6 — Згадка третіх осіб (може бути заборона! LLM вирішить)
    RegexRule(
        "S6",
        "THIRD_PARTY_HINT",
        re.compile(
            r"третіх\s+осіб|3\s*особ|third\s*part[yi]|"
            r"від\s*третіх|від\s*інших\s*осіб",
            re.IGNORECASE,
        ),
        20,
        "NEEDS_LLM",
        "Згадка третіх осіб — потрібен контекст (заборона чи вимога?)",
    ),
    # S7 — Чардж / реф / диспут (арбітражний сленг)
    RegexRule(
        "S7",
        "CHARGEBACK",
        re.compile(
            r"чардж|chargeback|charge\s*back|"
            r"реф(анд)?|refund|повернення\s*коштів\s*(через\s*банк)|"
            r"диспут|dispute\s*(відкри|подам)|"
            r"(відкрию|подам)\s*спір",
            re.IGNORECASE,
        ),
        40,
        "NEEDS_LLM",
        "Сленг чарджбек/реф/диспут",
    ),
    # S8 — Фінмон / заморозка / блокування
    RegexRule(
        "S8",
        "FINCRIME",
        re.compile(
            r"фінмон|фінансови[йх]\s*моніторинг|"
            r"заморозк|блокуван(ня|і)\s*(рахунку|картки)|"
            r"anti.?money|aml|"
            r"сір(а|і)\s*(схем|гроші)|"
            r"відмивання",
            re.IGNORECASE,
        ),
        50,
        "NEEDS_LLM",
        "Фінмон, заморозка, сіра схема",
    ),
    # S9 — Посередник / номінал (арбітражний сленг)
    RegexRule(
        "S9",
        "MIDDLEMAN",
        re.compile(
            r"посередник|номінал[ьн]?|"
            r"через\s*посередника|"
            r"middleman|nominee",
            re.IGNORECASE,
        ),
        40,
        "NEEDS_LLM",
        "Посередник або номінальний власник",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# WARN RULES — тільки попередження в алерті, не блокують, не ескалують
# ─────────────────────────────────────────────────────────────────────────────
WARN_RULES: list[RegexRule] = [
    # W_R1 — Квитанція/чек (сам по собі не скам, але варто знати)
    RegexRule(
        "WR1",
        "RECEIPT_REQUIRED",
        re.compile(
            r"квитанці[яю]|скриншот\s*(оплат|квитанц)|"
            r"фото\s*(чеку|квитанц)|чек\s*(оплат|надішліть)|"
            r"підтвердження\s*оплати\s*(надішл|скинь)|"
            r"screenshot\s*of\s*payment",
            re.IGNORECASE,
        ),
        10,
        "WARN",
        "Мерчант вимагає квитанцію або скриншот оплати",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# SAFE RULES — suppressors (від'ємні бали)
# Знижують загальний score щоб легітимні мерчанти не потрапляли в LLM.
# ─────────────────────────────────────────────────────────────────────────────
SAFE_RULES: list[RegexRule] = [
    # Стандартні банки — трохи знижує підозру
    RegexRule(
        "W1",
        "STANDARD_BANKS",
        re.compile(
            r"тільки\s*(mono|monobank|privat|privatbank|пумб|а-банк|abank|моно|приват|"
            r"oschad|ощадбанк|raiffeisen|райффайзен|sense|сенс)",
            re.IGNORECASE,
        ),
        -20,
        "SAFE",
        "Перелік стандартних банків (норм)",
    ),
    # Мерчант ЗАБОРОНЯЄ третіх осіб — скасовує HARD TRIANGLE
    RegexRule(
        "W2",
        "ANTI_THIRD_PARTY",
        re.compile(
            r"без\s+третіх\s+осіб|"
            r"не\s+(від|приймаю\s+від)\s+третіх\s+осіб|"
            r"тільки\s+зі?\s+своєї\s+картки|"
            r"лише\s+зі?\s+своєї\s+картки|"
            r"тільки\s+з\s+особистої\s+картки|"
            r"оплата\s+лише\s+з\s+картки\s+власника|"
            r"переказ\s+тільки\s+від\s+власника|"
            r"не\s+приймаю\s+від\s+інших\s+осіб|"
            r"без\s+посередників|"
            r"тільки\s+власник\s+картки|"
            r"дропи?\s+(заборонен|не\s+прийма|відмовлю)",
            re.IGNORECASE,
        ),
        -50,
        "SAFE",
        "Мерчант явно забороняє третіх осіб",
        suppress_hard=frozenset({"TRIANGLE"}),
    ),
    # Мерчант ЗАБОРОНЯЄ зовнішні месенджери — скасовує HARD EXTERNAL_LINK
    RegexRule(
        "W3",
        "ANTI_EXTERNAL_LINK",
        re.compile(
            r"не\s+пишіть\s+(у|в)\s+(telegram|tg|viber|whatsapp|signal|телеграм|вайбер)|"
            r"в\s+месенджери\s+не\s+переходжу|"
            r"спілкування\s+тільки\s+(в\s+)?чаті\s+біржі|"
            r"тільки\s+чат\s+біржі|"
            r"не\s+виходжу\s+за\s+межі\s+платформи|"
            r"chat\s+only\s+(on\s+)?platform",
            re.IGNORECASE,
        ),
        -40,
        "SAFE",
        "Мерчант забороняє зовнішні месенджери",
        suppress_hard=frozenset({"EXTERNAL_LINK"}),
    ),
    # Перевірений мерчант / офіційно сертифікований
    RegexRule(
        "W4",
        "VERIFIED_MERCHANT",
        re.compile(
            r"верифікован|verified\s*merchant|"
            r"сертифікован|офіційний\s*мерчант|"
            r"trusted\s*seller",
            re.IGNORECASE,
        ),
        -15,
        "SAFE",
        "Мерчант заявляє про верифікацію (слабкий suppressor)",
    ),
]


# Зведений список для RegexAnalyzer
ALL_RULES: list[RegexRule] = HARD_RULES + SOFT_RULES + WARN_RULES + SAFE_RULES
# Карта suppression: category → frozenset HARD категорій що скасовуються.
# Будується автоматично з SAFE_RULES.suppress_hard — не треба підтримувати вручну.
SUPPRESSOR_MAP: dict[str, frozenset] = {
    rule.category: rule.suppress_hard
    for rule in SAFE_RULES
    if rule.suppress_hard
}