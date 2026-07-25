"""
build_rules.py
==============
Читає antifrod_trade_terms.json, antifrod_reviews.json, antifrod_concepts.json.
Дедуплікує, merge-ить policy, генерує:
  - core/rules_generated.py   (чернетка нового rules.py)
  - tests/test_rules_generated.py  (pytest-сценарії)

Policy rules для severity merge (source_type):
  trade_terms  → merchant instruction layer  → severity as-is
  reviews      → review signal layer         → max SOFT, never HARD
                 Exception: якщо concept-layer каже HARD і ambiguous=false → HARD
  concepts     → base regex atoms layer      → severity_default unless overridden

Co-occurrence guard:
  SHORT_ATOMS (≤3 символи або ambiguous=true) → downgrade to SOFT/WARN unless
  paired with a second signal in the same rule group.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

# ── Config ────────────────────────────────────────────────────────────────────
SOURCE_DIR = Path(__file__).parent
TRADE_TERMS_FILE = SOURCE_DIR / "antifrod_trade_terms.json"
REVIEWS_FILE     = SOURCE_DIR / "antifrod_reviews.json"
CONCEPTS_FILE    = SOURCE_DIR / "antifrod_concepts.json"

# 🚀 ВКАЗУЄМО СКРИПТУ ПИСАТИ ОДРАЗУ В БОЙОВІ ФАЙЛИ:
OUT_RULES  = SOURCE_DIR / "core" / "rules.py"
OUT_TESTS  = SOURCE_DIR / "core" / "test_rules.py"

# Short atoms that MUST be ambiguous=SOFT regardless of source claims
FORCE_SOFT_ATOMS = {
    r"\btg\b", r"\bлс\b", r"\bпроц\b", r"\bбук\b",
    r"\bреф\b", r"\bдисп\b", r"\bапел\b",
}

SEVERITY_ORDER = {"SAFE": -1, "WARN": 0, "SOFT": 1, "HARD": 2}

CATEGORY_MAP = {
    "EXTERNAL_LINK":   ("HARD_RULES",  100, "BLOCK"),
    "TRIANGLE":        ("HARD_RULES",  100, "BLOCK"),
    "NO_COMMENTS":     ("HARD_RULES",  100, "BLOCK"),
    "CASINO":          ("HARD_RULES",  100, "BLOCK"),
    "FINCRIME":        ("SOFT_RULES",   50, "NEEDS_LLM"),
    "SUSPICIOUS_BIZ":  ("SOFT_RULES",   40, "NEEDS_LLM"),
    "MIDDLEMAN":       ("SOFT_RULES",   40, "NEEDS_LLM"),
    "CHARGEBACK":      ("SOFT_RULES",   40, "NEEDS_LLM"),
    "CHAT_FIRST":      ("SOFT_RULES",   30, "NEEDS_LLM"),
    "APPEAL_PRESSURE": ("SOFT_RULES",   30, "NEEDS_LLM"),
    "ANONYMOUS":       ("SOFT_RULES",   30, "NEEDS_LLM"),
    "THIRD_PARTY_HINT":("SOFT_RULES",   20, "NEEDS_LLM"),
    "NEW_USERS":       ("SOFT_RULES",   20, "NEEDS_LLM"),
    "RECEIPT_REQUIRED":("WARN_RULES",   10, "WARN"),
    "SAFE":            ("SAFE_RULES",  -50, "SAFE"),
}

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class MergedRule:
    rule_id:       str
    category:      str
    severity:      str          # HARD | SOFT | WARN | SAFE
    source_type:   str          # trade_terms | review | concept | merged
    ambiguous:     bool
    regex_atoms:   list[str]    = field(default_factory=list)
    suppress_hard: list[str]    = field(default_factory=list)
    description:   str          = ""
    why_risky:     str          = ""
    confidence:    float        = 0.0
    review_only:   bool         = False
    co_required:   Optional[str]= None   # category that must co-occur for SOFT short atoms

    @property
    def effective_severity(self) -> str:
        # review-only signals capped at SOFT
        if self.review_only and self.severity == "HARD":
            return "SOFT"
        return self.severity

    @property
    def layer(self) -> str:
        base_layer = CATEGORY_MAP.get(self.category, ("SOFT_RULES", 30, "NEEDS_LLM"))[0]
        # review_only rules never go into HARD_RULES — always SOFT at most
        if self.review_only and base_layer == "HARD_RULES":
            return "SOFT_RULES"
        # Explicit HARD + unambiguous → force to HARD_RULES even if category default is SOFT
        if self.severity == "HARD" and not self.ambiguous and not self.review_only:
            return "HARD_RULES"
        # Downgraded severity also shifts layer
        if self.effective_severity == "SOFT" and base_layer == "HARD_RULES":
            return "SOFT_RULES"
        return base_layer

    @property
    def weight(self) -> int:
        # Weight is always derived from the final resolved layer —
        # not from category default — so HARD_RULES always carries 100,
        # SAFE_RULES always -50, etc. This prevents the edge-case where
        # a rule is promoted to HARD_RULES but retains its soft-ish weight.
        layer = self.layer
        if layer == "HARD_RULES":
            return 100
        if layer == "WARN_RULES":
            return 10
        if layer == "SAFE_RULES":
            return -50
        # SOFT_RULES: use category default, capped at 50
        return CATEGORY_MAP.get(self.category, ("SOFT_RULES", 30, "NEEDS_LLM"))[1]

    @property
    def action(self) -> str:
        # Action mirrors layer so the generated metadata stays consistent
        # with how regex_analyzer.py actually routes each rule.
        layer = self.layer
        if layer == "HARD_RULES":
            return "BLOCK"
        if layer == "WARN_RULES":
            return "WARN"
        if layer == "SAFE_RULES":
            return "SAFE"
        return "NEEDS_LLM"


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _clean_atom(atom: str) -> str:
    """Strips surrounding slashes if someone wrote /pattern/."""
    return atom.strip().strip("/")


def _split_top_level_or(pattern: str) -> list[str]:
    """
    Split a regex string on top-level | only.
    Respects parenthesis depth so that alternations inside groups
    (including non-capturing (?:...) and lookahead (?=...)/(?!...))
    are never split.

    Handles:
      - a|b|c                          → [a, b, c]
      - (?:a|b)|c                      → [(?:a|b), c]
      - x{0,10}|y                      → [x{0,10}, y]
      - (?=abc)foo|bar                 → [(?=abc)foo, bar]
      - без.{0,10}(від|при).{0}|дроп  → [без.{0,10}(від|при).{0}, дроп]
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    esc = False

    for ch in pattern:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == "\\":
            buf.append(ch)
            esc = True
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        if ch == "|" and depth == 0:
            part = "".join(buf).strip()
            if part:
                out.append(part)
            buf = []
            continue
        buf.append(ch)

    part = "".join(buf).strip()
    if part:
        out.append(part)

    return out


def _atoms_from_candidate(c: dict) -> list[str]:
    """
    Extract regex atoms from a candidate's regex_hint by splitting on
    top-level | so each atom can be deduped and recombined cleanly.
    """
    hint = c.get("regex_hint", "")
    if not hint:
        return []
    return [_clean_atom(x) for x in _split_top_level_or(_clean_atom(hint))]


# ── Unit tests for _split_top_level_or (run at import-time in dev) ───────────
def _run_splitter_tests() -> None:
    cases = [
        # (input, expected_parts)
        ("a|b|c",                                  ["a", "b", "c"]),
        ("(?:a|b)|c",                              ["(?:a|b)", "c"]),
        ("x{0,10}|y",                              ["x{0,10}", "y"]),
        ("(?=abc)foo|bar",                         ["(?=abc)foo", "bar"]),
        ("без.{0,10}(від|при).{0,5}третіх|дроп",  ["без.{0,10}(від|при).{0,5}третіх", "дроп"]),
        ("single",                                  ["single"]),
        ("a|(b|c)|d",                              ["a", "(b|c)", "d"]),
        (r"\bреф\b|refund|реф.{0,15}банк",        [r"\bреф\b", "refund", r"реф.{0,15}банк"]),
        ("(?!safe)danger|ok",                      ["(?!safe)danger", "ok"]),
    ]
    errors = []
    for pattern, expected in cases:
        got = _split_top_level_or(pattern)
        if got != expected:
            errors.append(f"  FAIL: {repr(pattern)}\n    expected {expected}\n    got     {got}")
    if errors:
        raise AssertionError("_split_top_level_or failures:\n" + "\n".join(errors))
    print(f"  ✅ _split_top_level_or: {len(cases)} cases passed")





def _atoms_from_concept(c: dict) -> list[str]:
    return [_clean_atom(a) for a in c.get("regex_atoms", []) if a.strip()]


def _merge_severity(a: str, b: str) -> str:
    if SEVERITY_ORDER.get(a, 0) >= SEVERITY_ORDER.get(b, 0):
        return a
    return b


# ── Main builder ──────────────────────────────────────────────────────────────

def build_merged_rules() -> list[MergedRule]:
    trade_data   = load_json(TRADE_TERMS_FILE)
    reviews_data = load_json(REVIEWS_FILE)
    concepts_data= load_json(CONCEPTS_FILE)

    # key: (category, normalized_description) → MergedRule
    registry: dict[str, MergedRule] = {}

    counter = {"H": 0, "S": 0, "W": 0, "F": 0}

    def _next_id(severity: str, category: str) -> str:
        prefix_map = {
            "HARD": "H", "SOFT": "S", "WARN": "W", "SAFE": "F",
        }
        p = prefix_map.get(severity, "X")
        counter[p] = counter.get(p, 0) + 1
        return f"GEN_{p}{counter[p]:02d}_{category[:8]}"

    # ── 1. Concepts as base layer ────────────────────────────────────────────
    for c in concepts_data.get("concepts", []):
        cat   = c.get("category", "")
        sev   = c.get("severity_default", "SOFT")
        atoms = _atoms_from_concept(c)
        if not atoms:
            continue

        key = f"concept::{cat}"
        r = MergedRule(
            rule_id     = _next_id(sev, cat),
            category    = cat,
            severity    = sev,
            source_type = "concept",
            ambiguous   = c.get("ambiguous", True),
            regex_atoms = atoms,
            suppress_hard=c.get("negative_safe_forms", []),  # used as hint
            description = c.get("why_risky", "")[:120],
            why_risky   = c.get("why_risky", ""),
            confidence  = float(c.get("confidence", 0.7)),
        )
        registry[key] = r

    # ── 2. Trade terms candidates — override/extend concepts ────────────────
    for c in trade_data.get("candidates", []):
        cat   = c.get("category", "")
        sev   = c.get("severity", "SOFT")
        atoms = _atoms_from_candidate(c)
        if not atoms:
            continue

        key = f"trade::{cat}::{c.get('phrase','')[:30]}"
        existing_key = f"concept::{cat}"

        if existing_key in registry:
            existing = registry[existing_key]
            # Merge: take stricter severity if trade_terms says HARD
            merged_sev = _merge_severity(sev, existing.severity)
            new_atoms  = list(dict.fromkeys(existing.regex_atoms + atoms))
            existing.severity    = merged_sev
            existing.regex_atoms = new_atoms
            existing.source_type = "merged"
            # If trade_terms gives an explicit non-ambiguous HARD → clear ambiguous flag
            if sev == "HARD" and not c.get("ambiguous", True):
                existing.ambiguous = False
            suppress = c.get("suppress_hard", [])
            if suppress:
                existing.suppress_hard = list(set(existing.suppress_hard + suppress))
        else:
            r = MergedRule(
                rule_id     = _next_id(sev, cat),
                category    = cat,
                severity    = sev,
                source_type = "trade_terms",
                ambiguous   = c.get("ambiguous", True),
                regex_atoms = atoms,
                suppress_hard= c.get("suppress_hard", []),
                description = c.get("why_risky", "")[:120],
                why_risky   = c.get("why_risky", ""),
                confidence  = float(c.get("confidence", 0.7)),
            )
            registry[key] = r

    # ── 3. Review candidates — always SOFT cap, review_only=True ─────────────
    review_registry: dict[str, MergedRule] = {}

    for c in reviews_data.get("candidates", []):
        cat   = c.get("category", "")
        sev   = c.get("severity", "SOFT")
        atoms = _atoms_from_candidate(c)
        if not atoms:
            continue

        # Policy: review source → cap at SOFT
        # Exception: HARD + ambiguous=false + concept also says HARD → keep HARD
        concept_sev = registry.get(f"concept::{cat}", MergedRule("","",sev,"",True)).severity
        is_hard_confirmed = (
            sev == "HARD"
            and not c.get("ambiguous", True)
            and concept_sev == "HARD"
        )
        effective_sev = sev if is_hard_confirmed else min(
            [sev, "SOFT"], key=lambda x: SEVERITY_ORDER.get(x, 0)
        )

        # For short ambiguous atoms → force WARN
        short_flag = any(
            a.strip() in FORCE_SOFT_ATOMS or (len(re.sub(r'[\\^$.*+?(){}|[\]]', '', a)) <= 4)
            for a in atoms
        )
        if short_flag and effective_sev not in ("SAFE",):
            effective_sev = "SOFT"
            co_req = cat  # needs co-occurrence

        key = f"review::{cat}::{c.get('phrase','')[:30]}"
        r = MergedRule(
            rule_id     = _next_id(effective_sev, cat),
            category    = cat,
            severity    = effective_sev,
            source_type = "review",
            ambiguous   = c.get("ambiguous", True) or not is_hard_confirmed,
            regex_atoms = atoms,
            suppress_hard= c.get("suppress_hard", []),
            description = c.get("why_risky", "")[:120],
            why_risky   = c.get("why_risky", ""),
            confidence  = float(c.get("confidence", 0.7)),
            review_only = True,
        )
        review_registry[key] = r

    all_rules = list(registry.values()) + list(review_registry.values())

    # ── 4. Dedup: merge rules with identical category + very similar atoms ───
    seen_cats: dict[str, MergedRule] = {}
    deduped: list[MergedRule] = []

    for r in all_rules:
        cat_key = r.category
        if cat_key in seen_cats and r.source_type != "review":
            existing = seen_cats[cat_key]
            # Merge atoms, keep stricter severity
            existing.regex_atoms = list(dict.fromkeys(existing.regex_atoms + r.regex_atoms))
            existing.severity = _merge_severity(existing.severity, r.severity)
            existing.source_type = "merged"
        elif cat_key not in seen_cats and r.source_type != "review":
            seen_cats[cat_key] = r
            deduped.append(r)
        else:
            # review rules kept separate (review_only flag)
            deduped.append(r)

    # ── 5. Inject canonical SAFE suppressors that JSON won't produce ─────────
    # These mirror the original rules.py W2/W3 and must be present in output.
    CANONICAL_SAFE = [
        MergedRule(
            rule_id     = "GEN_SAFE_ANTI_EXT",
            category    = "SAFE",
            severity    = "SAFE",
            source_type = "canonical",
            ambiguous   = False,
            regex_atoms = [
                r"не.{0,10}пишіть.{0,10}(у|в).{0,5}(telegram|tg|viber|whatsapp|signal|телеграм|вайбер)",
                r"в.{0,5}месенджери.{0,5}не.{0,5}переходжу",
                r"спілкування.{0,5}тільки.{0,5}(в\s*)?чаті.{0,5}біржі",
                r"тільки.{0,5}чат.{0,5}(біржі|платформи)",
                r"не.{0,5}виходжу.{0,5}за.{0,5}межі",
                r"chat.{0,5}only.{0,5}(on\s*)?platform",
            ],
            suppress_hard = ["EXTERNAL_LINK"],
            description   = "Мерчант забороняє зовнішні месенджери",
            confidence    = 0.96,
        ),
        MergedRule(
            rule_id     = "GEN_SAFE_ANTI_DROP",
            category    = "SAFE",
            severity    = "SAFE",
            source_type = "canonical",
            ambiguous   = False,
            regex_atoms = [
                r"без.{0,5}третіх.{0,5}осіб",
                r"не.{0,10}(від|приймаю.{0,5}від).{0,5}третіх.{0,5}осіб",
                r"тільки.{0,5}(зі?|з).{0,5}своєї.{0,5}картк",
                r"лише.{0,5}(зі?|з).{0,5}своєї.{0,5}картк",
                r"тільки.{0,5}з.{0,5}особистої.{0,5}картк",
                r"оплата.{0,5}лише.{0,5}з.{0,5}картки.{0,5}власника",
                r"переказ.{0,5}тільки.{0,5}від.{0,5}власника",
                r"не.{0,5}приймаю.{0,5}від.{0,5}інших.{0,5}осіб",
                r"без.{0,5}посередників",
                r"тільки.{0,5}власник.{0,5}картки",
                r"дроп[иі]?.{0,5}(заборонен|не\s*прийма|відмовлю)",
                r"без.{0,5}дропів",
            ],
            suppress_hard = ["TRIANGLE", "THIRD_PARTY_HINT"],
            description   = "Мерчант явно забороняє третіх осіб та дропів",
            confidence    = 0.97,
        ),
    ]

    # Remove any auto-generated SAFE rules that lack suppress_hard (they were built from phrases)
    # and replace with canonical ones
    deduped = [r for r in deduped if not (r.category == "SAFE" and not r.suppress_hard)]

    # Avoid duplicating canonical safe rules
    existing_safe_ids = {r.rule_id for r in deduped}
    for cs in CANONICAL_SAFE:
        if cs.rule_id not in existing_safe_ids:
            deduped.append(cs)

    return deduped


# ── Code generator ─────────────────────────────────────────────────────────────

HEADER = '''# core/rules_generated.py
# =============================================================================
# AUTO-GENERATED by build_rules.py — НЕ РЕДАГУВАТИ РУКАМИ
# Джерела: antifrod_trade_terms.json + antifrod_reviews.json + antifrod_concepts.json
# Policy:
#   - trade_terms  → merchant instruction layer, severity as-is
#   - reviews      → capped at SOFT (review_only=True), no direct BLOCK
#   - concepts     → base regex atoms, deduped + merged
#   - short ambiguous atoms → SOFT, require LLM co-eval
# =============================================================================
"""
Розширений реєстр правил v2.0.

Нові правила додані поверх базового rules.py:
  - HARD/SOFT/WARN/SAFE шари збережені
  - review_only правила мають окремий review_only=True прапор
  - suppress_hard поля заповнені де є explicit SAFE кандидати

Додавання нових правил:
  1. Оновіть antifrod_*.json
  2. Запустіть build_rules.py → rules_generated.py
  3. Скопіюйте нові RegexRule в production rules.py
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HARD_CONFIRM_REQUIRED = frozenset({"EXTERNAL_LINK", "TRIANGLE"})
HARD_DIRECT_BLOCK     = frozenset({"NO_COMMENTS", "CASINO"})


@dataclass(slots=True)
class RegexRule:
    id:            str
    category:      str
    pattern:       re.Pattern
    weight:        int
    action:        str
    description:   str
    suppress_hard: frozenset = frozenset()
    review_only:   bool      = False


'''

def _escape_py(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _build_pattern_str(atoms: list[str]) -> str:
    """Combine atoms into a single regex alternation."""
    # Deduplicate preserving order
    seen = dict.fromkeys(atoms)
    parts = list(seen.keys())
    if len(parts) == 1:
        return parts[0]
    return "|".join(f"(?:{p})" for p in parts)


def generate_rules_py(rules: list[MergedRule]) -> str:
    sections: dict[str, list[str]] = {
        "HARD_RULES":  [],
        "SOFT_RULES":  [],
        "WARN_RULES":  [],
        "SAFE_RULES":  [],
    }

    for r in rules:
        sev     = r.effective_severity
        layer   = r.layer
        weight  = r.weight
        action  = r.action
        pattern = _build_pattern_str(r.regex_atoms)
        desc    = _escape_py(r.description or r.why_risky or r.category)[:100]
        suppress= (
            f"frozenset({{{', '.join(repr(s) for s in r.suppress_hard)}}})"
            if r.suppress_hard else "frozenset()"
        )
        review_note = "  # review-only signal" if r.review_only else ""

        code = f'''    RegexRule(
        "{r.rule_id}",
        "{r.category}",
        re.compile(
            r"{pattern}",
            re.IGNORECASE,
        ),
        {weight},
        "{action}",
        "{desc}",
        suppress_hard={suppress},
        review_only={r.review_only},{review_note}
    ),'''
        sections[layer].append(code)

    out = [HEADER]

    for list_name, items in sections.items():
        if not items:
            out.append(f"{list_name}: list[RegexRule] = []\n")
            continue
        out.append(f"{list_name}: list[RegexRule] = [")
        out.extend(items)
        out.append("]\n")

    out.append("\nALL_RULES: list[RegexRule] = HARD_RULES + SOFT_RULES + WARN_RULES + SAFE_RULES\n")
    out.append("""
SUPPRESSOR_MAP: dict[str, frozenset] = {
    rule.category: rule.suppress_hard
    for rule in SAFE_RULES
    if rule.suppress_hard
}
""")
    return "\n".join(out)


# ── Test generator ─────────────────────────────────────────────────────────────

TEST_HEADER = '''"""
tests/test_rules_generated.py
AUTO-GENERATED by build_rules.py

Покриває:
  - HARD правила: повинні давати BLOCK
  - SOFT правила: повинні накопичувати score > 0
  - SAFE suppressors: повинні скасовувати відповідний HARD
  - review_only правила: не повинні BLOCK, тільки score
  - False positive guards: амбігуозні короткі маркери не повинні блокувати чисті тексти
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from rules_generated import HARD_RULES, SOFT_RULES, SAFE_RULES, WARN_RULES, ALL_RULES


def _matches(rule, text: str) -> bool:
    return bool(rule.pattern.search(text.lower()))


def _first_hard(text: str):
    for r in HARD_RULES:
        if _matches(r, text):
            return r
    return None


def _soft_score(text: str) -> int:
    score = 0
    for r in SOFT_RULES:
        if _matches(r, text):
            score += r.weight
    return score


def _safe_suppresses(text: str, hard_category: str) -> bool:
    for r in SAFE_RULES:
        if hard_category in r.suppress_hard and _matches(r, text):
            return True
    return False

'''

def generate_tests(rules: list[MergedRule]) -> str:
    lines = [TEST_HEADER]

    # ── HARD positive tests
    lines.append("# ═══════════════════════════════════════════════════════")
    lines.append("# HARD rules — повинні давати збіг")
    lines.append("# ═══════════════════════════════════════════════════════\n")

    hard_cases = [
        ("EXTERNAL_LINK", "пишіть мені у телеграм перед оплатою", True),
        ("EXTERNAL_LINK", "t.me/mychannel123", True),
        ("EXTERNAL_LINK", "@coolmerchant99 for details", True),
        ("TRIANGLE",      "оплата від знайомого вітається", True),
        ("TRIANGLE",      "дропи ок, можна переказувати", True),
        ("TRIANGLE",      "карта дружини підійде", True),
        ("NO_COMMENTS",   "без коментарів у переказі", True),
        ("NO_COMMENTS",   "поле залишіть пустим", True),
        ("NO_COMMENTS",   "нічого не пишіть у призначенні", True),
        ("CASINO",        "казино трафік приймаю", True),
        ("CASINO",        "1xbet та melbet ок", True),
        ("CASINO",        "процесинг агрегатор", True),
        ("FINCRIME",      "обнал приймаю, ставка 5%", True),
        ("FINCRIME",      "грязні гроші без питань", True),
    ]

    lines.append("@pytest.mark.parametrize('category,text,expected', [")
    for cat, text, exp in hard_cases:
        lines.append(f"    ({repr(cat)}, {repr(text)}, {exp}),")
    lines.append("])")
    lines.append("def test_hard_match(category, text, expected):")
    lines.append("    matched = _first_hard(text)")
    lines.append("    if expected:")
    lines.append("        assert matched is not None, f'HARD miss: {repr(text)}'")
    lines.append("        assert matched.category == category, f'Wrong category: {matched.category} != {category}'")
    lines.append("    else:")
    lines.append("        assert matched is None, f'Unexpected HARD match: {repr(text)}'")
    lines.append("")

    # ── SAFE suppressor tests
    lines.append("\n# ═══════════════════════════════════════════════════════")
    lines.append("# SAFE suppressors — повинні скасовувати HARD")
    lines.append("# ═══════════════════════════════════════════════════════\n")

    suppressor_cases = [
        ("TRIANGLE",      "без третіх осіб, тільки власник картки"),
        ("TRIANGLE",      "дропи заборонені, без посередників"),
        ("TRIANGLE",      "тільки зі своєї картки"),
        ("EXTERNAL_LINK", "не пишіть у телеграм, тільки чат біржі"),
        ("EXTERNAL_LINK", "в месенджери не переходжу"),
    ]

    lines.append("@pytest.mark.parametrize('hard_category,text', [")
    for cat, text in suppressor_cases:
        lines.append(f"    ({repr(cat)}, {repr(text)}),")
    lines.append("])")
    lines.append("def test_safe_suppresses_hard(hard_category, text):")
    lines.append("    assert _safe_suppresses(text, hard_category), \\")
    lines.append("        f'Suppressor miss for {hard_category}: {repr(text)}'")
    lines.append("")

    # ── SOFT score accumulation tests
    lines.append("\n# ═══════════════════════════════════════════════════════")
    lines.append("# SOFT rules — повинні накопичувати score > threshold")
    lines.append("# ═══════════════════════════════════════════════════════\n")

    soft_cases = [
        ("писати в лс до оплати", 20),
        ("рахунок фоп, iban фоп", 20),
        ("апеляцію відкрию якщо не платите", 20),
        ("анонімно, без зайвих питань", 20),
        ("посередник є, номінальний рахунок", 30),
        ("чардж зроблю якщо не відпустите", 30),
        ("фінмон не проблема, заморозка не наша вина", 30),
    ]

    lines.append("@pytest.mark.parametrize('text,min_score', [")
    for text, minscore in soft_cases:
        lines.append(f"    ({repr(text)}, {minscore}),")
    lines.append("])")
    lines.append("def test_soft_score(text, min_score):")
    lines.append("    score = _soft_score(text)")
    lines.append("    assert score >= min_score, \\")
    lines.append("        f'Score too low for {repr(text)}: {score} < {min_score}'")
    lines.append("")

    # ── False positive guard tests (clean texts that must NOT trigger HARD)
    lines.append("\n# ═══════════════════════════════════════════════════════")
    lines.append("# False positive guard — чисті тексти НЕ повинні BLOCK")
    lines.append("# ═══════════════════════════════════════════════════════\n")

    clean_cases = [
        "тільки monobank та privatbank, верифікований мерчант",
        "не пишіть у telegram, спілкування тільки в чаті біржі",
        "без третіх осіб, тільки власник картки",
        "без посередників, без дропів, переказ тільки від власника",
        "оплата mono або приват, перевірений мерчант 500+ угод",
        "дропи не приймаю, відмовлю",
        "не виходжу за межі платформи",
        "тільки з особистої картки, без посередників",
    ]

    lines.append("@pytest.mark.parametrize('text', [")
    for text in clean_cases:
        lines.append(f"    {repr(text)},")
    lines.append("])")
    lines.append("def test_clean_text_no_hard_block(text):")
    lines.append("    matched = _first_hard(text)")
    lines.append("    assert matched is None, \\")
    lines.append("        f'False positive HARD on clean text: {repr(text)} → {matched}'")
    lines.append("")

    # ── Review-only rules must NOT produce BLOCK action directly
    lines.append("\n# ═══════════════════════════════════════════════════════")
    lines.append("# review_only rules — ніколи не повинні бути в HARD_RULES")
    lines.append("# ═══════════════════════════════════════════════════════\n")

    lines.append("def test_review_only_not_in_hard():")
    lines.append("    for r in HARD_RULES:")
    lines.append("        assert not r.review_only, \\")
    lines.append("            f'review_only rule found in HARD_RULES: {r.id} ({r.category})'")
    lines.append("")

    # ── Regex compilation sanity
    lines.append("\n# ═══════════════════════════════════════════════════════")
    lines.append("# Sanity — всі патерни компілюються без помилок")
    lines.append("# ═══════════════════════════════════════════════════════\n")

    lines.append("def test_all_patterns_compile():")
    lines.append("    for r in ALL_RULES:")
    lines.append("        assert r.pattern is not None, f'None pattern in rule {r.id}'")
    lines.append("        # Just try a match to ensure no runtime error")
    lines.append("        try:")
    lines.append("            r.pattern.search('test string')")
    lines.append("        except Exception as e:")
    lines.append("            pytest.fail(f'Pattern error in {r.id}: {e}')")
    lines.append("")

    return "\n".join(lines)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    print("🔧 Юніт-тести парсера regex_hint...")
    _run_splitter_tests()
    print("🔧 Будуємо merged rules...")
    rules = build_merged_rules()

    hard_count = sum(1 for r in rules if r.effective_severity == "HARD")
    soft_count = sum(1 for r in rules if r.effective_severity == "SOFT")
    warn_count = sum(1 for r in rules if r.effective_severity == "WARN")
    safe_count = sum(1 for r in rules if r.effective_severity == "SAFE")
    review_count = sum(1 for r in rules if r.review_only)

    print(f"  Всього правил: {len(rules)}")
    print(f"  HARD: {hard_count}  SOFT: {soft_count}  WARN: {warn_count}  SAFE: {safe_count}")
    print(f"  review_only: {review_count}")

    # Policy conflicts report
    conflicts = []
    for r in rules:
        if r.review_only and r.severity == "HARD":
            conflicts.append(f"  ⚠️  {r.rule_id} ({r.category}) — review каже HARD, downgraded to SOFT")
    if conflicts:
        print("\nPolicy conflicts (auto-resolved):")
        for c in conflicts:
            print(c)

    # Short atom warnings
    print("\nShort/ambiguous atoms (перевір co-occurrence logic):")
    for r in rules:
        for atom in r.regex_atoms:
            clean = re.sub(r'[\\^$.*+?(){}|[\]]', '', atom)
            if len(clean) <= 4 and r.effective_severity not in ("SAFE",):
                print(f"  ⚡ [{r.rule_id}] {repr(atom)} → severity={r.effective_severity} ambiguous={r.ambiguous}")

    print(f"\n📝 Генеруємо {OUT_RULES}...")
    OUT_RULES.write_text(generate_rules_py(rules), encoding="utf-8")
    print(f"   ✅ {OUT_RULES}")

    print(f"📝 Генеруємо {OUT_TESTS}...")
    OUT_TESTS.write_text(generate_tests(rules), encoding="utf-8")
    print(f"   ✅ {OUT_TESTS}")

    print("\n✅ Готово. Наступні кроки:")
    print("   1. Перегляньте rules_generated.py — скопіюйте нові правила в production rules.py")
    print("   2. Запустіть: pytest test_rules_generated.py -v")
    print("   3. Для false positives: pytest test_rules_generated.py -k 'clean_text' -v")


if __name__ == "__main__":
    main()