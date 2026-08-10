"""
tests/test_bank_discovery.py

Банки, які біржі віддають, а реєстр не знає.

З'явилось із живого питання «а що таке код 545?». Відповідь не потребувала
здогадок: біржа присилає назву методу оплати в тому самому payload, з якого
береться код, — просто парсери її викидали.

Наслідки різні й обидва тихі: Bybit кладе сирий код і той стає
псевдобанком (у статистиці це виглядає як «немає активних карток»), а
Binance і OKX невідоме мовчки викидають — ордер лишається з коротшим
списком банків і може відпасти як «не той банк».
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.engine import bank_discovery


@pytest.fixture(autouse=True)
def _clean():
    bank_discovery.reset()
    yield
    bank_discovery.reset()


# ═══════════════════════════════════════════════════════════════
# Витягування назви
# ═══════════════════════════════════════════════════════════════

def test_name_is_taken_from_the_payload():
    assert bank_discovery.extract_name({"paymentName": "Sense Bank"}) == "Sense Bank"
    assert bank_discovery.extract_name({"bankName": "ПУМБ"}) == "ПУМБ"
    assert bank_discovery.extract_name({"tradeMethodName": "Monobank"}) == "Monobank"
    assert bank_discovery.extract_name("Raiffeisen") == "Raiffeisen"


def test_bybit_hides_the_name_one_level_deeper():
    payload = {"paymentType": "545", "paymentConfigVo": {"paymentName": "Абанк"}}
    assert bank_discovery.extract_name(payload) == "Абанк"


def test_no_name_is_not_invented():
    assert bank_discovery.extract_name({"paymentType": "545"}) == ""
    assert bank_discovery.extract_name(None) == ""
    assert bank_discovery.extract_name(42) == ""


# ═══════════════════════════════════════════════════════════════
# Накопичення
# ═══════════════════════════════════════════════════════════════

def test_code_is_remembered_with_its_name():
    bank_discovery.note("Bybit", "545", {"paymentConfigVo": {"paymentName": "Абанк"}})
    bank_discovery.note("Bybit", "545", {"paymentConfigVo": {"paymentName": "Абанк"}})

    found = bank_discovery.discovered()
    assert len(found) == 1
    assert found[0].code == "545"
    assert found[0].exchange == "Bybit"
    assert found[0].hits == 2
    assert found[0].title == "Абанк"


def test_same_code_on_different_exchanges_stays_separate():
    """Код 545 на Bybit і 545 на MEXC — різні банки, зливати їх не можна."""
    bank_discovery.note("Bybit", "545", {"paymentName": "Один"})
    bank_discovery.note("MEXC", "545", {"paymentName": "Інший"})

    assert len(bank_discovery.discovered()) == 2


def test_code_without_a_name_is_still_tracked():
    bank_discovery.note("Bybit", "545", {"paymentType": "545"})

    found = bank_discovery.discovered()[0]
    assert found.hits == 1
    assert found.title == "назва невідома", "не вигадуємо назву"


def test_most_frequent_first():
    for _ in range(5):
        bank_discovery.note("Bybit", "545")
    bank_discovery.note("Bybit", "999")

    assert [u.code for u in bank_discovery.discovered()] == ["545", "999"]


def test_tracking_is_bounded():
    """Зламаний payload не має роздути словник на весь стакан."""
    for i in range(bank_discovery._MAX_TRACKED + 50):
        bank_discovery.note("Bybit", f"code{i}")

    assert len(bank_discovery.discovered()) == bank_discovery._MAX_TRACKED


def test_empty_code_is_ignored():
    bank_discovery.note("Bybit", "", {"paymentName": "X"})
    bank_discovery.note("Bybit", None)
    assert bank_discovery.discovered() == []


# ═══════════════════════════════════════════════════════════════
# Подача
# ═══════════════════════════════════════════════════════════════

def test_render_is_empty_when_registry_covers_everything():
    assert bank_discovery.render() == ""


def test_render_names_code_exchange_and_title():
    bank_discovery.note("Bybit", "545", {"paymentConfigVo": {"paymentName": "Абанк"}})
    text = bank_discovery.render()

    assert "545" in text
    assert "Bybit" in text
    assert "Абанк" in text
    assert "config/banks.py" in text, "має бути видно, куди це додається"


# ═══════════════════════════════════════════════════════════════
# Парсери бірж справді це кличуть
# ═══════════════════════════════════════════════════════════════

def test_bybit_parser_reports_unmapped_code():
    """
    Ключовий випадок: Bybit кладе сирий paymentType. Код лишається в
    bank_codes (щоб не змінювати матчинг), але тепер він ще й помічений.
    """
    from exchanges.bybit import BybitExchange

    ex = BybitExchange(client=None)
    order = ex._parse_order({
        "id": "x1", "price": "44.5", "lastQuantity": "100",
        "minAmount": "1000", "maxAmount": "5000",
        "userId": "u1", "nickName": "M", "recentOrderNum": 10,
        "recentExecuteRate": 99,
        "payments": [
            {"paymentType": "43"},
            {"paymentType": "545", "paymentConfigVo": {"paymentName": "Абанк"}},
        ],
    })

    assert "545" in order.bank_codes, "поведінка матчингу не змінилась"
    found = bank_discovery.discovered()
    assert [u.code for u in found] == ["545"], "43 у реєстрі є, він не невідомий"
    assert found[0].title == "Абанк"


def test_binance_parser_reports_dropped_method():
    """У Binance невідомий метод випадав зі списку банків мовчки."""
    from exchanges.binance import BinanceExchange

    ex = BinanceExchange(client=None)
    ex._parse_order({
        "adv": {
            "advNo": "a1", "price": "44.5", "surplusAmount": "100",
            "minSingleTransAmount": "1000", "maxSingleTransAmount": "5000",
            "tradeMethods": [
                {"identifier": "Monobank", "tradeMethodName": "Monobank"},
                {"identifier": "SomeNewBank", "tradeMethodName": "Новий Банк"},
            ],
        },
        "advertiser": {"userNo": "u1", "nickName": "M", "monthOrderCount": 10,
                       "monthFinishRate": 0.99},
    }, "43")

    found = bank_discovery.discovered()
    assert [u.code for u in found] == ["SomeNewBank"]
    assert found[0].title == "Новий Банк"
