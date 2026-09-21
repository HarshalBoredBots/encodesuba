import os
from functools import wraps
from typing import List, Callable
from dotenv import load_dotenv

load_dotenv()

def get_admin_ids() -> List[int]:
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    if not admin_ids_str:
        return []
    try:
        return [int(admin_id.strip()) for admin_id in admin_ids_str.split(",") if admin_id.strip()]
    except ValueError:
        return []


def admin_only(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        user_id = message.from_user.id
        admin_ids = get_admin_ids()
        if user_id not in admin_ids:
            await message.reply_text("❌ You are not authorized to use this command.")
            return
        return await func(client, message, *args, **kwargs)
    return wrapper
