"""
stats_engine.py — Аналітика торгівлі.

Методи:
  - get_profit_by_day: прибуток по днях
  - get_top_exchanges: топ бірж за об'ємом/кількістю
  - get_hourly_heatmap: теплова карта спредів по годинах/днях тижня
  - get_weekly_comparison: будні vs вихідні
  - get_summary: загальна статистика
"""
import logging
from typing import Optional
from core.storage.merchant_db import MerchantDB

logger = logging.getLogger("StatsEngine")


class StatsEngine:
    def __init__(self, db: MerchantDB):
        self._db = db

    async def get_profit_by_day(self, period_days: int = 30, owner_user_id: int = 0) -> list[dict]:
        """
        Прибуток по днях за останній період.
        → [{"date": "2026-04-01", "profit": 125.50, "trades": 3}]
        """
        if not self._db._db:
            return []
        try:
            query = """
                SELECT DATE(ts.completed_at) as date,
                       SUM(ts.gross_profit) as profit,
                       COUNT(*) as trades
                FROM trade_sessions ts
                WHERE ts.session_status = 'COMPLETED'
                  AND ts.completed_at >= datetime('now', ?)
                GROUP BY DATE(ts.completed_at)
                ORDER BY date DESC
            """
            async with self._db._db.execute(query, (f"-{period_days} days",)) as cur:
                rows = await cur.fetchall()
            return [{"date": r["date"], "profit": r["profit"] or 0, "trades": r["trades"]} for r in rows]
        except Exception as e:
            logger.error("get_profit_by_day: %s", e)
            return []

    async def get_top_exchanges(self, period_days: int = 30) -> list[dict]:
        """
        Топ бірж за об'ємом та кількістю.
        → [{"exchange": "Bybit", "volume_uah": 50000, "trades": 12}]
        """
        if not self._db._db:
            return []
        try:
            query = """
                SELECT at.exchange,
                       SUM(at.fiat_amount) as volume_uah,
                       COUNT(*) as trades
                FROM active_trades at
                WHERE at.status = 'COMPLETED'
                  AND at.created_at >= datetime('now', ?)
                GROUP BY at.exchange
                ORDER BY volume_uah DESC
            """
            async with self._db._db.execute(query, (f"-{period_days} days",)) as cur:
                rows = await cur.fetchall()
            return [{"exchange": r["exchange"], "volume_uah": r["volume_uah"] or 0, "trades": r["trades"]} for r in rows]
        except Exception as e:
            logger.error("get_top_exchanges: %s", e)
            return []

    async def get_hourly_heatmap(self, period_days: int = 14) -> dict[str, dict[str, int]]:
        """
        Теплова карта: у які години яких днів тижня найбільше угод.
        → {"Mon": {"09": 3, "10": 5, ...}, "Tue": {...}}
        """
        if not self._db._db:
            return {}
        try:
            query = """
                SELECT 
                    CASE CAST(strftime('%w', ts.created_at) AS INTEGER)
                        WHEN 0 THEN 'Sun' WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue'
                        WHEN 3 THEN 'Wed' WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri'
                        WHEN 6 THEN 'Sat'
                    END as day_of_week,
                    strftime('%H', ts.created_at) as hour,
                    COUNT(*) as cnt
                FROM trade_sessions ts
                WHERE ts.session_status = 'COMPLETED'
                  AND ts.created_at >= datetime('now', ?)
                GROUP BY day_of_week, hour
                ORDER BY day_of_week, hour
            """
            async with self._db._db.execute(query, (f"-{period_days} days",)) as cur:
                rows = await cur.fetchall()

            heatmap: dict[str, dict[str, int]] = {}
            for r in rows:
                day = r["day_of_week"]
                hour = r["hour"]
                if day not in heatmap:
                    heatmap[day] = {}
                heatmap[day][hour] = r["cnt"]
            return heatmap
        except Exception as e:
            logger.error("get_hourly_heatmap: %s", e)
            return {}

    async def get_weekly_comparison(self, period_days: int = 30) -> dict:
        """
        Будні vs вихідні: середній спред, кількість угод.
        → {"weekday": {"avg_profit": 45.2, "trades": 20}, "weekend": {"avg_profit": 62.1, "trades": 8}}
        """
        if not self._db._db:
            return {}
        try:
            query = """
                SELECT
                    CASE WHEN CAST(strftime('%w', ts.completed_at) AS INTEGER) IN (0, 6)
                         THEN 'weekend' ELSE 'weekday'
                    END as period,
                    AVG(ts.gross_profit) as avg_profit,
                    COUNT(*) as trades,
                    SUM(ts.gross_profit) as total_profit
                FROM trade_sessions ts
                WHERE ts.session_status = 'COMPLETED'
                  AND ts.completed_at >= datetime('now', ?)
                GROUP BY period
            """
            async with self._db._db.execute(query, (f"-{period_days} days",)) as cur:
                rows = await cur.fetchall()

            result = {}
            for r in rows:
                result[r["period"]] = {
                    "avg_profit": round(r["avg_profit"] or 0, 2),
                    "trades": r["trades"],
                    "total_profit": round(r["total_profit"] or 0, 2),
                }
            return result
        except Exception as e:
            logger.error("get_weekly_comparison: %s", e)
            return {}

    async def get_summary(self, period_days: int = 30) -> dict:
        """Особиста статистика (Мої угоди)."""
        if not getattr(self._db, "_db", None): return {}

        async with self._db._db.execute(
                """SELECT COUNT(*), SUM(gross_profit), AVG(gross_profit)
                   FROM trade_sessions
                   WHERE session_status = 'COMPLETED'
                     AND completed_at >= datetime('now', ?)""",
                (f"-{period_days} days",)
        ) as cur:
            row = await cur.fetchone()

        # Найкращий день
        async with self._db._db.execute(
                """SELECT DATE (completed_at) as d, SUM (gross_profit) as p
                   FROM trade_sessions
                   WHERE session_status = 'COMPLETED' AND completed_at >= datetime('now', ?)
                   GROUP BY d
                   ORDER BY p DESC LIMIT 1""",
                (f"-{period_days} days",)
        ) as cur:
            best_day = await cur.fetchone()

        return {
            "total_trades": row[0] if row else 0,
            "total_profit": row[1] if row and row[1] else 0.0,
            "avg_profit": row[2] if row and row[2] else 0.0,
            "best_day": best_day[0] if best_day else "N/A"
        }

    async def get_proposals_summary(self, period_days: int = 7) -> dict:
        """Статистика знайдених сканером спредів (Аналітика ринку)."""
        if not getattr(self._db, "_db", None): return {}

        try:
            # Якщо є колонка was_sent, рахуємо її теж
            query = """
                    SELECT COUNT(*)                                      as total,
                           SUM(CASE WHEN was_sent = 1 THEN 1 ELSE 0 END) as sent,
                           AVG(spread_pct)                               as avg_spread,
                           MAX(spread_pct)                               as max_spread
                    FROM scanner_proposals
                    WHERE created_at >= datetime('now', ?) \
                    """
            async with self._db._db.execute(query, (f"-{period_days} days",)) as cur:
                row = await cur.fetchone()
                return {
                    "total": row[0] if row else 0,
                    "sent": row[1] if row else 0,
                    "avg_spread": row[2] if row and row[2] else 0.0,
                    "max_spread": row[3] if row and row[3] else 0.0
                }
        except Exception:
            # Fallback, якщо колонки was_sent ще немає в таблиці
            query = """
                    SELECT COUNT(*)        as total,
                           AVG(spread_pct) as avg_spread,
                           MAX(spread_pct) as max_spread
                    FROM scanner_proposals
                    WHERE created_at >= datetime('now', ?) \
                    """
            async with self._db._db.execute(query, (f"-{period_days} days",)) as cur:
                row = await cur.fetchone()
                return {
                    "total": row[0] if row else 0,
                    "sent": 0,
                    "avg_spread": row[1] if row and row[1] else 0.0,
                    "max_spread": row[2] if row and row[2] else 0.0
                }

    # ─── Форматування для Telegram ──────────────────────────────────────

    async def get_top_banks(self, period_days: int = 30) -> list[dict]:
        """
        Топ банків за кількістю угод.
        → [{"bank": "Monobank", "trades": 15, "volume_uah": 75000}]
        """
        if not self._db._db:
            return []
        try:
            query = """
                SELECT at.payment_method as bank,
                       COUNT(*) as trades,
                       SUM(at.fiat_amount) as volume_uah
                FROM active_trades at
                WHERE at.status = 'COMPLETED'
                  AND at.payment_method IS NOT NULL
                  AND at.payment_method != ''
                  AND at.created_at >= datetime('now', ?)
                GROUP BY at.payment_method
                ORDER BY trades DESC
                LIMIT 10
            """
            async with self._db._db.execute(query, (f"-{period_days} days",)) as cur:
                rows = await cur.fetchall()
            return [
                {"bank": r["bank"], "trades": r["trades"], "volume_uah": r["volume_uah"] or 0}
                for r in rows
            ]
        except Exception as e:
            logger.error("get_top_banks: %s", e)
            return []

    async def format_stats_message(self, period_days: int = 30) -> str:
        """Готове повідомлення для /stats команди в боті."""
        summary = await self.get_summary(period_days)
        proposals = await self._db.get_proposals_summary(period_days)

        # Якщо взагалі нічого немає
        if (not summary or summary.get("total_trades", 0) == 0) and not proposals:
            return "📊 Статистика порожня — завершених угод і пропозицій немає."

        weekly = await self.get_weekly_comparison(period_days)
        wd = weekly.get("weekday", {})
        we = weekly.get("weekend", {})

        lines = [
            f"📊 *Статистика за {period_days} днів*",
            "",
        ]

        # ── Пропозиції сканера ──
        if proposals:
            lines.append("📡 *Пропозиції сканера:*")
            lines.append(f"  Знайдено: *{proposals['total']}* спредів")
            lines.append(f"  Надіслано: *{proposals.get('sent', 0)}* алертів")
            lines.append(f"  Сер. спред: *{proposals['avg_spread']:.2f}%*")
            lines.append(f"  Макс. спред: *{proposals['max_spread']:.2f}%*")
            lines.append(f"  Потенц. прибуток: *{proposals['total_potential_profit']:.0f} UAH*")
            lines.append("")

        # ── Мої угоди ──
        if summary and summary.get("total_trades", 0) > 0:
            lines.append("💼 *Мої угоди:*")
            lines.append(f"  💰 Прибуток: *{summary['total_profit']:.2f} UAH*")
            lines.append(f"  📈 Угод: *{summary['total_trades']}*")
            lines.append(f"  📉 Середній: *{summary['avg_profit']:.2f} UAH*")
            lines.append(f"  🏆 Найкращий день: *{summary['best_day']}*")
            lines.append("")
            lines.append("📅 *Будні vs Вихідні:*")
            lines.append(f"  Будні: {wd.get('trades', 0)} угод, avg {wd.get('avg_profit', 0):.2f} UAH")
            lines.append(f"  Вихідні: {we.get('trades', 0)} угод, avg {we.get('avg_profit', 0):.2f} UAH")
        else:
            lines.append("💼 *Мої угоди:* немає завершених")

        # Топ банків
        top_banks = await self.get_top_banks(period_days)
        if top_banks:
            lines.append("")
            lines.append("🏦 *Топ банків:*")
            for b in top_banks[:5]:
                lines.append(f"  {b['bank']}: {b['trades']} угод, {b['volume_uah']:.0f} UAH")

        return "\n".join(lines)

    # ─── Детальні звіти для drill-down ────────────────────────────────────

    async def format_daily_report(self, period_days: int = 30) -> str:
        """Детальний звіт по днях."""
        days = await self.get_profit_by_day(period_days)
        if not days:
            return "📅 <b>Прибуток по днях</b>\n\nДаних немає — жодної завершеної угоди."

        total_profit = sum(d["profit"] for d in days)
        total_trades = sum(d["trades"] for d in days)
        avg_daily = total_profit / len(days) if days else 0
        best = max(days, key=lambda d: d["profit"]) if days else None
        worst = min(days, key=lambda d: d["profit"]) if days else None

        lines = [
            f"📅 <b>Прибуток по днях ({period_days}д)</b>",
            "",
            f"📊 Всього днів з угодами: <b>{len(days)}</b>",
            f"💰 Загалом: <b>{total_profit:.2f} ₴</b> ({total_trades} угод)",
            f"📈 Середній день: <b>{avg_daily:.2f} ₴</b>",
        ]

        if best:
            lines.append(f"🏆 Найкращий: <b>{best['date']}</b> → +{best['profit']:.2f} ₴ ({best['trades']} угод)")
        if worst:
            lines.append(f"📉 Найгірший: <b>{worst['date']}</b> → +{worst['profit']:.2f} ₴ ({worst['trades']} угод)")

        lines.append("")
        lines.append("<code>───────────────────────────</code>")

        # Графік: bar chart з емодзі
        max_profit = max(d["profit"] for d in days) if days else 1
        for d in days[:20]:  # Останні 20 днів
            bar_len = int((d["profit"] / max_profit) * 8) if max_profit > 0 else 0
            bar = "█" * max(bar_len, 1) if d["profit"] > 0 else "░"
            short_date = d["date"][5:]  # MM-DD
            lines.append(
                f"<code>{short_date}</code> {bar} <b>{d['profit']:>7.0f}₴</b> ({d['trades']})"
            )

        if len(days) > 20:
            lines.append(f"<i>…та ще {len(days) - 20} днів</i>")

        return "\n".join(lines)

    async def format_exchanges_report(self, period_days: int = 30) -> str:
        """Детальний звіт по біржах."""
        exchanges = await self.get_top_exchanges(period_days)
        if not exchanges:
            return "🏛 <b>Статистика по біржах</b>\n\nДаних немає."

        total_vol = sum(e["volume_uah"] for e in exchanges)
        total_trades = sum(e["trades"] for e in exchanges)

        ICONS = {"Binance": "🟡", "Bybit": "🟣", "OKX": "🟢", "MEXC": "🔵", "Wallet": "👛"}

        lines = [
            f"🏛 <b>Статистика по біржах ({period_days}д)</b>",
            "",
            f"💱 Загальний оборот: <b>{total_vol:,.0f} ₴</b>",
            f"📈 Всього угод: <b>{total_trades}</b>",
            "",
        ]

        for e in exchanges:
            icon = ICONS.get(e["exchange"], "◽️")
            pct = (e["volume_uah"] / total_vol * 100) if total_vol > 0 else 0
            avg_trade = e["volume_uah"] / e["trades"] if e["trades"] > 0 else 0
            bar_len = int(pct / 5)
            bar = "▓" * max(bar_len, 1)

            lines.append(
                f"{icon} <b>{e['exchange']}</b>\n"
                f"  {bar} {pct:.1f}%\n"
                f"  💰 Оборот: <b>{e['volume_uah']:,.0f} ₴</b>\n"
                f"  📊 Угод: <b>{e['trades']}</b> (avg {avg_trade:,.0f} ₴/угода)"
            )
            lines.append("")

        return "\n".join(lines)

    async def format_banks_report(self, period_days: int = 30) -> str:
        """Детальний звіт по банках."""
        banks = await self.get_top_banks(period_days)
        if not banks:
            return "🏦 <b>Статистика по банках</b>\n\nДаних немає — payment_method не зберігався."

        total_vol = sum(b["volume_uah"] for b in banks)
        total_trades = sum(b["trades"] for b in banks)

        BANK_ICONS = {
            "Monobank": "🐈‍⬛", "PrivatBank": "🟢", "ПУМБ": "🔵",
            "А-Банк": "🔴", "43": "🐈‍⬛", "14": "🟢", "64": "🔵", "48": "🔴",
        }

        lines = [
            f"🏦 <b>Статистика по банках ({period_days}д)</b>",
            "",
            f"💳 Всього методів оплати: <b>{len(banks)}</b>",
            f"📊 Всього угод: <b>{total_trades}</b>",
            f"💰 Загальний оборот: <b>{total_vol:,.0f} ₴</b>",
            "",
        ]

        for i, b in enumerate(banks, 1):
            icon = BANK_ICONS.get(b["bank"], "💳")
            pct = (b["volume_uah"] / total_vol * 100) if total_vol > 0 else 0
            avg_trade = b["volume_uah"] / b["trades"] if b["trades"] > 0 else 0

            lines.append(
                f"{i}. {icon} <b>{b['bank']}</b>\n"
                f"  📊 Угод: <b>{b['trades']}</b> ({pct:.1f}% обороту)\n"
                f"  💰 Оборот: <b>{b['volume_uah']:,.0f} ₴</b> (avg {avg_trade:,.0f} ₴)"
            )

        return "\n".join(lines)

    async def format_heatmap_report(self, period_days: int = 14) -> str:
        """Теплова карта: години × дні тижня."""
        heatmap = await self.get_hourly_heatmap(period_days)
        if not heatmap:
            return "🕐 <b>Теплова карта</b>\n\nДаних немає."

        DAY_LABELS = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт",
                       "Fri": "Пт", "Sat": "Сб", "Sun": "Нд"}
        DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        # Знаходимо максимум для масштабування
        max_cnt = max(
            (cnt for day_data in heatmap.values() for cnt in day_data.values()),
            default=1,
        )

        # Визначаємо топ-години
        all_hours: dict[str, int] = {}
        for day_data in heatmap.values():
            for hour, cnt in day_data.items():
                all_hours[hour] = all_hours.get(hour, 0) + cnt

        top_hours = sorted(all_hours.items(), key=lambda x: x[1], reverse=True)[:5]
        best_hour = top_hours[0] if top_hours else ("?", 0)

        lines = [
            f"🕐 <b>Теплова карта ({period_days}д)</b>",
            "",
            f"⏰ Найкращий час: <b>{best_hour[0]}:00</b> ({best_hour[1]} угод)",
            "",
            "🔥 Топ-5 годин:",
        ]
        for h, cnt in top_hours:
            lines.append(f"  {h}:00 — <b>{cnt}</b> угод")

        lines.append("")
        lines.append("<code>     06 08 10 12 14 16 18 20 22</code>")

        HEAT = [" ", "░", "▒", "▓", "█"]
        for day in DAY_ORDER:
            label = DAY_LABELS.get(day, day)
            day_data = heatmap.get(day, {})
            row_chars = []
            for h in range(6, 24, 2):
                h_str = f"{h:02d}"
                cnt = day_data.get(h_str, 0)
                level = min(int(cnt / max(max_cnt, 1) * 4), 4) if cnt > 0 else 0
                row_chars.append(HEAT[level] * 2)
            row = " ".join(row_chars)
            lines.append(f"<code>{label} {row}</code>")

        lines.append("")
        lines.append(f"<code>Легенда: ░=мало ▒=середньо ▓=багато █=пік</code>")

        return "\n".join(lines)

    async def format_weekly_report(self, period_days: int = 30) -> str:
        """Детальне порівняння будні vs вихідні."""
        weekly = await self.get_weekly_comparison(period_days)
        if not weekly:
            return "📅 <b>Будні vs Вихідні</b>\n\nДаних немає."

        wd = weekly.get("weekday", {})
        we = weekly.get("weekend", {})

        wd_trades = wd.get("trades", 0)
        we_trades = we.get("trades", 0)
        wd_profit = wd.get("total_profit", 0)
        we_profit = we.get("total_profit", 0)
        wd_avg = wd.get("avg_profit", 0)
        we_avg = we.get("avg_profit", 0)
        total = wd_profit + we_profit

        # Хто виграє
        if wd_avg > we_avg:
            verdict = "📊 <b>Висновок:</b> Будні більш прибуткові в середньому"
        elif we_avg > wd_avg:
            verdict = "📊 <b>Висновок:</b> Вихідні більш прибуткові в середньому"
        else:
            verdict = "📊 <b>Висновок:</b> Різниці немає"

        lines = [
            f"📅 <b>Будні vs Вихідні ({period_days}д)</b>",
            "",
            "🏢 <b>БУДНІ (Пн-Пт):</b>",
            f"  📊 Угод: <b>{wd_trades}</b>",
            f"  💰 Прибуток: <b>{wd_profit:.2f} ₴</b>",
            f"  📈 Середній: <b>{wd_avg:.2f} ₴</b>/угода",
            f"  🔄 Частка: {wd_profit / total * 100:.0f}%" if total > 0 else "",
            "",
            "🏖 <b>ВИХІДНІ (Сб-Нд):</b>",
            f"  📊 Угод: <b>{we_trades}</b>",
            f"  💰 Прибуток: <b>{we_profit:.2f} ₴</b>",
            f"  📈 Середній: <b>{we_avg:.2f} ₴</b>/угода",
            f"  🔄 Частка: {we_profit / total * 100:.0f}%" if total > 0 else "",
            "",
            verdict,
        ]
        return "\n".join(lines)

    async def format_history_report(self, limit: int = 15) -> str:
        """Останні завершені угоди."""
        trades = await self._db.get_completed_trades(period_days=30)
        if not trades:
            return "📋 <b>Історія угод</b>\n\nНемає завершених угод."

        ICONS = {"Binance": "🟡", "Bybit": "🟣", "OKX": "🟢", "MEXC": "🔵", "Wallet": "👛"}
        lines = [
            f"📋 <b>Останні {min(limit, len(trades))} угод</b>",
            "",
        ]

        for t in trades[:limit]:
            icon = ICONS.get(t.get("exchange", ""), "◽️")
            leg = t.get("leg", "?")
            leg_icon = "🛒" if leg == "BUY" else "💸"
            price = t.get("price", 0) or 0
            amount = t.get("amount", 0) or 0
            fiat = t.get("fiat_amount", 0) or 0
            strategy = t.get("strategy", "?")
            bank = t.get("payment_method", "")
            profit = t.get("gross_profit", 0) or 0
            network = t.get("network", "")
            net_fee = t.get("network_fee", 0) or 0
            created = str(t.get("created_at", "?"))[:16]

            main_line = f"{leg_icon}{icon} <b>{strategy}</b> {leg} @ <code>{price:.2f}</code>"
            detail_line = f"  {amount:.2f} USDT → {fiat:.0f} ₴"
            meta_parts = []
            if bank:
                meta_parts.append(f"🏦{bank}")
            if network and network != "N/A":
                meta_parts.append(f"🌐{network}")
                if net_fee > 0:
                    meta_parts.append(f"fee={net_fee:.2f}$")
            if profit > 0:
                meta_parts.append(f"+{profit:.0f}₴")
            meta = " | ".join(meta_parts)

            lines.append(f"{main_line}")
            lines.append(f"{detail_line}")
            if meta:
                lines.append(f"  {meta}")
            lines.append(f"  <i>{created}</i>")
            lines.append("")

        if len(trades) > limit:
            lines.append(f"<i>…та ще {len(trades) - limit} угод</i>")

        return "\n".join(lines)

    async def get_full_stats(self, period_days: int = 30) -> dict:
        """Повна статистика для API."""
        return {
            "summary": await self.get_summary(period_days),
            "proposals": await self._db.get_proposals_summary(period_days),
            "daily": await self.get_profit_by_day(period_days),
            "exchanges": await self.get_top_exchanges(period_days),
            "banks": await self.get_top_banks(period_days),
            "heatmap": await self.get_hourly_heatmap(period_days),
            "weekly": await self.get_weekly_comparison(period_days),
        }

    # ─── Пропозиції сканера (детальні звіти) ──────────────────────────────

    async def format_proposals_report(self, period_days: int = 7) -> str:
        """Детальний звіт по пропозиціях сканера по днях."""
        summary = await self._db.get_proposals_summary(period_days)
        days = await self._db.get_proposals_by_day(period_days)

        if not summary or summary.get("total", 0) == 0:
            return "📡 <b>Пропозиції сканера</b>\n\nЗа цей період пропозицій не знайдено."

        lines = [
            f"📡 <b>Пропозиції сканера ({period_days}д)</b>",
            "",
            f"📊 Всього знайдено: <b>{summary['total']}</b> спредів",
            f"📤 Надіслано алертів: <b>{summary.get('sent', 0)}</b>",
            f"📈 Сер. спред: <b>{summary['avg_spread']:.2f}%</b>",
            f"🔝 Макс. спред: <b>{summary['max_spread']:.2f}%</b>",
            f"💰 Сер. потенц. профіт: <b>{summary['avg_profit']:.2f} ₴</b>",
            f"💎 Загальний потенціал: <b>{summary['total_potential_profit']:.0f} ₴</b>",
        ]

        if days:
            lines.append("")
            lines.append("<code>───────────────────────────</code>")
            max_total = max(d["total"] for d in days) if days else 1
            for d in days[:14]:
                bar_len = int((d["total"] / max_total) * 8) if max_total > 0 else 0
                bar = "█" * max(bar_len, 1)
                short_date = d["date"][5:] if d["date"] else "?"
                lines.append(
                    f"<code>{short_date}</code> {bar} <b>{d['total']:>4}</b> ({d['sent']} відправл.) avg {d['avg_spread']:.2f}%"
                )

        return "\n".join(lines)

    async def format_proposals_routes(self, period_days: int = 7) -> str:
        """Топ маршрутів сканера."""
        routes = await self._db.get_proposals_by_route(period_days)
        if not routes:
            return "🗺 <b>Маршрути сканера</b>\n\nДаних немає."

        ICONS = {"Binance": "🟡", "Bybit": "🟣", "OKX": "🟢", "MEXC": "🔵", "Wallet": "👛"}

        lines = [
            f"🗺 <b>Топ маршрутів сканера ({period_days}д)</b>",
            "",
        ]

        for i, r in enumerate(routes, 1):
            route = r["route"]
            parts = route.split("→")
            buy_icon = ICONS.get(parts[0].strip(), "◽️") if len(parts) > 0 else ""
            sell_icon = ICONS.get(parts[1].strip(), "◽️") if len(parts) > 1 else ""
            lines.append(
                f"{i}. {buy_icon}→{sell_icon} <b>{route}</b> ({r['route_type']})\n"
                f"   📊 {r['count']} раз | avg {r['avg_spread']:.2f}% | avg +{r['avg_profit']:.0f}₴"
            )
            lines.append("")

        return "\n".join(lines)

    async def get_proposals_top_exchanges(self, period_days: int = 14) -> list[dict]:
        """Топ бірж, які сканер знаходив найчастіше."""
        if not getattr(self._db, "_db", None): return []
        async with self._db._db.execute(
                """
                SELECT buy_exchange as exchange, COUNT(*) as count, AVG(spread_pct) as avg_spread
                FROM scanner_proposals
                WHERE created_at >= datetime('now', ?)
                GROUP BY buy_exchange
                ORDER BY count DESC LIMIT 5
                """, (f"-{period_days} days",)
        ) as cur:
            rows = await cur.fetchall()
            return [{"exchange": r[0], "count": r[1], "avg_spread": r[2]} for r in rows]

    async def get_proposals_hourly_heatmap(self, period_days: int = 14) -> dict:
        """Теплова карта активності ринку (коли з'являються спреди)."""
        if not getattr(self._db, "_db", None): return {}
        async with self._db._db.execute(
                """
                SELECT strftime('%w', created_at) as weekday,
                       strftime('%H', created_at) as hour,
                   COUNT(*) as count
                FROM scanner_proposals
                WHERE created_at >= datetime('now', ?)
                GROUP BY weekday, hour
                """, (f"-{period_days} days",)
        ) as cur:
            rows = await cur.fetchall()

        heatmap = {str(d): {f"{h:02d}": 0 for h in range(24)} for d in range(7)}
        for r in rows:
            heatmap[r[0]][r[1]] = r[2]
        return heatmap

    async def get_my_hourly_heatmap(self, period_days: int = 30) -> dict:
        """Теплова карта МОЇХ успішних угод."""
        if not getattr(self._db, "_db", None): return {}
        async with self._db._db.execute(
                """
                SELECT strftime('%w', completed_at) as weekday,
                       strftime('%H', completed_at) as hour,
                   COUNT(*) as count
                FROM trade_sessions
                WHERE session_status = 'COMPLETED' AND completed_at >= datetime('now', ?)
                GROUP BY weekday, hour
                """, (f"-{period_days} days",)
        ) as cur:
            rows = await cur.fetchall()

        heatmap = {str(d): {f"{h:02d}": 0 for h in range(24)} for d in range(7)}
        for r in rows:
            heatmap[r[0]][r[1]] = r[2]
        return heatmap
