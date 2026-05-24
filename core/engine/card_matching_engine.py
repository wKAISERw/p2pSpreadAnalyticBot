import logging
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from itertools import combinations

from core.storage.merchant_db import MerchantDB

logger = logging.getLogger(__name__)

@dataclass
class CardMatchResult:
    status: str  # "success", "needs_split", "no_cards", "disabled", "no_crypto"
    best_card: Optional[Dict] = None
    split_options: List[List[Dict]] = field(default_factory=list)
    rejection_report: List[Dict] = field(default_factory=list)

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
        excluded_cards: Optional[List[str]] = None
    ) -> CardMatchResult:
        
        settings = await self.db.get_user_card_settings(user_id)
        if not settings or settings.get("card_module_mode") == "off":
            return CardMatchResult(status="disabled")
            
        if direction in ("sell", "spread") and not crypto_available:
            return CardMatchResult(status="no_crypto")
            
        if direction == "spread":
            buy_res = await self._run_single(user_id, bank, amount, "buy", settings, excluded_cards)
            sell_res = await self._run_single(user_id, bank, amount, "sell", settings, excluded_cards)
            if buy_res.status == "success" and sell_res.status == "success":
                return CardMatchResult(
                    status="success", 
                    best_card={"buy": buy_res.best_card, "sell": sell_res.best_card}
                )
            return CardMatchResult(status="no_cards", rejection_report=buy_res.rejection_report + sell_res.rejection_report)

        return await self._run_single(user_id, bank, amount, direction, settings, excluded_cards)

    async def _run_single(self, user_id: int, bank: str, amount: float, direction: str, settings: dict, excluded_cards: Optional[List[str]] = None) -> CardMatchResult:
        # Fetch limits
        limits = await self.db.get_user_bank_limits(user_id, bank)
        if not limits:
            limits = {
                "daily_out_max": 150000.0, "daily_in_max": 150000.0,
                "monthly_out_max": 400000.0, "monthly_in_max": 400000.0,
                "max_single_tx_out": 29999.0, "max_single_tx_in": 29999.0,
                "max_tx_per_day": 15, "cooldown_hours": 24
            }

        all_cards = await self.db.get_cards(user_id, bank_name=bank, status="active")
        if excluded_cards:
            all_cards = [c for c in all_cards if c["id"] not in excluded_cards]
            
        if not all_cards:
            return CardMatchResult(status="no_cards", rejection_report=[{"reason": "No active cards"}])

        for card in all_cards:
            await self.db.lazy_monthly_reset(card["id"])

        all_cards = await self.db.get_cards(user_id, bank_name=bank, status="active")
        if excluded_cards:
            all_cards = [c for c in all_cards if c["id"] not in excluded_cards]

        passed_cards = []
        requires_split_cards = []
        rejections = []

        for card in all_cards:
            reason = None
            
            # Fetch card-specific effective limits (incorporating local overrides if custom limits are active)
            card_limits = await self.db.get_card_effective_limits(card["id"], user_id, bank)
            
            max_single = card_limits.get("max_single_tx_in" if direction == "sell" else "max_single_tx_out", 29999.0)
            if max_single == -1 or max_single == -1.0:
                max_single = float('inf')

            daily_max = card_limits.get("daily_in_max" if direction == "sell" else "daily_out_max", 150000.0)
            if daily_max == -1 or daily_max == -1.0:
                daily_max = float('inf')

            monthly_max = card_limits.get("monthly_in_max" if direction == "sell" else "monthly_out_max", 400000.0)
            if monthly_max == -1 or monthly_max == -1.0:
                monthly_max = float('inf')

            if card["cooldown_until"] > time.time():
                reason = "Card is on cooldown"
                
            if not reason and direction == "buy" and card["balance"] < amount:
                if card["balance"] > 0:
                    pass # Could be used for split
                else:
                    reason = "Insufficient balance"
                    
            if not reason:
                tx_count = await self.db.get_card_transactions_count(card["id"], hours=24)
                max_tx = card_limits.get("max_tx_per_day", 15)
                if max_tx != -1 and max_tx != -1.0 and tx_count >= max_tx:
                    reason = f"Max transactions reached ({tx_count})"
                else:
                    card["_tx_count"] = tx_count
            
            if not reason:
                used_daily = await self.db.get_rolling_used(card["id"], direction, hours=24)
                used_monthly = await self.db.get_rolling_used(card["id"], direction, hours=24*30) 
                
                avail_daily = daily_max - used_daily if daily_max != float('inf') else float('inf')
                avail_monthly = monthly_max - used_monthly if monthly_max != float('inf') else float('inf')
                
                # Uncapped — без обмеження max_single_tx (для внутрішнього спліту B6)
                max_avail_uncapped = min(avail_daily, avail_monthly)
                if direction == "buy":
                    max_avail_uncapped = min(max_avail_uncapped, card["balance"])
                
                max_avail = min(max_avail_uncapped, max_single)
                    
                if max_avail <= 0:
                    reason = "Daily/Monthly limits exhausted"
                else:
                    card["_max_avail"] = max_avail
                    card["_max_avail_uncapped"] = max_avail_uncapped
                    card["_max_single"] = max_single
                    
            if reason:
                rejections.append({"card_id": card["id"], "last_four": card["last_four"], "reason": reason})
            else:
                if card["_max_avail"] >= amount:
                    passed_cards.append(card)
                else:
                    requires_split_cards.append(card)

        if not passed_cards and not requires_split_cards:
            return CardMatchResult(status="no_cards", rejection_report=rejections)

        if passed_cards:
            best = self._score_and_pick(passed_cards, amount)
            return CardMatchResult(status="success", best_card=best, rejection_report=rejections)
        
        max_cards_split = settings.get("max_cards_per_order", 3)
        splits = self._generate_splits(requires_split_cards, amount, max_cards_split)
        
        if splits:
            return CardMatchResult(status="needs_split", split_options=splits, rejection_report=rejections)
            
        return CardMatchResult(status="no_cards", rejection_report=rejections + [{"reason": "Could not generate split"}])

    def _score_and_pick(self, cards: List[Dict], amount: float) -> Dict:
        for card in cards:
            score = 100
            if not card.get("is_own", 1):
                score += 50
            
            last_tx = card.get("last_tx_timestamp", 0)
            if last_tx > 0 and (time.time() - last_tx) < 3600:
                score += 30
            if (card["_max_avail"] - amount) < (card["_max_avail"] * 0.1):
                score += 40
            if card.get("_tx_count", 0) >= 13:
                score -= 60
            card["_score"] = score
            
        cards.sort(key=lambda x: x["_score"], reverse=True)
        return cards[0]

    def _generate_splits(self, cards: List[Dict], amount: float, max_cards: int) -> List[List[Dict]]:
        valid_splits = []
        
        # B6: Внутрішній спліт — одна картка, дві транзакції
        for card in cards:
            uncapped = card.get("_max_avail_uncapped", card["_max_avail"])
            max_single = card.get("_max_single", 29999.0)
            if uncapped >= amount and max_single < amount:
                # Картка має достатньо ліміту, але одна TX не вміщує суму
                # Розбиваємо на дві TX: max_single + залишок
                first_leg = max_single
                second_leg = amount - first_leg
                if second_leg <= max_single:
                    valid_splits.append([
                        {"card_id": card["id"], "last_four": card["last_four"], "amount": first_leg},
                        {"card_id": card["id"], "last_four": card["last_four"], "amount": second_leg},
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
                            "amount": alloc
                        })
                        remaining -= alloc
                        if remaining <= 0:
                            break
                    if remaining <= 0:
                        valid_splits.append(split_plan)
        valid_splits.sort(key=lambda s: len(s))
        return valid_splits
