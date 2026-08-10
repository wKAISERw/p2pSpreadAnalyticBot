"""
tests/test_terms_status.py

«Умови не вказані» проти «умов не видно».

Порожній рядок у `trade_terms` означав дві протилежні речі: мерчант нічого
не написав (факт про мерчанта) і ми не змогли дістати (факт про нас). В
алерті обидва виглядали як «Умови не вказані», і людина робила висновок про
контрагента там, де насправді бачила протухлу сесію.

Окремо перевіряється те, що виявилось найгіршим: Wallet і CryptoBot
намагались розрізняти ці випадки, але писали причину ВСЕРЕДИНУ поля умов —
і `risk_engine` проганяв службове речення через регекси й віддавав LLM як
слова мерчанта.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.engine import terms_status as ts


# ═══════════════════════════════════════════════════════════════
# Ключ відсутній ≠ ключ порожній
# ═══════════════════════════════════════════════════════════════

def test_present_and_filled_is_ok():
    text, status = ts.from_payload({"remarks": "Тільки одним платежем"}, "remarks")
    assert text == "тільки одним платежем"
    assert status == ts.OK


def test_present_but_empty_is_the_merchants_choice():
    """Ключ є, значення порожнє — мерчант справді нічого не написав."""
    text, status = ts.from_payload({"remarks": ""}, "remarks")
    assert text == ""
    assert status == ts.EMPTY
    assert not ts.is_blind(status), "це факт про мерчанта, не про нас"


def test_missing_key_is_unknown_not_empty():
    """
    Ключа немає зовсім — ми не бачили умов. Раніше `.get(key, "")` робив із
    цього таку саму порожнечу, як і з реально порожніх умов.
    """
    text, status = ts.from_payload({"price": "44.5"}, "remarks")
    assert text == ""
    assert status == ts.UNKNOWN
    assert ts.is_blind(status)


def test_first_present_key_wins():
    text, status = ts.from_payload({"remark": "друге"}, "tradeTerms", "remark")
    assert text == "друге"
    assert status == ts.OK


def test_garbage_payload_is_unknown():
    assert ts.from_payload(None, "remarks") == ("", ts.UNKNOWN)
    assert ts.from_payload("рядок", "remarks") == ("", ts.UNKNOWN)


def test_only_empty_is_not_blind():
    assert not ts.is_blind(ts.EMPTY)
    assert not ts.is_blind(ts.OK)
    for status in (ts.NO_SESSION, ts.SESSION_EXPIRED, ts.FETCH_FAILED,
                   ts.NOT_SUPPORTED, ts.UNKNOWN):
        assert ts.is_blind(status), status


# ═══════════════════════════════════════════════════════════════
# Парсери бірж
# ═══════════════════════════════════════════════════════════════

def test_okx_missing_block_is_not_reported_as_no_terms():
    """
    Саме випадок зі скріна користувача: OKX не завжди кладе
    tradingOrderInfo у список ордерів, і бот писав «Умови не вказані».
    """
    from exchanges.okx import OkxExchange

    order = OkxExchange(client=None)._parse_order({
        "id": "o1", "price": "46.03", "availableAmount": "1753",
        "quoteMinAmountPerOrder": "3000", "quoteMaxAmountPerOrder": "33000",
        "publicUserId": "u1", "nickName": "Misha_p2p",
        "completedOrderQuantity": 1055, "completedRate": "0.95",
        "paymentMethods": [{"bankName": "ПУМБ"}],
    })

    assert order.trade_terms == ""
    assert order.terms_status == ts.UNKNOWN


def test_okx_present_but_empty_block_means_merchant_said_nothing():
    from exchanges.okx import OkxExchange

    order = OkxExchange(client=None)._parse_order({
        "id": "o1", "price": "46.03", "availableAmount": "1753",
        "quoteMinAmountPerOrder": "3000", "quoteMaxAmountPerOrder": "33000",
        "publicUserId": "u1", "nickName": "M",
        "completedOrderQuantity": 10, "completedRate": "0.99",
        "paymentMethods": [],
        "tradingOrderInfo": {"tradeOrderDesc": ""},
    })
    assert order.terms_status == ts.EMPTY


def test_bybit_terms_status_is_set():
    from exchanges.bybit import BybitExchange

    ex = BybitExchange(client=None)
    with_terms = ex._parse_order({
        "id": "1", "price": "44", "lastQuantity": "10", "minAmount": "100",
        "maxAmount": "200", "userId": "u", "nickName": "M",
        "recentOrderNum": 1, "recentExecuteRate": 99,
        "payments": [], "remark": "Без третіх осіб",
    })
    assert with_terms.terms_status == ts.OK
    assert with_terms.trade_terms == "без третіх осіб"

    without = ex._parse_order({
        "id": "2", "price": "44", "lastQuantity": "10", "minAmount": "100",
        "maxAmount": "200", "userId": "u", "nickName": "M",
        "recentOrderNum": 1, "recentExecuteRate": 99, "payments": [],
    })
    assert without.terms_status == ts.UNKNOWN


# ═══════════════════════════════════════════════════════════════
# Найгірше: причина, записана в поле даних
# ═══════════════════════════════════════════════════════════════

def test_failure_reason_never_lands_in_the_terms_text():
    """
    Wallet і CryptoBot писали «не вдалося отримати доступ до умов через
    технічну помилку сесії» прямо в trade_terms. Це речення потім
    проганялось регексами risk_engine і йшло в промпт LLM як слова
    мерчанта.
    """
    import inspect

    from exchanges import cryptobot_web, wallet

    for module in (wallet, cryptobot_web):
        source = inspect.getsource(module)
        assert "trade_terms = \"не вдалося" not in source, module.__name__
        assert "terms_status" in source, module.__name__


# ═══════════════════════════════════════════════════════════════
# Подача в алерті
# ═══════════════════════════════════════════════════════════════

def test_blind_status_says_why_instead_of_staying_silent():
    from bot.formatters import _terms_block

    block = _terms_block("", terms_status=ts.NO_SESSION)
    assert "Умов не видно" in block
    assert "немає сесії" in block
    assert "не означає, що умов немає" in block


def test_empty_terms_stay_silent_as_before():
    """Мерчант нічого не написав — окремий блок тут зайвий."""
    from bot.formatters import _terms_block

    assert _terms_block("", terms_status=ts.EMPTY) == ""
    assert _terms_block("", terms_status="") == ""


def test_real_terms_are_shown_regardless_of_status():
    from bot.formatters import _terms_block

    block = _terms_block("тільки одним платежем", terms_status=ts.OK)
    assert "одним платежем" in block


# ═══════════════════════════════════════════════════════════════
# Прапорець ризику
# ═══════════════════════════════════════════════════════════════

def test_risk_flag_carries_the_reason():
    from bot.formatters import UNKNOWN_REASONS

    for status in (ts.NO_SESSION, ts.SESSION_EXPIRED, ts.FETCH_FAILED,
                   ts.UNKNOWN, ts.EMPTY, ts.NOT_SUPPORTED):
        assert status in UNKNOWN_REASONS, f"{status} нічим показати людині"
