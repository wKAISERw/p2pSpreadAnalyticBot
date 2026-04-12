# ≡ƒÆ░ ╨ñ╨å╨¢╨¼╨ó╨á ╨ª╨å╨¥╨ÿ (Price Range ╨┤╨╗╤Å ╤é╨╡╨╣╨║╨╡╤Ç╤û╨▓)
# =========================================================================

@router.callback_query(F.data == "set:price_range")
async def on_price_range_menu(call: CallbackQuery) -> None:
    from bot.keyboards import price_range_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "≡ƒÆ░ <b>╨ñ╤û╨╗╤î╤é╤Ç ╤å╤û╨╜╨╕ (UAH/USDT)</b>\n\n"
            "╨₧╨▒╨╝╨╡╨╢╤â╤ö ╨╛╤Ç╨┤╨╡╤Ç╨╕, ╤Å╨║╤û ╨┐╨╛╨║╨░╨╖╤â╤ö ╤é╨╡╨╣╨║╨╡╤Ç-╤Ç╨╡╨╢╨╕╨╝.\n"
            "╨₧╨▒╨╡╤Ç╤û╤é╤î ╤é╨╕╨┐ ╤ä╤û╨╗╤î╤é╤Ç╨░:",
            reply_markup=price_range_kb(),
        )
    await call.answer()


@router.callback_query(F.data == "prange:off")
async def on_price_range_off(call: CallbackQuery) -> None:
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET price_range_json = '{}' WHERE user_id = ?",
            (call.from_user.id,),
        )
        await conn.commit()
    await call.answer("Γ£à ╨ñ╤û╨╗╤î╤é╤Ç ╤å╤û╨╜╨╕ ╨▓╨╕╨╝╨║╨╜╨╡╨╜╨╛", show_alert=True)
    with suppress(TelegramBadRequest):
        await call.message.edit_text("Γ£à ╨ñ╤û╨╗╤î╤é╤Ç ╤å╤û╨╜╨╕ ╨▓╨╕╨╝╨║╨╜╨╡╨╜╨╛.", reply_markup=back_to_main_kb())


@router.callback_query(F.data == "prange:range")
async def on_price_range_range(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(price_range_mode="range")
    await state.set_state(PriceRangeStates.waiting_range_min)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "≡ƒôÅ <b>╨ö╤û╨░╨┐╨░╨╖╨╛╨╜ ╤å╤û╨╜╨╕</b>\n\n╨Æ╨▓╨╡╨┤╨╕ <b>╨╝╤û╨╜╤û╨╝╨░╨╗╤î╨╜╤â</b> ╤å╤û╨╜╤â (UAH):",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(PriceRangeStates.waiting_range_min)
async def on_price_range_min_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("Γ¥î ╨Æ╨▓╨╡╨┤╨╕ ╨┤╨╛╨┤╨░╤é╨╜╨╡ ╤ç╨╕╤ü╨╗╨╛.")
    await state.update_data(price_min=val)
    await state.set_state(PriceRangeStates.waiting_range_max)
    await message.answer(f"Γ£à ╨£╤û╨╜: <b>{val:.2f}</b> Γé┤\n\n╨ó╨╡╨┐╨╡╤Ç ╨▓╨▓╨╡╨┤╨╕ <b>╨╝╨░╨║╤ü╨╕╨╝╨░╨╗╤î╨╜╤â</b> ╤å╤û╨╜╤â:")


@router.message(PriceRangeStates.waiting_range_max)
async def on_price_range_max_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("Γ¥î ╨Æ╨▓╨╡╨┤╨╕ ╨┤╨╛╨┤╨░╤é╨╜╨╡ ╤ç╨╕╤ü╨╗╨╛.")
    data = await state.get_data()
    price_min = data.get("price_min", 0)
    if val <= price_min:
        return await message.answer(f"Γ¥î ╨£╨░╨║╤ü╨╕╨╝╤â╨╝ ({val}) ╨┐╨╛╨▓╨╕╨╜╨╡╨╜ ╨▒╤â╤é╨╕ ╨▒╤û╨╗╤î╤ê╨╡ ╨╝╤û╨╜╤û╨╝╤â╨╝╤â ({price_min}).")
    await state.clear()
    import json
    pr = json.dumps({"mode": "range", "min": price_min, "max": val})
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET price_range_json = ? WHERE user_id = ?",
            (pr, message.from_user.id),
        )
        await conn.commit()
    await message.answer(
        f"Γ£à ╨ñ╤û╨╗╤î╤é╤Ç ╤å╤û╨╜╨╕: <b>{price_min:.2f} ΓÇö {val:.2f}</b> Γé┤",
        reply_markup=back_to_main_kb(),
    )


@router.callback_query(F.data.in_({"prange:exact", "prange:max", "prange:min"}))
async def on_price_range_single(call: CallbackQuery, state: FSMContext) -> None:
    mode = call.data.split(":")[1]
    labels = {"exact": "╨ó╨╛╤ç╨╜╨░ ╤å╤û╨╜╨░", "max": "╨£╨░╨║╤ü╨╕╨╝╨░╨╗╤î╨╜╨░ ╤å╤û╨╜╨░", "min": "╨£╤û╨╜╤û╨╝╨░╨╗╤î╨╜╨░ ╤å╤û╨╜╨░"}
    await state.update_data(price_range_mode=mode)
    await state.set_state(PriceRangeStates.waiting_value)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"≡ƒÄ» <b>{labels[mode]}</b>\n\n╨Æ╨▓╨╡╨┤╨╕ ╤å╤û╨╜╤â (UAH):",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(PriceRangeStates.waiting_value)
async def on_price_range_value_input(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("Γ¥î ╨Æ╨▓╨╡╨┤╨╕ ╨┤╨╛╨┤╨░╤é╨╜╨╡ ╤ç╨╕╤ü╨╗╨╛.")
    data = await state.get_data()
    mode = data.get("price_range_mode", "exact")
    await state.clear()
    import json
    pr = json.dumps({"mode": mode, "value": val})
    if _db:
        conn = getattr(_db, "db", None) or getattr(_db, "_db", _db)
        await conn.execute(
            "UPDATE scanner_users SET price_range_json = ? WHERE user_id = ?",
            (pr, message.from_user.id),
        )
        await conn.commit()
    labels = {"exact": f"Γëê{val:.2f}", "max": f"Γëñ{val:.2f}", "min": f"ΓëÑ{val:.2f}"}
    await message.answer(
        f"Γ£à ╨ñ╤û╨╗╤î╤é╤Ç ╤å╤û╨╜╨╕: <b>{labels[mode]}</b> Γé┤",
        reply_markup=back_to_main_kb(),
    )


# =========================================================================
# ≡ƒô¥ ╨í╨ó╨Æ╨₧╨á╨ò╨¥╨¥╨» P2P ╨₧╨ô╨₧╨¢╨₧╨¿╨ò╨¥╨¥╨» (Create Ad Wizard)
# =========================================================================

@router.callback_query(F.data == "ad:create")
async def on_ad_create(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    from bot.keyboards import create_ad_exchange_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "≡ƒô¥ <b>╨í╤é╨▓╨╛╤Ç╨╡╨╜╨╜╤Å P2P ╨╛╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å</b>\n\n"
            "╨₧╨▒╨╡╤Ç╨╕ ╨▒╤û╤Ç╨╢╤â:",
            reply_markup=create_ad_exchange_kb(),
        )
    await call.answer()


@router.callback_query(F.data.startswith("ad:ex:"))
async def on_ad_exchange(call: CallbackQuery, state: FSMContext) -> None:
    exchange = call.data.split(":")[2]
    if exchange == "_unsupported":
        return await call.answer("ΓÅ│ ╨ª╤Å ╨▒╤û╤Ç╨╢╨░ ╤ë╨╡ ╨╜╨╡ ╨┐╤û╨┤╤é╤Ç╨╕╨╝╤â╤ö╤é╤î╤ü╤Å", show_alert=True)
    await state.update_data(ad_exchange=exchange)
    await state.set_state(CreateAdStates.waiting_side)
    from bot.keyboards import create_ad_side_kb
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"≡ƒô¥ <b>╨₧╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å ╨╜╨░ {exchange}</b>\n\n"
            "╨₧╨▒╨╡╤Ç╨╕ ╤ü╤é╨╛╤Ç╨╛╨╜╤â:",
            reply_markup=create_ad_side_kb(),
        )
    await call.answer()


@router.callback_query(F.data.startswith("ad:side:"))
async def on_ad_side(call: CallbackQuery, state: FSMContext) -> None:
    side = call.data.split(":")[2]  # BUY or SELL
    await state.update_data(ad_side=side)
    await state.set_state(CreateAdStates.waiting_price)

    data = await state.get_data()
    exchange = data.get("ad_exchange", "")

    # ╨ƒ╤û╨┤╨║╨░╨╖╨║╨░ PriceAdvisor
    hint = ""
    from core.engine.price_advisor import PriceAdvisor
    if side == "SELL":
        # ╨ö╨╗╤Å ╨┐╤Ç╨╛╨┤╨░╨╢╤â ╨┐╨╛╤é╤Ç╤û╨▒╨╜╨░ ╤å╤û╨╜╨░ ╨║╤â╨┐╤û╨▓╨╗╤û ΓÇö ╨┐╨╛╨║╨╕ ╤ë╨╛ ╨▒╨╡╨╖ ╨┐╤û╨┤╨║╨░╨╖╨║╨╕
        hint = "\n\n≡ƒÆí <i>╨ƒ╤û╤ü╨╗╤Å ╨▓╨▓╨╛╨┤╤â ╤å╤û╨╜╨╕ ╨┐╨╛╨║╨░╨╢╤â ╨╝╤û╨╜╤û╨╝╨░╨╗╤î╨╜╤â ╤Ç╨╡╨╜╤é╨░╨▒╨╡╨╗╤î╨╜╤â ╤å╤û╨╜╤â.</i>"
    elif side == "BUY":
        # ╨ö╨╗╤Å ╨║╤â╨┐╤û╨▓╨╗╤û ΓÇö ╨┤╤û╤ü╤é╨░╤ö╨╝╨╛ sell_book_top ╨╖ ╨æ╨ö
        sell_book_top = 0.0
        if _db:
            sell_book_top = await _db.get_best_sell_price(exchange=exchange, minutes=5)
        if sell_book_top > 0:
            advice = PriceAdvisor.suggest_buy_price(
                sell_book_top=sell_book_top,
                buy_exchange=exchange,
                sell_exchange=exchange,
            )
            hint = "\n\n" + PriceAdvisor.format_buy_suggestion(advice)
        else:
            hint = "\n\n≡ƒÆí <i>╨¥╨╡╨╝╨░╤ö ╨┤╨░╨╜╨╕╤à ╤ü╤é╨░╨║╨░╨╜╤â ΓÇö ╨┐╤û╨┤╨║╨░╨╖╨║╨░ ╨▒╤â╨┤╨╡ ╨┐╤û╤ü╨╗╤Å ╨╜╨░╨║╨╛╨┐╨╕╤ç╨╡╨╜╨╜╤Å ╤ü╨╜╨░╨┐╤ê╨╛╤é╤û╨▓.</i>"

    icon = "≡ƒ¢Æ" if side == "BUY" else "≡ƒÆ╕"
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"{icon} <b>{side} ╨╜╨░ {exchange}</b>\n\n"
            f"╨Æ╨▓╨╡╨┤╨╕ ╤å╤û╨╜╤â (UAH ╨╖╨░ 1 USDT):{hint}",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(CreateAdStates.waiting_price)
async def on_ad_price(message: Message, state: FSMContext) -> None:
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("Γ¥î ╨Æ╨▓╨╡╨┤╨╕ ╤å╤û╨╜╤â (╨┤╨╛╨┤╨░╤é╨╜╨╡ ╤ç╨╕╤ü╨╗╨╛).")

    await state.update_data(ad_price=price)
    await state.set_state(CreateAdStates.waiting_amount)

    data = await state.get_data()
    side = data.get("ad_side", "SELL")
    exchange = data.get("ad_exchange", "")

    # ╨ƒ╤û╨┤╨║╨░╨╖╨║╨░ PriceAdvisor
    hint = ""
    from core.engine.price_advisor import PriceAdvisor
    if side == "SELL":
        advice = PriceAdvisor.suggest_sell_price(
            buy_price=price,
            amount_usdt=500.0,
            buy_exchange=exchange,
            sell_exchange=exchange,
        )
        hint = "\n\n" + PriceAdvisor.format_sell_suggestion(advice)
    elif side == "BUY":
        sell_book_top = 0.0
        if _db:
            sell_book_top = await _db.get_best_sell_price(exchange=exchange, minutes=5)
        if sell_book_top > 0 and price < sell_book_top:
            advice = PriceAdvisor.suggest_buy_price(
                sell_book_top=sell_book_top,
                buy_exchange=exchange,
                sell_exchange=exchange,
            )
            if price > advice["max_buy_price"]:
                hint = f"\n\nΓÜá∩╕Å ╨ª╤û╨╜╨░ {price:.2f} ╨▓╨╕╤ë╨╡ ╤Ç╨╡╨║╨╛╨╝╨╡╨╜╨┤╨╛╨▓╨░╨╜╨╛╨│╨╛ ╨╝╨░╨║╤ü. {advice['max_buy_price']:.2f}"
            else:
                hint = f"\n\nΓ£à ╨ª╤û╨╜╨░ ╨₧╨Ü (╨╝╨░╨║╤ü. ╤Ç╨╡╨║╨╛╨╝╨╡╨╜╨┤╨╛╨▓╨░╨╜╨░: {advice['max_buy_price']:.2f})"

    await message.answer(
        f"Γ£à ╨ª╤û╨╜╨░: <b>{price:.4f}</b> Γé┤{hint}\n\n"
        f"╨Æ╨▓╨╡╨┤╨╕ ╨║╤û╨╗╤î╨║╤û╤ü╤é╤î <b>USDT</b>:",
        reply_markup=back_to_main_kb(),
    )


@router.message(CreateAdStates.waiting_amount)
async def on_ad_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("Γ¥î ╨Æ╨▓╨╡╨┤╨╕ ╨║╤û╨╗╤î╨║╤û╤ü╤é╤î USDT (╨┤╨╛╨┤╨░╤é╨╜╨╡ ╤ç╨╕╤ü╨╗╨╛).")

    await state.update_data(ad_amount=amount)
    await state.set_state(CreateAdStates.waiting_min_limit)

    data = await state.get_data()
    side = data.get("ad_side", "SELL")
    price = data.get("ad_price", 0)
    exchange = data.get("ad_exchange", "")

    # ╨ƒ╨╡╤Ç╨╡╤Ç╨░╤à╤â╨╜╨╛╨║ ╨┐╤û╨┤╨║╨░╨╖╨║╨╕ ╨╖ ╤Ç╨╡╨░╨╗╤î╨╜╨╕╨╝ amount
    hint = ""
    from core.engine.price_advisor import PriceAdvisor
    if side == "SELL" and price > 0:
        advice = PriceAdvisor.suggest_sell_price(
            buy_price=price,
            amount_usdt=amount,
            buy_exchange=exchange,
            sell_exchange=exchange,
        )
        hint = (
            f"\n\n≡ƒÆí ╨ƒ╨╡╤Ç╨╡╤Ç╨░╤à╤â╨╜╨╛╨║ ╨┤╨╗╤Å {amount:.0f} USDT:\n"
            f"╨£╤û╨╜. ╤å╤û╨╜╨░ ╨┐╤Ç╨╛╨┤╨░╨╢╤â: <b>{advice['min_sell_price']:.4f}</b> Γé┤\n"
            f"╨ƒ╤Ç╨╛╤ä╤û╤é: +{advice['profit_at_min_uah']:.2f} Γé┤"
        )

    max_fiat = amount * price
    await message.answer(
        f"Γ£à ╨Ü╤û╨╗╤î╨║╤û╤ü╤é╤î: <b>{amount:.2f}</b> USDT (~{max_fiat:.0f} Γé┤){hint}\n\n"
        f"╨Æ╨▓╨╡╨┤╨╕ <b>╨╝╤û╨╜╤û╨╝╨░╨╗╤î╨╜╨╕╨╣ ╨╗╤û╨╝╤û╤é</b> ╤â╨│╨╛╨┤╨╕ (Γé┤):\n"
        f"<i>(╨╜╨░╨┐╤Ç. 500)</i>",
        reply_markup=back_to_main_kb(),
    )


@router.message(CreateAdStates.waiting_min_limit)
async def on_ad_min_limit(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        return await message.answer("Γ¥î ╨Æ╨▓╨╡╨┤╨╕ ╤ü╤â╨╝╤â (Γé┤).")
    await state.update_data(ad_min_limit=val)
    await state.set_state(CreateAdStates.waiting_max_limit)
    await message.answer(
        f"Γ£à ╨£╤û╨╜. ╨╗╤û╨╝╤û╤é: <b>{val:.0f}</b> Γé┤\n\n"
        f"╨Æ╨▓╨╡╨┤╨╕ <b>╨╝╨░╨║╤ü╨╕╨╝╨░╨╗╤î╨╜╨╕╨╣ ╨╗╤û╨╝╤û╤é</b> ╤â╨│╨╛╨┤╨╕ (Γé┤):",
        reply_markup=back_to_main_kb(),
    )


@router.message(CreateAdStates.waiting_max_limit)
async def on_ad_max_limit(message: Message, state: FSMContext) -> None:
    try:
        val = float(message.text.strip().replace(",", "."))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("Γ¥î ╨Æ╨▓╨╡╨┤╨╕ ╤ü╤â╨╝╤â (Γé┤).")
    data = await state.get_data()
    if val <= data.get("ad_min_limit", 0):
        return await message.answer("Γ¥î ╨£╨░╨║╤ü. ╨╗╤û╨╝╤û╤é ╨┐╨╛╨▓╨╕╨╜╨╡╨╜ ╨▒╤â╤é╨╕ ╨▒╤û╨╗╤î╤ê╨╡ ╨╝╤û╨╜╤û╨╝╨░╨╗╤î╨╜╨╛╨│╨╛.")
    await state.update_data(ad_max_limit=val)
    await state.set_state(CreateAdStates.waiting_banks)

    # ╨ƒ╨╛╨║╨░╨╖╤â╤ö╨╝╨╛ ╨▓╨╕╨▒╤û╤Ç ╨▒╨░╨╜╨║╤û╨▓
    from bot.keyboards import create_ad_banks_kb
    from config.banks import BANK_NAMES
    current_banks = list(BANK_NAMES.keys())[:3]  # default top 3
    await state.update_data(ad_banks=current_banks)
    await message.answer(
        f"Γ£à ╨£╨░╨║╤ü. ╨╗╤û╨╝╤û╤é: <b>{val:.0f}</b> Γé┤\n\n"
        f"╨₧╨▒╨╡╤Ç╨╕ ╨▒╨░╨╜╨║╨╕ ╨┤╨╗╤Å ╨╛╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å:",
        reply_markup=create_ad_banks_kb(BANK_NAMES, current_banks),
    )


@router.callback_query(F.data.startswith("ad:bank:"))
async def on_ad_bank_toggle(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[2]
    data = await state.get_data()
    selected = data.get("ad_banks", [])
    if code in selected:
        if len(selected) > 1:
            selected.remove(code)
        else:
            return await call.answer("Γ¥ù ╨£╤û╨╜╤û╨╝╤â╨╝ 1 ╨▒╨░╨╜╨║", show_alert=True)
    else:
        selected.append(code)
    await state.update_data(ad_banks=selected)
    from bot.keyboards import create_ad_banks_kb
    from config.banks import BANK_NAMES
    with suppress(TelegramBadRequest):
        await call.message.edit_reply_markup(
            reply_markup=create_ad_banks_kb(BANK_NAMES, selected)
        )
    await call.answer()


@router.callback_query(F.data == "ad:banks_done")
async def on_ad_banks_done(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateAdStates.waiting_terms)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "≡ƒô¥ <b>╨ú╨╝╨╛╨▓╨╕ ╤â╨│╨╛╨┤╨╕</b>\n\n"
            "╨¥╨░╨┐╨╕╤ê╨╕ ╤â╨╝╨╛╨▓╨╕ ╨╛╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å (╨░╨▒╨╛ <code>-</code> ╤ë╨╛╨▒ ╨┐╤Ç╨╛╨┐╤â╤ü╤é╨╕╤é╨╕):",
            reply_markup=back_to_main_kb(),
        )
    await call.answer()


@router.message(CreateAdStates.waiting_terms)
async def on_ad_terms(message: Message, state: FSMContext) -> None:
    terms = message.text.strip()
    if terms == "-":
        terms = ""
    await state.update_data(ad_terms=terms)
    await state.set_state(CreateAdStates.waiting_confirm)

    data = await state.get_data()
    from config.banks import BANK_NAMES
    bank_names = [BANK_NAMES.get(c, c) for c in data.get("ad_banks", [])]
    side_icon = "≡ƒ¢Æ" if data.get("ad_side") == "BUY" else "≡ƒÆ╕"

    from bot.keyboards import create_ad_confirm_kb
    await message.answer(
        f"{side_icon} <b>╨ƒ╤û╨┤╤é╨▓╨╡╤Ç╨┤╨╢╨╡╨╜╨╜╤Å ╨╛╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å</b>\n\n"
        f"╨æ╤û╤Ç╨╢╨░: <b>{data.get('ad_exchange')}</b>\n"
        f"╨í╤é╨╛╤Ç╨╛╨╜╨░: <b>{data.get('ad_side')}</b>\n"
        f"╨ª╤û╨╜╨░: <code>{data.get('ad_price', 0):.4f}</code> Γé┤\n"
        f"╨Ü╤û╨╗╤î╨║╤û╤ü╤é╤î: <code>{data.get('ad_amount', 0):.2f}</code> USDT\n"
        f"╨¢╤û╨╝╤û╤é╨╕: <b>{data.get('ad_min_limit', 0):.0f} ΓÇö {data.get('ad_max_limit', 0):.0f}</b> Γé┤\n"
        f"╨æ╨░╨╜╨║╨╕: {', '.join(bank_names)}\n"
        f"╨ú╨╝╨╛╨▓╨╕: {terms or 'ΓÇö'}\n\n"
        f"ΓÜá∩╕Å ╨¥╨░╤é╨╕╤ü╨╜╨╕ Γ£à ╨┤╨╗╤Å ╤ü╤é╨▓╨╛╤Ç╨╡╨╜╨╜╤Å.",
        reply_markup=create_ad_confirm_kb(),
    )


@router.callback_query(F.data == "ad:confirm")
async def on_ad_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    exchange = data.get("ad_exchange", "")
    side = data.get("ad_side", "")

    with suppress(TelegramBadRequest):
        await call.message.edit_text(f"ΓÅ│ <b>╨í╤é╨▓╨╛╤Ç╤Ä╤Ä {side} ╨╛╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å ╨╜╨░ {exchange}ΓÇª</b>", reply_markup=None)

    try:
        # ╨ù╨░╨▓╨░╨╜╤é╨░╨╢╤â╤ö╨╝╨╛ credentials ╤Ä╨╖╨╡╤Ç╨░
        user_id = call.from_user.id
        creds = {}
        if _db:
            creds = await _db.get_credentials(exchange=exchange, user_id=user_id) or {}
            if not creds.get("api_key"):
                creds = await _db.get_credentials(exchange=exchange, user_id=0) or {}
        if not creds.get("api_key"):
            raise RuntimeError(f"╨¥╨╡╨╝╨░╤ö API ╨║╨╗╤Ä╤ç╤û╨▓ ╨┤╨╗╤Å {exchange}. ╨ƒ╤û╨┤╨║╨╗╤Ä╤ç╤û╤é╤î ╤ç╨╡╤Ç╨╡╨╖ /connect.")

        # ╨Æ╨╕╨║╨╛╤Ç╨╕╤ü╤é╨╛╨▓╤â╤ö╨╝╨╛ RouteExecutor ╨┤╨╗╤Å ╤ü╤é╨▓╨╛╤Ç╨╡╨╜╨╜╤Å ╨╛╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å
        from core.engine.route_executor import RouteExecutor
        executor = RouteExecutor()

        result = await executor.create_maker_ad(
            exchange=exchange,
            action=side,
            price=data.get("ad_price", 0),
            amount_usdt=data.get("ad_amount", 0),
            min_order_uah=data.get("ad_min_limit", 500),
            max_order_uah=data.get("ad_max_limit"),
            credentials=creds,
            payment_methods=data.get("ad_banks", []),
        )

        if result.get("success"):
            ad_id = result.get("ad_id", "ΓÇö")
            text = (
                f"Γ£à <b>╨₧╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å ╤ü╤é╨▓╨╛╤Ç╨╡╨╜╨╛!</b>\n\n"
                f"ID: <code>{ad_id}</code>\n"
                f"╨æ╤û╤Ç╨╢╨░: {exchange} | {side}"
            )

            # ≡ƒÜÇ ╨É╨▓╤é╨╛╨╖╨░╨┐╤â╤ü╨║ AdRepricer (╤Å╨║╤ë╨╛ ╤å╨╡ SELL ╨╛╨│╨╛╨╗╨╛╤ê╨╡╨╜╨╜╤Å)
            if side == "SELL" and ad_id and ad_id != "ΓÇö":
                try:
                    from core.engine.ad_repricer import AdRepricer

                    buy_price = data.get("ad_price", 0)
                    amount_usdt = data.get("ad_amount", 500)

                    async def _notify_tg(msg: str) -> None:
                        if _notifier:
                            try:
                                await _notifier._send_with_retry(msg, chat_id=call.message.chat.id)
                            except Exception as e:
                                logger.error("AdRepricer notify error: %s", e)

                    repricer = AdRepricer(
                        session_id=0,
                        sell_ad_id=str(ad_id),
                        exchange=exchange,
                        buy_price=buy_price,
                        amount_usdt=amount_usdt,
                        network_fee=0.0,
                        min_margin=0.003,
                        notify_cb=_notify_tg,
                    )

                    from infrastructure.http.bybit_p2p_client import BybitP2PClient

                    async def _fetch_book_top(ex: str, _ad_id: str):
                        client = BybitP2PClient()
                        client.set_credentials(creds.get("api_key", ""), creds.get("api_secret", ""))
                        async with client:
                            return await client.fetch_p2p_book_top(side=1, exclude_ad_id=_ad_id)

                    async def _update_price(ex: str, _ad_id: str, new_price: float):
                        from core.engine.route_executor import RouteExecutor
                        _exec = RouteExecutor()
                        return await _exec.update_maker_ad_price(ex, _ad_id, new_price, creds)

                    import asyncio as _aio
                    _active_repricers[str(ad_id)] = _aio.create_task(
                        repricer.watch(_fetch_book_top, _update_price, _db),
                        name=f"repricer_{ad_id}",
                    )
                    text += "\n\n≡ƒôè <i>AdRepricer ╨╖╨░╨┐╤â╤ë╨╡╨╜╨╛ ΓÇö ╤å╤û╨╜╨░ ╨░╨▓╤é╨╛╨╝╨░╤é╨╕╤ç╨╜╨╛ ╨╛╨╜╨╛╨▓╨╗╤Ä╤ö╤é╤î╤ü╤Å.</i>"
                except Exception as e:
                    logger.warning("╨¥╨╡ ╨▓╨┤╨░╨╗╨╛╤ü╤î ╨╖╨░╨┐╤â╤ü╤é╨╕╤é╨╕ AdRepricer: %s", e)
                    text += f"\n\nΓÜá∩╕Å <i>AdRepricer ╨╜╨╡ ╨╖╨░╨┐╤â╤ë╨╡╨╜╨╛: {str(e)[:100]}</i>"

            # ≡ƒÜÇ ╨É╨▓╤é╨╛╨╖╨░╨┐╤â╤ü╨║ MakerAdMonitor
            if ad_id and ad_id != "ΓÇö":
                try:
                    if _maker_monitor:
                        chat_id = call.message.chat.id
                        import asyncio as _aio
                        _aio.create_task(
                            _maker_monitor.start_watching(user_id, chat_id, exchange, str(ad_id)),
                            name=f"maker_watch_{ad_id}",
                        )
                        text += "\n≡ƒöö <i>╨£╨╛╨╜╤û╤é╨╛╤Ç╨╕╨╜╨│ ╨▓╤à╤û╨┤╨╜╨╕╤à ╨╛╤Ç╨┤╨╡╤Ç╤û╨▓ ╨╖╨░╨┐╤â╤ë╨╡╨╜╨╛.</i>"
                except Exception as e:
                    logger.warning("╨¥╨╡ ╨▓╨┤╨░╨╗╨╛╤ü╤î ╨╖╨░╨┐╤â╤ü╤é╨╕╤é╨╕ MakerAdMonitor: %s", e)

        else:
            text = f"Γ¥î <b>╨ƒ╨╛╨╝╨╕╨╗╨║╨░:</b> {result.get('error', 'Unknown')}"
    except NotImplementedError as e:
        text = f"Γ¥î {e}"
    except Exception as e:
        logger.error("ad:confirm error: %s", e, exc_info=True)
        text = f"Γ¥î <b>╨ƒ╨╛╨╝╨╕╨╗╨║╨░:</b> <code>{str(e)[:200]}</code>"

    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=keyboards.back_to_main_kb())
    await call.answer()


@router.callback_query(F.data == "ad:cancel")
async def on_ad_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, is_active = await _generate_dashboard_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text, reply_markup=main_menu_kb(is_active, is_muted(), _is_admin(call.from_user.id))
        )
    await call.answer("╨í╨║╨░╤ü╨╛╨▓╨░╨╜╨╛")


# =========================================================================
# ΓÜí TAKER EXECUTE ΓÇö ╤ê╨▓╨╕╨┤╨║╨╡ ╨▓╤û╨┤╨║╤Ç╨╕╤é╤é╤Å ╨╛╤Ç╨┤╨╡╤Ç╨░
# =========================================================================

from core.utils.cache import TTLCache as _TTLCache
_taker_order_cache: _TTLCache = _TTLCache(ttl_seconds=300.0, max_size=500)


@router.callback_query(F.data.startswith("taker:take:"))
async def on_taker_take(call: CallbackQuery, state: FSMContext) -> None:
    if not _single_leg_executor:
        return await call.answer("Γ¥î SingleLegExecutor ╨╜╨╡ ╨┐╤û╨┤╨║╨╗╤Ä╤ç╨╡╨╜╨╛!", show_alert=True)

    cache_key = call.data.split(":", 2)[2]
    data = _taker_order_cache.get(cache_key)
    if not data:
        return await call.answer("Γ¥î ╨₧╤Ç╨┤╨╡╤Ç ╨╖╨░╤ü╤é╨░╤Ç╤û╨▓ (>5 ╤à╨▓).", show_alert=True)

    price = data["price"]
    min_usdt = data["min_limit"] / price if price > 0 else 0
    max_usdt = data["max_limit"] / price if price > 0 else 0
    action = data.get("action", "BUY")

    await state.update_data(
        taker_cache_key=cache_key, taker_action=action,
        taker_ad_id=data["ad_id"], taker_exchange=data["exchange"],
        taker_price=price, taker_merchant_id=data["merchant_id"],
        taker_bank=data.get("bank", ""),
        taker_min_usdt=min_usdt, taker_max_usdt=max_usdt,
    )
    await state.set_state(TakerExecuteStates.waiting_amount)

    icon = "≡ƒ¢Æ" if action == "BUY" else "≡ƒÆ╕"
    label = "╨Ü╤â╨┐╤û╨▓╨╗╤Å" if action == "BUY" else "╨ƒ╤Ç╨╛╨┤╨░╨╢"
    text = (
        f"{icon} <b>{label} (Taker)</b>\n\n"
        f"╨æ╤û╤Ç╨╢╨░: <b>{data['exchange']}</b>\n"
        f"╨ª╤û╨╜╨░: <code>{price:.4f}</code> UAH\n"
        f"╨¢╤û╨╝╤û╤é╨╕: <b>{min_usdt:.1f} ΓÇö {max_usdt:.1f} USDT</b>\n\n"
        f"≡ƒæç ╨Æ╨▓╨╡╨┤╨╕ ╤ü╤â╨╝╤â ╨▓ <b>USDT</b> (╨░╨▒╨╛ <code>max</code>):"
    )
    await call.message.answer(text, reply_markup=back_to_main_kb())
    await call.answer()


@router.message(TakerExecuteStates.waiting_amount)
async def on_taker_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    min_usdt = data.get("taker_min_usdt", 0)
    max_usdt = data.get("taker_max_usdt", 0)
    raw = message.text.strip().lower()

    try:
        amount = max_usdt if raw == "max" else float(raw.replace(",", "."))
        if not (min_usdt - 0.001 <= amount <= max_usdt + 0.001):
            raise ValueError
    except ValueError:
        return await message.answer(
            f"Γ¥î ╨í╤â╨╝╨░ ╨▓╤û╨┤ <b>{min_usdt:.1f}</b> ╨┤╨╛ <b>{max_usdt:.1f}</b> (╨░╨▒╨╛ 'max'):"
        )

    await state.update_data(taker_amount=amount)
    await state.set_state(TakerExecuteStates.waiting_confirm)

    action = data.get("taker_action", "BUY")
    icon = "≡ƒ¢Æ" if action == "BUY" else "≡ƒÆ╕"
    label = "╨Ü╤â╨┐╤û╨▓╨╗╤Å" if action == "BUY" else "╨ƒ╤Ç╨╛╨┤╨░╨╢"
    price = data.get("taker_price", 0)
    fiat_amount = amount * price

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Γ£à ╨ƒ╤û╨┤╤é╨▓╨╡╤Ç╨┤╨╕╤é╨╕", callback_data="taker:confirm"),
        InlineKeyboardButton(text="Γ¥î ╨í╨║╨░╤ü╤â╨▓╨░╤é╨╕", callback_data="taker:cancel"),
    )

    await message.answer(
        f"{icon} <b>╨ƒ╤û╨┤╤é╨▓╨╡╤Ç╨┤╨╢╨╡╨╜╨╜╤Å {label}</b>\n\n"
        f"╨æ╤û╤Ç╨╢╨░: <b>{data.get('taker_exchange')}</b>\n"
        f"╨₧╨▒'╤ö╨╝: <code>{amount:.2f} USDT</code> (~{fiat_amount:.0f} Γé┤)\n"
        f"╨ª╤û╨╜╨░: <code>{price:.4f}</code>\n\n"
        f"ΓÜá∩╕Å <i>╨¥╨░╤é╨╕╤ü╨╜╨╕ Γ£à ΓÇö ╨╛╤Ç╨┤╨╡╤Ç ╨▓╤û╨┤╨║╤Ç╨╕╤ö╤é╤î╤ü╤Å ╨░╨▓╤é╨╛╨╝╨░╤é╨╕╤ç╨╜╨╛.\n"
        f"╨ƒ╤û╤ü╨╗╤Å ╤å╤î╨╛╨│╨╛ ╨╛╤é╤Ç╨╕╨╝╨░╤ö╤ê ╨┐╤Ç╤Å╨╝╨╡ ╨┐╨╛╤ü╨╕╨╗╨░╨╜╨╜╤Å.</i>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.in_({"taker:confirm", "taker:cancel"}))
async def on_taker_confirm(call: CallbackQuery, state: FSMContext) -> None:
    if call.data == "taker:cancel":
        await state.clear()
        with suppress(TelegramBadRequest):
            await call.message.edit_text("≡ƒÜ½ ╨í╨║╨░╤ü╨╛╨▓╨░╨╜╨╛.", reply_markup=keyboards.back_to_main_kb())
        return await call.answer("╨í╨║╨░╤ü╨╛╨▓╨░╨╜╨╛.")

    data = await state.get_data()
    await state.clear()

    if not _single_leg_executor:
        return await call.answer("Γ¥î SingleLegExecutor ╨╜╨╡ ╨┐╤û╨┤╨║╨╗╤Ä╤ç╨╡╨╜╨╛!", show_alert=True)

    action = data.get("taker_action", "BUY")
    amount_usdt = data.get("taker_amount", 0)
    exchange = data.get("taker_exchange", "")

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"ΓÅ│ <b>╨Æ╤û╨┤╨║╤Ç╨╕╨▓╨░╤Ä {action} ╨╛╤Ç╨┤╨╡╤Ç ╨╜╨░ {exchange}ΓÇª</b>", reply_markup=None,
        )

    try:
        if action == "BUY":
            result = await _single_leg_executor.execute_single_buy(
                exchange=exchange, ad_id=data.get("taker_ad_id", ""),
                price=data.get("taker_price", 0), amount_usdt=amount_usdt,
                merchant_id=data.get("taker_merchant_id", ""),
                owner_user_id=call.from_user.id,
                payment_method=data.get("taker_bank", ""),
            )
        else:
            result = await _single_leg_executor.execute_single_sell(
                exchange=exchange, ad_id=data.get("taker_ad_id", ""),
                price=data.get("taker_price", 0), amount_usdt=amount_usdt,
                merchant_id=data.get("taker_merchant_id", ""),
                owner_user_id=call.from_user.id,
                payment_method=data.get("taker_bank", ""),
            )

        if result["success"]:
            order_id = result.get("order_id", "")
            from core.analytics.merchant_profile import build_order_url
            order_url = build_order_url(exchange, order_id)

            text = (
                f"Γ£à <b>{action} ╨╛╤Ç╨┤╨╡╤Ç ╨▓╤û╨┤╨║╤Ç╨╕╤é╨╛!</b>\n\n"
                f"╨æ╤û╤Ç╨╢╨░: {exchange}\n"
                f"Order ID: <code>{order_id}</code>\n"
                f"Trade #: {result.get('trade_id', 'ΓÇö')}\n"
            )
            if result.get("warning"):
                text += f"\nΓÜá∩╕Å {result['warning']}\n"
            text += "\n<b>╨ó╨╡╨┐╨╡╤Ç ╨╖╨░╨▓╨╡╤Ç╤ê╨╕ ╤â╨│╨╛╨┤╤â ╨▓╤Ç╤â╤ç╨╜╤â ≡ƒæç</b>"

            kb_rows = []
            if order_url:
                kb_rows.append([InlineKeyboardButton(
                    text=f"≡ƒöù ╨Æ╤û╨┤╨║╤Ç╨╕╤é╨╕ ╨╛╤Ç╨┤╨╡╤Ç ╨╜╨░ {exchange}", url=order_url
                )])
            kb_rows.append([InlineKeyboardButton(text="≡ƒöÖ ╨Æ ╨╝╨╡╨╜╤Ä", callback_data="menu:main")])
            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        else:
            text = f"Γ¥î <b>╨ƒ╨╛╨╝╨╕╨╗╨║╨░ {action}:</b> {result.get('error', 'Unknown')}"
            if result.get("warning"):
                text += f"\n\nΓÜá∩╕Å {result['warning']}"
            kb = keyboards.back_to_main_kb()

    except Exception as e:
        logger.error("taker:confirm error: %s", e, exc_info=True)
        text = f"Γ¥î <b>╨Ü╤Ç╨╕╤é╨╕╤ç╨╜╨░ ╨┐╨╛╨╝╨╕╨╗╨║╨░:</b> <code>{str(e)[:200]}</code>"
        kb = keyboards.back_to_main_kb()

    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

