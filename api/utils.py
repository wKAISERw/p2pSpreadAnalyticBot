# api/utils.py
import re
import json
import aiosqlite

def to_camel(snake_str: str) -> str:
    components = snake_str.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])

def dict_to_camel(obj):
    if isinstance(obj, list):
        return [dict_to_camel(item) for item in obj]
    elif isinstance(obj, dict):
        return {to_camel(k): dict_to_camel(v) for k, v in obj.items()}
    return obj

def to_snake(camel_str: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', camel_str).lower()

def dict_to_snake(obj):
    if isinstance(obj, list):
        return [dict_to_snake(item) for item in obj]
    elif isinstance(obj, dict):
        return {to_snake(k): dict_to_snake(v) for k, v in obj.items()}
    return obj

async def fix_bank_codes_in_db(db_path: str):
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT user_id, buy_bank_codes FROM scanner_users") as cur:
            rows = await cur.fetchall()
        for user_id, raw in rows:
            if raw and raw.strip().startswith("["):
                try:
                    codes = json.loads(raw)
                    csv = ",".join(str(c) for c in codes)
                    await conn.execute(
                        "UPDATE scanner_users SET buy_bank_codes = ? WHERE user_id = ?",
                        (csv, user_id)
                    )
                    print(f"Fixed user {user_id}: {raw} → {csv}")
                except Exception as e:
                    print(f"Skip user {user_id}: {e}")
        await conn.commit()