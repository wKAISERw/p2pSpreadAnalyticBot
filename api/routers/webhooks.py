# api/routers/webhooks.py
import logging
from fastapi import APIRouter, Request

from api.schemas import SessionPayload

router = APIRouter(prefix="/api/v1", tags=["Webhooks"])
logger = logging.getLogger("ApiWebhooks")


@router.post("/session/receive")
async def receive_session(payload: SessionPayload):
    """Ендпоінт для отримання кукісів браузера через Bookmarklet скрипт."""
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

    return {"status": "ok"}