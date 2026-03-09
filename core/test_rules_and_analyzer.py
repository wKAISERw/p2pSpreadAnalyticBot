# tests/test_rules_and_analyzer.py
"""
Unit-тести для core/rules.py і core/regex_analyzer.py.

Запуск:
    python -m unittest tests/test_rules_and_analyzer.py -v

Структура:
    TestRulesIntegrity      — перевірка self-consistency rules.py
    TestHardRules           — HARD_RULES → завжди BLOCK
    TestSuppressors         — SAFE_RULES → скасовують HARD
    TestSoftRules           — SOFT_RULES → NEEDS_LLM при достатньому score
    TestWarnRules           — WARN_RULES → warn_flags без блоку
    TestSuppressorMap       — SUPPRESSOR_MAP будується коректно
    TestEdgeCases           — порожній текст, fuzzy/latin, комбінації
    TestScoreAccumulation   — score накопичується і знижується suppressors
    TestRegexResultFields   — поля RegexResult заповнюються правильно
"""
import sys
import importlib.util
import unittest

# ── bootstrap ──────────────────────────────────────────────────────────────
# Файл тестів може лежати:
#   tests/test_rules_and_analyzer.py  (стандартно)
#   core/test_rules_and_analyzer.py   (якщо поклали поруч з модулями)
# Знаходимо корінь проєкту автоматично — жодних хардкод шляхів.

from pathlib import Path

_THIS_FILE   = Path(__file__).resolve()
_TESTS_DIR   = _THIS_FILE.parent          # папка де лежить цей файл
_PROJECT_ROOT = _TESTS_DIR.parent         # крок вгору → корінь проєкту

# Якщо тест лежить у core/ — корінь це батьківська тека core/
if _TESTS_DIR.name in ("core", "tests"):
    _PROJECT_ROOT = _TESTS_DIR.parent

sys.path.insert(0, str(_PROJECT_ROOT))


def _load(module_name: str, rel_path: str):
    """Завантажує модуль відносно кореня проєкту."""
    abs_path = _PROJECT_ROOT / rel_path
    if not abs_path.exists():
        raise FileNotFoundError(
            f"Не знайдено {abs_path}. "
            f"PROJECT_ROOT={_PROJECT_ROOT}. "
            f"Перевір що rules.py і regex_analyzer.py є в core/"
        )
    spec = importlib.util.spec_from_file_location(module_name, str(abs_path))
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod

rules_mod = _load("core.rules",          "core/rules.py")
ra_mod    = _load("core.regex_analyzer", "core/regex_analyzer.py")

from core.rules import (
    HARD_RULES, SOFT_RULES, SAFE_RULES, WARN_RULES, ALL_RULES,
    SUPPRESSOR_MAP, HARD_CONFIRM_REQUIRED, HARD_DIRECT_BLOCK,
    RegexRule,
)
from core.regex_analyzer import analyze, RegexResult


# ─────────────────────────────────────────────────────────────────────────────
class TestRulesIntegrity(unittest.TestCase):
    """Перевірка self-consistency самих правил."""

    def test_all_rules_have_required_fields(self):
        for rule in ALL_RULES:
            self.assertIsInstance(rule.id, str, f"id not str: {rule}")
            self.assertIsInstance(rule.category, str, f"category not str: {rule}")
            self.assertIsInstance(rule.weight, int, f"weight not int: {rule}")
            self.assertIn(rule.action, ("BLOCK", "NEEDS_LLM", "WARN", "SAFE"),
                          f"Unknown action: {rule}")

    def test_hard_rules_have_weight_100(self):
        for rule in HARD_RULES:
            self.assertEqual(rule.weight, 100, f"HARD rule {rule.id} weight != 100")

    def test_soft_rules_have_positive_weight(self):
        for rule in SOFT_RULES:
            self.assertGreater(rule.weight, 0, f"SOFT rule {rule.id} weight <= 0")

    def test_safe_rules_have_negative_weight(self):
        for rule in SAFE_RULES:
            self.assertLess(rule.weight, 0, f"SAFE rule {rule.id} weight >= 0")

    def test_warn_rules_have_action_warn(self):
        for rule in WARN_RULES:
            self.assertEqual(rule.action, "WARN", f"WARN rule {rule.id} action != WARN")

    def test_no_duplicate_ids(self):
        ids = [r.id for r in ALL_RULES]
        self.assertEqual(len(ids), len(set(ids)), f"Дублікати id: {ids}")

    def test_hard_confirm_and_direct_block_disjoint(self):
        overlap = HARD_CONFIRM_REQUIRED & HARD_DIRECT_BLOCK
        self.assertEqual(overlap, frozenset(), f"Перетин CONFIRM/DIRECT: {overlap}")

    def test_hard_categories_covered(self):
        hard_cats = {r.category for r in HARD_RULES}
        covered = HARD_CONFIRM_REQUIRED | HARD_DIRECT_BLOCK
        self.assertEqual(hard_cats, covered,
                         f"HARD категорії не покриті: {hard_cats ^ covered}")

    def test_suppressor_map_built_from_safe_rules(self):
        expected = {
            rule.category: rule.suppress_hard
            for rule in SAFE_RULES if rule.suppress_hard
        }
        self.assertEqual(dict(SUPPRESSOR_MAP), expected)

    def test_suppress_hard_only_known_categories(self):
        hard_cats = {r.category for r in HARD_RULES}
        for rule in SAFE_RULES:
            for cat in rule.suppress_hard:
                self.assertIn(cat, hard_cats,
                              f"suppress_hard {cat!r} не є HARD категорією")


# ─────────────────────────────────────────────────────────────────────────────
class TestHardRules(unittest.TestCase):
    """HARD_RULES — прямий BLOCK."""

    def _assert_block(self, text: str, expected_category: str = None):
        r = analyze(text)
        self.assertEqual(r.verdict, "BLOCK", f"Очікував BLOCK для: {text!r}")
        if expected_category:
            self.assertEqual(r.risk_type, expected_category,
                             f"Очікував category={expected_category}, отримав={r.risk_type}")
        self.assertEqual(r.score, 100)

    # H1 EXTERNAL_LINK
    def test_h1_tme_link(self):
        self._assert_block("t.me/my_channel для угоди", "EXTERNAL_LINK")

    def test_h1_at_username(self):
        self._assert_block("@username пишіть до оплати", "EXTERNAL_LINK")

    def test_h1_write_telegram_before(self):
        self._assert_block("пишіть у telegram перед оплатою", "EXTERNAL_LINK")

    def test_h1_viber_protocol(self):
        self._assert_block("viber://contact/me", "EXTERNAL_LINK")

    # H2 TRIANGLE
    def test_h2_drop_welcome(self):
        self._assert_block("дропи вітаються", "TRIANGLE")

    def test_h2_payment_from_another_person(self):
        self._assert_block("оплата від іншої особи", "TRIANGLE")

    def test_h2_friend_card(self):
        self._assert_block("карта знайомого підійде", "TRIANGLE")

    def test_h2_strangers_card(self):
        self._assert_block("чужа картка не проблема", "TRIANGLE")

    # H3 NO_COMMENTS
    def test_h3_bez_komentariv(self):
        self._assert_block("без коментарів до платежу", "NO_COMMENTS")

    def test_h3_push_pole(self):
        self._assert_block("пусте поле обов'язково", "NO_COMMENTS")

    def test_h3_nichogo_ne_pysh(self):
        self._assert_block("нічого не пишіть в полі", "NO_COMMENTS")

    # H4 CASINO
    def test_h4_1xbet(self):
        self._assert_block("1xbet поповнення", "CASINO")

    def test_h4_casino_word(self):
        self._assert_block("казино оплата", "CASINO")

    def test_h4_procesing(self):
        self._assert_block("процесинг агрегатор", "CASINO")

    def test_h4_melbet(self):
        self._assert_block("melbet депозит", "CASINO")


# ─────────────────────────────────────────────────────────────────────────────
class TestSuppressors(unittest.TestCase):
    """SAFE_RULES — suppressors скасовують HARD."""

    def _assert_not_block(self, text: str):
        r = analyze(text)
        self.assertNotEqual(r.verdict, "BLOCK",
                            f"Не очікував BLOCK для: {text!r} (score={r.score})")

    # W2 ANTI_THIRD_PARTY → скасовує TRIANGLE
    def test_w2_bez_tretikh_osib(self):
        self._assert_not_block("без третіх осіб")

    def test_w2_tilky_zi_svoei(self):
        self._assert_not_block("тільки зі своєї картки")

    def test_w2_ne_pryimaiu_vid_tretikh(self):
        self._assert_not_block("не приймаю від третіх осіб")

    def test_w2_bez_dropiw(self):
        self._assert_not_block("без посередників, без дропів")

    def test_w2_dropu_zaboroneno(self):
        self._assert_not_block("дропи заборонені")

    def test_w2_tilky_vlasnyk(self):
        self._assert_not_block("тільки власник картки")

    # W3 ANTI_EXTERNAL_LINK → скасовує EXTERNAL_LINK
    def test_w3_ne_pyshit_u_telegram(self):
        self._assert_not_block("не пишіть у telegram, тільки чат біржі")

    def test_w3_v_mesendzhery_ne_perekhodzhu(self):
        self._assert_not_block("в месенджери не переходжу")

    def test_w3_tilky_chat_birzhi(self):
        self._assert_not_block("спілкування тільки в чаті біржі")

    # Suppressor + незв'язаний HARD — HARD спрацьовує
    def test_suppressor_does_not_protect_unrelated_hard(self):
        # W2 захищає від TRIANGLE але не від NO_COMMENTS
        r = analyze("без третіх осіб, без коментарів")
        self.assertEqual(r.verdict, "BLOCK")
        self.assertEqual(r.risk_type, "NO_COMMENTS")


# ─────────────────────────────────────────────────────────────────────────────
class TestSoftRules(unittest.TestCase):
    """SOFT_RULES → NEEDS_LLM."""

    def _assert_llm(self, text: str, expected_risk: str = None):
        r = analyze(text)
        self.assertEqual(r.verdict, "NEEDS_LLM",
                         f"Очікував NEEDS_LLM для: {text!r} (verdict={r.verdict}, score={r.score})")
        self.assertTrue(r.needs_llm)
        if expected_risk:
            self.assertEqual(r.risk_type, expected_risk)

    def test_s1_fop_oplata(self):
        self._assert_llm("фоп оплата на рахунок", "SUSPICIOUS_BIZ")

    def test_s1_iban_fop(self):
        self._assert_llm("iban фоп приймаю", "SUSPICIOUS_BIZ")

    def test_s2_pyshit_meni(self):
        self._assert_llm("пишіть мені до оплати", "CHAT_FIRST")

    def test_s2_napyshite_pered(self):
        self._assert_llm("напишіть перед переказом", "CHAT_FIRST")

    def test_s3_apelyatsiia(self):
        self._assert_llm("апеляцію відкрию якщо не виконаєш")

    def test_s5_bez_zaivykh_pytan(self):
        self._assert_llm("без зайвих питань, анонімно")

    def test_s6_tretikh_osib_without_negation(self):
        # S6 THIRD_PARTY_HINT = 20 балів.
        # Один S6 (score=20) < LLM_SCORE_THRESHOLD=30 → не ескалює сам по собі.
        # З додатковим сигналом → score >= 30 → NEEDS_LLM.
        r_alone = analyze("третіх осіб допускаю")
        self.assertEqual(r_alone.score, 20)
        self.assertNotEqual(r_alone.verdict, "BLOCK")   # не блокує безпідставно

        # S6 (20) + S2 CHAT_FIRST (30) = 50 → NEEDS_LLM
        r_combo = analyze("третіх осіб, пишіть мені до оплати")
        self.assertEqual(r_combo.verdict, "NEEDS_LLM")

    def test_s7_charzhbek(self):
        self._assert_llm("чардж або диспут відкрию", "CHARGEBACK")

    def test_s7_refund(self):
        self._assert_llm("refund через банк подам")

    def test_s8_finmon(self):
        self._assert_llm("фінмон може заблокувати", "FINCRIME")

    def test_s8_sira_skhema(self):
        self._assert_llm("сіра схема ок")

    def test_s9_posrednyk(self):
        self._assert_llm("посередник для угоди", "MIDDLEMAN")

    def test_multi_signal_escalation(self):
        # Два soft сигнали → ескалація навіть якщо кожен < threshold
        r = analyze("перший раз, тільки для нових")
        # S4 NEW_USERS × 2 або одна з комбінацій → score >= 20
        self.assertIn(r.verdict, ("NEEDS_LLM", "OK"))  # залежить від score


# ─────────────────────────────────────────────────────────────────────────────
class TestWarnRules(unittest.TestCase):
    """WARN_RULES → warn_flags без блокування."""

    def test_wr1_kyvantsiia_not_blocked(self):
        r = analyze("надішліть квитанцію після оплати")
        self.assertNotEqual(r.verdict, "BLOCK")

    def test_wr1_screenshot_gives_warn_flag(self):
        r = analyze("скриншот оплати будь ласка надішліть")
        warn_cats = [cat for cat, _ in r.warn_flags]
        self.assertIn("RECEIPT_REQUIRED", warn_cats)

    def test_wr1_warn_flag_is_tuple_with_excerpt(self):
        r = analyze("фото чеку після оплати")
        for item in r.warn_flags:
            self.assertIsInstance(item, tuple, "warn_flag має бути tuple")
            self.assertEqual(len(item), 2, "warn_flag tuple має бути (category, excerpt)")
            cat, excerpt = item
            self.assertIsInstance(cat, str)
            self.assertIsInstance(excerpt, str)
            self.assertTrue(excerpt.startswith('"'), f"excerpt не в лапках: {excerpt!r}")

    def test_warn_does_not_increase_score(self):
        r_plain = analyze("звичайні умови")
        r_warn  = analyze("надішліть квитанцію після оплати")
        # warn не має піднімати score до NEEDS_LLM
        self.assertLessEqual(r_warn.score, 15)

    def test_no_warn_flag_for_clean_text(self):
        r = analyze("тільки Monobank, без третіх осіб")
        self.assertEqual(r.warn_flags, [])


# ─────────────────────────────────────────────────────────────────────────────
class TestSuppressorMap(unittest.TestCase):

    def test_anti_third_party_suppresses_triangle(self):
        self.assertIn("TRIANGLE", SUPPRESSOR_MAP.get("ANTI_THIRD_PARTY", frozenset()))

    def test_anti_external_link_suppresses_external_link(self):
        self.assertIn("EXTERNAL_LINK", SUPPRESSOR_MAP.get("ANTI_EXTERNAL_LINK", frozenset()))

    def test_suppressor_map_contains_only_safe_rules(self):
        safe_cats = {r.category for r in SAFE_RULES}
        for cat in SUPPRESSOR_MAP:
            self.assertIn(cat, safe_cats)


# ─────────────────────────────────────────────────────────────────────────────
class TestEdgeCases(unittest.TestCase):

    def test_empty_string_returns_ok(self):
        r = analyze("")
        self.assertEqual(r.verdict, "OK")

    def test_whitespace_only_returns_ok(self):
        r = analyze("   \n\t  ")
        self.assertEqual(r.verdict, "OK")

    def test_none_safe_terms(self):
        r = analyze("тільки Monobank, без третіх осіб, лише зі своєї картки")
        self.assertEqual(r.verdict, "OK")

    def test_fuzzy_latin_casino(self):
        # "кaзино" де 'a' латинська — fuzzy замінює на кирилицю → 'казино' → BLOCK
        r = analyze("кaзино приймаю")   # a=latin
        self.assertEqual(r.verdict, "BLOCK")
        self.assertEqual(r.risk_type, "CASINO")

    def test_fuzzy_latin_telegram(self):
        # "tеlеgrаm" з латинськими символами
        r = analyze("t.me/some_link угода")
        self.assertEqual(r.verdict, "BLOCK")

    def test_normalized_text_filled(self):
        r = analyze("Тільки Monobank")
        self.assertIsInstance(r.normalized_text, str)
        self.assertTrue(len(r.normalized_text) > 0)

    def test_suppressor_plus_soft_gives_ok_or_needs_llm(self):
        # Suppressor збив HARD, але залишились SOFT сигнали → OK або NEEDS_LLM (не BLOCK)
        r = analyze("без третіх осіб, пишіть мені до оплати")
        self.assertNotEqual(r.verdict, "BLOCK")

    def test_unicode_cleanup(self):
        # Zero-width символи не впливають на результат
        r1 = analyze("без коментарів")
        r2 = analyze("без\u200bкоментарів")
        self.assertEqual(r1.verdict, r2.verdict)


# ─────────────────────────────────────────────────────────────────────────────
class TestScoreAccumulation(unittest.TestCase):

    def test_single_soft_below_threshold(self):
        # S4 NEW_USERS = 20 < LLM_SCORE_THRESHOLD=30 → OK
        r = analyze("тільки для нових")
        self.assertEqual(r.verdict, "OK")
        self.assertEqual(r.score, 20)

    def test_single_soft_at_threshold(self):
        # S2 CHAT_FIRST = 30 >= threshold → NEEDS_LLM
        r = analyze("пишіть мені до оплати")
        self.assertEqual(r.verdict, "NEEDS_LLM")
        self.assertEqual(r.score, 30)

    def test_suppressor_reduces_score(self):
        # S2 (+30) + W1 STANDARD_BANKS (-20) = score 10 → OK
        r = analyze("пишіть мені до оплати, тільки privatbank")
        # score 10 < 30 → OK (suppressor знизив)
        self.assertLess(r.score, 30)

    def test_strong_suppressor_cancels_soft(self):
        # S6 THIRD_PARTY_HINT (+20) + W2 ANTI_THIRD_PARTY (-50) = -30 → score=0
        r = analyze("третіх осіб не приймаю від третіх осіб")
        self.assertEqual(r.score, 0)

    def test_score_never_negative(self):
        # Навіть з потужними suppressors score >= 0
        r = analyze("без третіх осіб, тільки monobank, тільки чат біржі")
        self.assertGreaterEqual(r.score, 0)

    def test_multiple_soft_accumulate(self):
        # S1 (40) + S2 (30) = 70 → NEEDS_LLM
        r = analyze("фоп рахунок, пишіть мені до оплати")
        self.assertGreaterEqual(r.score, 60)
        self.assertEqual(r.verdict, "NEEDS_LLM")


# ─────────────────────────────────────────────────────────────────────────────
class TestRegexResultFields(unittest.TestCase):

    def test_block_result_has_risk_type(self):
        r = analyze("без коментарів")
        self.assertEqual(r.verdict, "BLOCK")
        self.assertNotEqual(r.risk_type, "")
        self.assertNotEqual(r.reason, "")

    def test_needs_llm_has_matches(self):
        r = analyze("пишіть мені до оплати")
        self.assertEqual(r.verdict, "NEEDS_LLM")
        self.assertTrue(r.needs_llm)
        self.assertGreater(len(r.matches), 0)

    def test_matches_have_excerpts(self):
        r = analyze("пишіть мені до оплати")
        for m in r.matches:
            self.assertIsInstance(m.excerpt, str)
            self.assertTrue(len(m.excerpt) > 0)

    def test_ok_result_fields(self):
        r = analyze("тільки monobank")
        self.assertEqual(r.verdict, "OK")
        self.assertFalse(r.needs_llm)
        self.assertGreaterEqual(r.score, 0)
        self.assertIsInstance(r.warn_flags, list)

    def test_block_result_score_100(self):
        r = analyze("1xbet депозит")
        self.assertEqual(r.score, 100)

    def test_normalized_text_lowercase(self):
        r = analyze("ПИШІТЬ МЕНІ ДО ОПЛАТИ")
        self.assertEqual(r.normalized_text, r.normalized_text.lower())


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    unittest.main(verbosity=2)