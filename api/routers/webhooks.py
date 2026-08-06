# api/routers/webhooks.py
import logging
from fastapi import APIRouter, Depends, Request

from api.schemas import SessionPayload
from api.security import require_api_key

router = APIRouter(prefix="/api/v1", tags=["Webhooks"])
logger = logging.getLogger("ApiWebhooks")


@router.post("/session/receive", dependencies=[Depends(require_api_key)])
async def receive_session(payload: SessionPayload):
    """
    Ендпоінт для отримання кукісів браузера через Bookmarklet скрипт.

    Захищений API-ключем: без нього будь-хто міг підкинути власну сесію
    біржі для довільного user_id, і сканер працював би під нею.
    Букмарклет може передати ключ як ?api_key=... (див. api/security.py).
    """
    from bot.handlers.core import _db as db
    cookies_dict = {}
    if payload.cookies_str:
        for chunk in str(payload.cookies_str).split(';'):
            if '=' in chunk:
                k, v = chunk.split('=', 1)
                cookies_dict[k.strip()] = v.strip()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*"
    }

    success = await db.save_auth_session(
        exchange=payload.exchange,
        headers=headers,
        cookies=cookies_dict,
        user_id=payload.user_id
    )
    if success:
        logger.info(f"✅ Успішно отримано сесію {payload.exchange} від юзера {payload.user_id} через Bookmarklet")
        return {"status": "ok", "message": "Session saved successfully"}

    logger.error(f"❌ Помилка збереження сесії {payload.exchange}")
    return {"status": "error", "message": "Failed to save session"}


@router.post("/webhooks/mono/card/{card_id}/{secret}")
async def mono_webhook(card_id: str, secret: str, request: Request):
    from bot.handlers.core import _db as db
    settings_card = await db.get_card_mono_settings(card_id)
    if not settings_card or settings_card.get("webhook_secret") != secret:
        return {"status": "error", "detail": "Invalid secret"}

    payload = await request.json()
    if payload.get("type") != "StatementItem":
        return {"status": "ok"}

    data = payload.get("data", {})
    account_id = data.get("account")
    stmt = data.get("statementItem", {})
    amount = float(stmt.get("amount", 0)) / 100.0
    true_balance = float(stmt.get("balance", 0)) / 100.0

    if not account_id or not amount:
        return {"status": "ignored"}

    direction = "in" if amount > 0 else "out"
    abs_amount = abs(amount)

    card = await db.get_card_by_mono_account(account_id)
    if not card:
        logger.warning(f"Webhook received for unknown Mono account: {account_id}")
        return {"status": "ignored", "detail": "Account not found"}

    order_leg = await db.find_pending_order_for_card(card["id"], abs_amount)

    if order_leg:
        await db.confirm_transaction(
            card_id=card["id"],
            amount=abs_amount,
            direction=direction,
            type_str="work",
            linked_order_id=order_leg["id"],
            source="webhook_mono",
            true_balance=true_balance
        )
        logger.info(f"✅ Webhook auto-confirmed order {order_leg['id']} for card {card['id']}")
    else:
        await db.confirm_transaction(
            card_id=card["id"],
            amount=abs_amount,
            direction=direction,
            type_str="personal",
            linked_order_id=None,
            source="webhook_mono",
            true_balance=true_balance
        )
        logger.info(f"ℹ️ Webhook recorded personal transaction for card {card['id']}")

    # 🚀 Monobank Tracker Notification
    owner_id = card.get("owner_id")
    tracker_enabled = int(settings_card.get("tracker_enabled", 1))
    tracker_mode = settings_card.get("tracker_mode", "INCOME")
    fields_raw = settings_card.get("tracker_fields") or '{}'

    try:
        import json
        fields_dict = json.loads(fields_raw) if isinstance(fields_raw, str) else fields_raw
    except Exception:
        fields_dict = {"amount": 1, "sender": 1, "comment": 1, "time": 1, "card": 1, "balance": 1, "p2p": 1}

    should_notify = tracker_enabled == 1 and (tracker_mode == "ALL" or (tracker_mode == "INCOME" and amount > 0))

    if should_notify and owner_id:
        try:
            from bot.handlers.core import _bot
            import time
            from datetime import datetime

            if _bot:
                lines = ["🐈 <b>Monobank — Нова транзакція!</b>\n"]
                if fields_dict.get("amount", 1):
                    icon = "🟢 +" if amount > 0 else "🔴 "
                    lines.append(f"💰 <b>Сума:</b> {icon}{abs_amount:,.2f} ₴")

                if fields_dict.get("sender", 1):
                    sender_name = stmt.get("counterName") or stmt.get("description") or "Невідомо"
                    lines.append(f"👤 <b>Відправник/Опис:</b> {sender_name}")

                if fields_dict.get("comment", 1) and stmt.get("comment"):
                    lines.append(f"💬 <b>Коментар:</b> <i>{stmt.get('comment')}</i>")

                if fields_dict.get("time", 1):
                    ts = stmt.get("time", time.time())
                    time_str = datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")
                    lines.append(f"⏰ <b>Час:</b> {time_str}")

                if fields_dict.get("card", 1):
                    card_label = card.get("label") or f"Картка *{card.get('last_four', '')}"
                    lines.append(f"💳 <b>Картка:</b> {card_label}")

                if fields_dict.get("balance", 1):
                    lines.append(f"📊 <b>Новий залишок:</b> {true_balance:,.2f} ₴")

                if fields_dict.get("p2p", 1) and order_leg:
                    lines.append(f"\n🔗 <b>✅ Співпадає з P2P ордером #{order_leg.get('order_id', order_leg.get('id', ''))}!</b>")

                await _bot.send_message(chat_id=owner_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as notify_err:
            logger.error(f"Failed to send Mono tracker notification to {owner_id}: {notify_err}")

    # 🚀 Trigger Buy Mode Auto-scaler check
    if owner_id:
        try:
            from core.engine.taker_scanner import trigger_buy_autoscale_check
            await trigger_buy_autoscale_check(db, owner_id)
        except Exception as auto_err:
            logger.debug(f"Auto-scale check error: {auto_err}")

    return {"status": "ok"}