import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from itertools import combinations

from config.banks import (
    bank_display_name, get_bank_profile, is_unmapped_code, normalize_bank,
)
from config.card_limits import (
    SPLIT_INTER_BANK, SPLIT_INTRA_BANK, SPLIT_OFF, night_cap_for_bank,
)
from core.engine import rejection_codes as rc
from core.engine.rejection_codes import _uah
from core.engine.terms_signals import mentions_iban
from core.storage.merchant_db import MerchantDB

logger = logging.getLogger(__name__)

@dataclass
class CardMatchResult:
    status: str  # "success", "needs_split", "no_cards", "disabled", "no_crypto"
    best_card: Optional[Dict] = None
    split_options: List[List[Dict]] = field(default_factory=list)
    rejection_report: List[Dict] = field(default_factory=list)
    # Скільки картки цього банку разом можуть провести під цей напрямок.
    # Потрібне тому, хто хоче підігнати суму під наявне, а не просто почути
    # «не вийшло». Тейкер саме це й робив — але читав неіснуюче поле
    # `available_balance` через getattr із дефолтом 0, тож підгонка не
    # спрацьовувала жодного разу і мовчки.
    available_uah: float = 0.0
    # Банки маршруту, яких мерчант не вказував в оголошенні. Порожньо —
    # маршрут повністю в межах заявленого; інакше з мерчантом треба
    # домовитись у чаті, перш ніж переказувати.
    needs_confirmation_banks: List[str] = field(default_factory=list)

class CardMatchingEngine:
    def __init__(self, db: MerchantDB):
        self.db = db

    async def run(
        self, 
        user_id: int, 
        bank: str, 
        amount: float, 
        direction: str,  # "buy", "sell", "spread"
        crypto_available: bool = True,
        excluded_cards: Optional[List[str]] = None,
        # Умови мерчанта. Потрібні рівно для одного: зрозуміти, чи піде
        # переказ по IBAN — бо частина банків відправляє IBAN лише в робочі
        # дні, тоді як картка на картку в них іде щодня.
        trade_terms: str = "",
        # Усі банки, з яких дозволено брати картки. None — лише `bank`, тобто
        # поточна поведінка. Перший елемент вважається банком мерчанта.
        banks: Optional[List[str]] = None,
        # Банки, які мерчант справді вказав в оголошенні. Усе поза цим
        # набором потребує узгодження в чаті — і має бути позначене.
        declared_banks: Optional[List[str]] = None,
    ) -> CardMatchResult:

        settings = await self.db.get_user_card_settings(user_id)
        if not settings or settings.get("card_module_mode") == "off":
            return CardMatchResult(status="disabled")

        if direction in ("sell", "spread") and not crypto_available:
            return CardMatchResult(
                status="no_crypto",
                rejection_report=[rc.Rejection(rc.NO_CRYPTO, rc.label(rc.NO_CRYPTO)).as_dict()],
            )

        if direction == "spread":
            buy_res = await self._run_single(user_id, bank, amount, "buy", settings, excluded_cards, trade_terms, banks)
            sell_res = await self._run_single(user_id, bank, amount, "sell", settings, excluded_cards, trade_terms, banks)
            if buy_res.status == "success" and sell_res.status == "success":
                return CardMatchResult(
                    status="success",
                    best_card={"buy": buy_res.best_card, "sell": sell_res.best_card}
                )
            return CardMatchResult(status="no_cards", rejection_report=buy_res.rejection_report + sell_res.rejection_report)

        result = await self._run_single(
            user_id, bank, amount, direction, settings, excluded_cards, trade_terms, banks
        )
        return self._mark_undeclared_banks(result, bank, declared_banks)

    @staticmethod
    def _mark_undeclared_banks(result: CardMatchResult, bank: str,
                               declared_banks: Optional[List[str]]) -> CardMatchResult:
        """
        Позначає банки маршруту, яких мерчант не вказував в оголошенні.

        Мерчанти часто перелічують не все, що приймають, тож такий маршрут
        не помилковий — але й не узгоджений. Рішення за людиною, і вона має
        побачити, про що саме питати в чаті, ПЕРЕД тим як тиснути «взяти».
        """
        if declared_banks is None:
            return result

        # Рівно те, що мерчант написав в оголошенні. Основний банк маршруту
        # сюди НЕ додаємо: коли фільтр банків мерчанта проігноровано, саме
        # він і може бути незаявленим — а це головний випадок, заради якого
        # позначка існує.
        declared = {normalize_bank(b) for b in declared_banks if b}

        used: List[str] = []
        if result.best_card and isinstance(result.best_card, dict):
            used.append(result.best_card.get("_bank")
                        or normalize_bank(result.best_card.get("bank_name", "")))
        for option in result.split_options:
            for leg in option:
                used.append(leg.get("bank", ""))

        result.needs_confirmation_banks = sorted(
            {b for b in used if b and b not in declared}
        )
        return result

    async def _run_single(self, user_id: int, bank: str, amount: float, direction: str, settings: dict,
                          excluded_cards: Optional[List[str]] = None, trade_terms: str = "",
                          banks: Optional[List[str]] = None) -> CardMatchResult:
        # Ліміти беруться по кожній картці окремо (get_card_effective_limits
        # нижче). Тут колись лежала ще одна копія спільних дефолтів, яку
        # нікуди не передавали — вона нічого не вирішувала й нікуди не йшла.

        # Набір банків, з яких можна брати картки. Один — поточна поведінка;
        # кілька — міжбанківський кошик (етап 3 плану). Дублікати прибираємо,
        # порядок зберігаємо: перший банк — той, який вказав мерчант, і саме
        # він лишається «рідним» для маршруту.
        bank_list: List[str] = []
        for b in (banks or [bank]):
            nb = normalize_bank(b)
            if nb and nb not in bank_list:
                bank_list.append(nb)
        if not bank_list:
            bank_list = [normalize_bank(bank)]

        # Нічні вікна й «тільки робочі дні» — властивості БАНКУ, тож рахуємо
        # їх по кожному окремо, а не один раз на весь прохід.
        is_weekend = datetime.date.today().weekday() >= 5
        terms_want_iban = mentions_iban(trade_terms)
        bank_night_cap = {b: night_cap_for_bank(b) for b in bank_list}
        bank_iban_block = {
            b: (get_bank_profile(b).business_days_only and terms_want_iban and is_weekend)
            for b in bank_list
        }

        async def _fetch_cards() -> List[Dict]:
            collected: List[Dict] = []
            for b in bank_list:
                rows = await self.db.get_cards(user_id, bank_name=b, status="active")
                for row in rows:
                    row["_bank"] = b
                    collected.append(row)
            if excluded_cards:
                collected = [c for c in collected if c["id"] not in excluded_cards]
            return collected

        all_cards = await _fetch_cards()

        if not all_cards:
            # Невідомий код банку — це не «немає картки», а діра в реєстрі:
            # під код, якого система не знає, картка не знайдеться ніколи.
            # Плутати ці дві причини в статистиці означає шукати проблему не
            # там: у першому випадку треба додати банк у config/banks.py, у
            # другому — картку.
            if is_unmapped_code(bank):
                return CardMatchResult(
                    status="no_cards",
                    rejection_report=[rc.unknown_bank_code(bank).as_dict()],
                )
            return CardMatchResult(
                status="no_cards",
                rejection_report=[
                    rc.Rejection(rc.NO_ACTIVE_CARDS, rc.label(rc.NO_ACTIVE_CARDS), bank=bank).as_dict()
                ],
            )

        for card in all_cards:
            await self.db.lazy_monthly_reset(card["id"])

        all_cards = await _fetch_cards()

        passed_cards = []
        requires_split_cards = []
        rejections = []

        card_settings = await self.db.get_user_card_settings(user_id)
        cold_card_limit = float(card_settings.get("cold_card_limit", 2000.0)) if card_settings else 2000.0

        for card in all_cards:
            reason: Optional[rc.Rejection] = None
            card_bank = card.get("_bank") or normalize_bank(card.get("bank_name", ""))
            where = {"card_id": card["id"], "last_four": card["last_four"], "bank": card_bank}
            night_cap = bank_night_cap.get(card_bank)
            iban_weekend_block = bank_iban_block.get(card_bank, False)

            # Ліміти рахуються за банком САМОЇ картки, а не за банком ордера:
            # у міжбанківському кошику це різні речі.
            card_limits = await self.db.get_card_effective_limits(card["id"], user_id, card_bank)


            # get_card_effective_limits повертає повний набір полів (довідник
            # банку + налаштування користувача), тож фолбеки з цифрами тут
            # більше не потрібні.
            max_single = card_limits["max_single_tx_in" if direction == "sell" else "max_single_tx_out"]
            if max_single == -1 or max_single == -1.0:
                max_single = float('inf')

            daily_max = card_limits["daily_in_max" if direction == "sell" else "daily_out_max"]
            if daily_max == -1 or daily_max == -1.0:
                daily_max = float('inf')

            monthly_max = card_limits["monthly_in_max" if direction == "sell" else "monthly_out_max"]
            if monthly_max == -1 or monthly_max == -1.0:
                monthly_max = float('inf')

            # Fetch pending amount for this card in the direction of the order
            pending_dir = "out" if direction == "buy" else "in"
            pending_amt = 0.0
            if self.db._db:
                async with self.db._db.execute(
                    """
                    SELECT SUM(l.amount) as total
                    FROM card_order_legs l
                    JOIN card_orders o ON l.order_id = o.id
                    WHERE l.card_id = ? AND l.leg_status = 'pending' AND o.direction = ?
                    """,
                    (card["id"], pending_dir)
                ) as cur:
                    row = await cur.fetchone()
                    if row and row["total"]:
                        pending_amt = float(row["total"])

            # Adjust card balance for check
            effective_balance = float(card["balance"])
            if direction == "buy":
                effective_balance = max(0.0, effective_balance - pending_amt)

            if card["cooldown_until"] > time.time():
                mins_left = max(1, int((card["cooldown_until"] - time.time()) / 60) + 1)
                reason = rc.cooldown(mins_left, **where)

            if not reason and direction == "buy" and effective_balance < amount:
                if effective_balance > 0:
                    pass # Could be used for split
                else:
                    reason = rc.insufficient_balance(amount, effective_balance, **where)

            if not reason and iban_weekend_block:
                reason = rc.business_days_only(bank_display_name(card_bank), **where)

            if not reason and night_cap == 0.0:
                reason = rc.night_window(0.0, **where)

            if not reason:
                tx_count = await self.db.get_card_transactions_count(card["id"], hours=24)
                max_tx = card_limits["max_tx_per_day"]
                if max_tx != -1 and max_tx != -1.0 and tx_count >= max_tx:
                    reason = rc.max_tx_per_day(tx_count, max_tx, **where)
                else:
                    card["_tx_count"] = tx_count
            
            if not reason:
                now = time.time()
                tx_count_total, last_tx_ts = await self.db.get_card_warmth_stats(card["id"])
                warmup_limit = self.db.get_card_warmup_limit(card, tx_count_total, last_tx_ts, now, cold_card_limit)
                
                is_warm = (warmup_limit is None)
                card["_is_warm"] = is_warm

                used_daily = await self.db.get_rolling_used(card["id"], direction, hours=24)
                # Місячний ліміт банк обнуляє 1-го числа, а не через 30 днів.
                used_monthly = await self.db.get_monthly_used(card["id"], direction)
                
                avail_daily = daily_max - used_daily if daily_max != float('inf') else float('inf')
                avail_monthly = monthly_max - used_monthly if monthly_max != float('inf') else float('inf')
                
                # Apply pending amounts to limits
                avail_daily = max(0.0, avail_daily - pending_amt)
                avail_monthly = max(0.0, avail_monthly - pending_amt)
                
                # Uncapped — без обмеження max_single_tx (для внутрішнього спліту B6)
                max_avail_uncapped = min(avail_daily, avail_monthly)
                if direction == "buy":
                    max_avail_uncapped = min(max_avail_uncapped, effective_balance)
                
                if warmup_limit is not None:
                    max_avail_uncapped = min(max_avail_uncapped, warmup_limit)

                # Нічне вікно банку. Стелю ставимо і на разовий переказ, і на
                # весь доступний обсяг: інакше спліт розписав би десяток
                # нічних переказів по 5к, чого банк не проведе.
                if night_cap:
                    max_avail_uncapped = min(max_avail_uncapped, night_cap)
                    max_single = min(max_single, night_cap)

                max_avail = min(max_avail_uncapped, max_single)
                
                if warmup_limit is not None and amount > warmup_limit:
                    reason = rc.cold_card(warmup_limit, amount, **where)

                if not reason and max_avail <= 0:
                    reason = rc.limits_exhausted(max_avail, amount, **where)
                else:
                    card["_max_avail"] = max_avail
                    card["_max_avail_uncapped"] = max_avail_uncapped
                    card["_max_single"] = max_single

            if reason:
                rejections.append(reason.as_dict())
            else:
                if card["_max_avail"] >= amount:
                    passed_cards.append(card)
                else:
                    requires_split_cards.append(card)

        # Скільки картки цього банку разом можуть провести. Нескінченність
        # (усі ліміти зняті на sell-напрямку) свідомо зводимо до нуля:
        # «безмежно» не є сумою, під яку можна підігнати угоду.
        available_uah = sum(
            float(c.get("_max_avail", 0.0)) for c in passed_cards + requires_split_cards
        )
        if available_uah == float("inf"):
            available_uah = 0.0

        if not passed_cards and not requires_split_cards:
            return CardMatchResult(status="no_cards", rejection_report=rejections)

        if passed_cards:
            best = self._score_and_pick(passed_cards, amount)
            return CardMatchResult(status="success", best_card=best,
                                   rejection_report=rejections, available_uah=available_uah)

        # Спліт вимкнено — це свідомий вибір користувача, а не збій.
        # «Одна картка, один переказ»: мерчанти часто пишуть «тільки одним
        # платежем», і три перекази через три застосунки за 15 хвилин
        # таймера — реальний ризик апеляції.
        split_mode = settings.get("card_split_mode") or SPLIT_INTRA_BANK
        if split_mode == SPLIT_OFF:
            return CardMatchResult(
                status="no_cards", available_uah=available_uah,
                rejection_report=rejections + [
                    rc.Rejection(
                        rc.SPLIT_DISABLED,
                        f"Жодна картка не тягне {amount:,.0f} ₴ повністю, "
                        f"а спліт вимкнено в налаштуваннях".replace(",", " "),
                        bank=bank,
                        shortfall_uah=max(0.0, amount - available_uah),
                    ).as_dict()
                ],
            )

        try:
            max_cards_split = max(1, min(3, int(settings.get("max_cards_per_order"))))
        except (TypeError, ValueError):
            max_cards_split = 3

        # Ранжування маршрутів із 6.3 плану: 1 картка > N карток одного банку
        # > N карток різних банків. Спершу пробуємо зібрати в межах кожного
        # банку окремо — це не лише менше переказів, а й менше пояснень із
        # мерчантом. Міжбанківський кошик — останній варіант.
        splits: List[List[Dict]] = []
        for b in bank_list:
            same_bank = [c for c in requires_split_cards if c.get("_bank") == b]
            splits.extend(self._generate_splits(same_bank, amount, max_cards_split))

        if not splits and split_mode == SPLIT_INTER_BANK and len(bank_list) > 1:
            basket = self._greedy_basket(requires_split_cards, amount, max_cards_split)
            if basket:
                splits = [basket]

        if splits:
            return CardMatchResult(status="needs_split", split_options=splits,
                                   rejection_report=rejections, available_uah=available_uah)

        if available_uah < amount:
            final = rc.insufficient_balance(amount, available_uah, bank=bank)
        else:
            final = self._diagnose_failed_split(
                requires_split_cards, amount, available_uah, bank_list,
                split_mode, max_cards_split, bank,
            )

        return CardMatchResult(
            status="no_cards", available_uah=available_uah,
            rejection_report=rejections + [final.as_dict()],
        )

    def _diagnose_failed_split(self, cards: List[Dict], amount: float,
                               available_uah: float, bank_list: List[str],
                               split_mode: str, max_cards: int, bank: str) -> "rc.Rejection":
        """
        Чому спліт не склався, коли грошей насправді вистачає.

        «Не вдалось скласти спліт» — правдиве, але марне формулювання: воно
        не каже, що робити. На живих даних користувача саме воно склало
        половину всіх відмов, і причина щоразу була одна з двох нижче, а не
        брак коштів.

        Діагностика нічого не змінює в маршруті — лише пояснює відмову.
        """
        # 1. Кошик між банками склався б, але режим спліту цього не дозволяє.
        #    Фіча в /features лише РОЗБЛОКОВУЄ цей режим; обрати його треба
        #    окремо в налаштуваннях карток, і про це легко не здогадатись.
        if split_mode != SPLIT_INTER_BANK and len(bank_list) > 1:
            if self._greedy_basket(cards, amount, max_cards):
                banks_used = ", ".join(bank_display_name(b) for b in bank_list)
                return rc.Rejection(
                    rc.SPLIT_NEEDS_INTER_BANK,
                    f"Грошей вистачає ({_uah(available_uah)}), але вони на різних "
                    f"банках ({banks_used}), а режим спліту — «у межах одного банку». "
                    f"Увімкніть «Спліт: між банками» в налаштуваннях карток",
                    bank=bank,
                )

        # 2. Склався б, але карток треба більше, ніж дозволено на угоду.
        for wider in range(max_cards + 1, 4):
            if self._greedy_basket(cards, amount, wider):
                return rc.Rejection(
                    rc.SPLIT_NEEDS_MORE_CARDS,
                    f"Грошей вистачає ({_uah(available_uah)}), але зібрати їх можна "
                    f"лише {wider} картками, а дозволено {max_cards}. "
                    f"Підніміть «Максимум карток на угоду»",
                    bank=bank,
                )

        # 3. Решта: суми є, але кожна окремо не лягає в ліміти переказу.
        return rc.Rejection(
            rc.SPLIT_IMPOSSIBLE,
            f"Разом на картках {_uah(available_uah)}, але скласти з них "
            f"{_uah(amount)} не вдалось — заважають ліміти окремих переказів",
            bank=bank,
        )

    def _score_card(self, card: Dict, amount: float) -> int:
        """
        Придатність картки під конкретну суму.

        Винесено з `_score_and_pick`, бо тепер той самий скоринг потрібен і
        кошику: дві копії ваг розійшлись би, і одиночний вибір почав би
        віддавати перевагу не тій картці, що набір.
        """
        score = 100
        if not card.get("is_own", 1):
            score += 50
        if card.get("_is_warm", False):
            score += 100

        # Tier банку — вага «за інших рівних». Свідомо слабша за прогрів
        # і за ознаку власної/дроп-картки: надійність банку не має
        # перебивати те, чим картка є.
        tier = get_bank_profile(card.get("_bank") or card.get("bank_name", "")).tier
        score += (4 - max(1, min(3, tier))) * 15

        last_tx = card.get("last_tx_timestamp", 0)
        if last_tx > 0 and (time.time() - last_tx) < 3600:
            score += 30

        # Картка, яку сума закриває майже повністю. Це не економія заради
        # економії: витратити 5 000 ₴ з ПУМБ одним переказом краще, ніж
        # відкусити ті самі 5 000 від Монобанку й лишити ПУМБ висіти —
        # великий залишок стане в пригоді під більший ордер.
        avail = float(card.get("_max_avail", 0.0))
        if avail > 0 and (avail - amount) < (avail * 0.1):
            score += 40

        if card.get("_tx_count", 0) >= 13:
            score -= 60
        return score

    def _score_and_pick(self, cards: List[Dict], amount: float) -> Dict:
        for card in cards:
            card["_score"] = self._score_card(card, amount)

        cards.sort(key=lambda x: x["_score"], reverse=True)
        return cards[0]

    def _greedy_basket(self, cards: List[Dict], amount: float, max_cards: int) -> List[Dict]:
        """
        Жадібний набір карток під суму — можливо, з різних банків.

        Комбінаторний перебір тут не годиться: при 20 картках (основна плюс
        три дроп-картки на банк) $O(2^N)$ дає тисячу варіантів НА КОЖЕН ордер
        і кладе цикл сканера. Жадібний прохід — $O(N \\log N)$ і дає маршрут,
        який людина й так обрала б.

        Сортуємо за доступною сумою вниз, а не за скорингом: мета кошика —
        мінімум переказів. Мерчанти часто пишуть «тільки одним платежем», і
        кожна зайва нога — це ще один застосунок під таймер угоди. Скоринг
        іде другим ключем, щоб за рівних сум перевагу мала прогріта картка
        надійнішого банку.
        """
        ordered = sorted(
            cards,
            key=lambda c: (float(c.get("_max_avail", 0.0)), self._score_card(c, amount)),
            reverse=True,
        )

        plan: List[Dict] = []
        remaining = amount
        for card in ordered:
            if len(plan) >= max_cards:
                break
            take = min(float(card.get("_max_avail", 0.0)), remaining)
            if take <= 0:
                continue
            plan.append({
                "card_id": card["id"],
                "last_four": card["last_four"],
                "bank": card.get("_bank") or normalize_bank(card.get("bank_name", "")),
                "amount": round(take, 2),
            })
            remaining -= take
            if remaining <= 0.01:
                return plan

        # Не набралось — маршруту немає. Часткове покриття не пропонуємо:
        # угода на меншу суму це вже інша угода, і вирішувати це має той,
        # хто знає ліміти мерчанта.
        return []

    def _generate_splits(self, cards: List[Dict], amount: float, max_cards: int) -> List[List[Dict]]:
        valid_splits = []
        
        # B6: Внутрішній спліт — одна картка, дві транзакції
        for card in cards:
            uncapped = card.get("_max_avail_uncapped", card["_max_avail"])
            # Картка сюди потрапляє лише пройшовши перевірки вище, де
            # _max_single проставляється завжди. Фолбек «без обмеження» на
            # випадок іншого викликача: він просто вимикає внутрішній спліт,
            # а не підставляє чужу цифру.
            max_single = card.get("_max_single", float("inf"))
            if uncapped >= amount and max_single < amount:
                # Картка має достатньо ліміту, але одна TX не вміщує суму
                # Розбиваємо на дві TX: max_single + залишок
                first_leg = max_single
                second_leg = amount - first_leg
                if second_leg <= max_single:
                    leg_bank = card.get("_bank") or normalize_bank(card.get("bank_name", ""))
                    valid_splits.append([
                        {"card_id": card["id"], "last_four": card["last_four"],
                         "bank": leg_bank, "amount": first_leg},
                        {"card_id": card["id"], "last_four": card["last_four"],
                         "bank": leg_bank, "amount": second_leg},
                    ])
        
        # Мульти-карт спліт
        for r in range(2, max_cards + 1):
            for combo in combinations(cards, r):
                total_avail = sum(c["_max_avail"] for c in combo)
                if total_avail >= amount:
                    split_plan = []
                    remaining = amount
                    for c in combo:
                        alloc = min(c["_max_avail"], remaining)
                        split_plan.append({
                            "card_id": c["id"],
                            "last_four": c["last_four"],
                            "bank": c.get("_bank") or normalize_bank(c.get("bank_name", "")),
                            "amount": alloc
                        })
                        remaining -= alloc
                        if remaining <= 0:
                            break
                    if remaining <= 0:
                        valid_splits.append(split_plan)
        valid_splits.sort(key=lambda s: len(s))
        return valid_splits
