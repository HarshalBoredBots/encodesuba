from pyrogram import Client, filters, enums
from pyrogram.types import Message

_worker_instance = None
_admin_ids = []


def set_worker_instance(worker):
    global _worker_instance
    _worker_instance = worker


def set_admin_ids(ids):
    global _admin_ids
    _admin_ids = list(ids)


def get_worker_instance():
    return _worker_instance


def get_admin_ids():
    return _admin_ids


async def _check_access(client, message: Message) -> bool:
    user_id = message.from_user.id
    try:
        await client.get_chat(user_id)
    except Exception:
        bot_username = (await client.get_me()).username
        await message.reply_text(
            f"⚠️ Please start the bot in DM first.\n"
            f"👉 @{bot_username} — press <b>Start</b>, then try again.",
            parse_mode=enums.ParseMode.HTML,
        )
        return False
    return True


def setup_cancel_handlers(app: Client, task_queue, config):

    allowed_group_filter = filters.chat(config.allowed_group_ids)

    @app.on_message(filters.command(["cancel", "c"]) & allowed_group_filter)
    async def cancel_command(client: Client, message: Message):
        if not await _check_access(client, message):
            return

        if len(message.command) < 2:
            await message.reply_text(
                "Usage: <code>/cancel &lt;task_id&gt;</code>\n"
                "Get the task ID from /status.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        task_id_part = message.command[1].strip()

        # ── Match task by prefix ──────────────────────────────────────────────
        matching_task_id = None
        for tid in list(task_queue.tasks.keys()):
            if tid.startswith(task_id_part):
                matching_task_id = tid
                break

        task = task_queue.get_task(matching_task_id)
        if not task:
            await message.reply_text(
                f"Task <code>{task_id_part}</code> no longer exists.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        # ── Ownership check ───────────────────────────────────────────────────
        user_id = message.from_user.id
        is_admin = user_id in _admin_ids
        if not is_admin and task.get("user_id") != user_id:
            await message.reply_text(
                "❌ You can only cancel your own tasks.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        worker = get_worker_instance()
        if not worker:
            await message.reply_text(
                "Worker is not available.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        task_status = task.get("status", "")
        if task_status == "queued":
            task_queue.remove_task(matching_task_id)
            await message.reply_text(
                f"✅ Task <code>{task_id_part}</code> cancelled (was queued, not yet started).",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        # ── Active task — delegate to worker ─────────────────────────────────
        try:
            await worker.cancel_task(matching_task_id)
            await message.reply_text(
                f"✅ Task <code>{task_id_part}</code> cancelled.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            await message.reply_text(
                f"Failed to cancel task: <code>{e}</code>",
                parse_mode=enums.ParseMode.HTML,
            )