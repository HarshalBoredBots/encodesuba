from pyrogram import Client, filters, enums
from pyrogram.types import Message


async def _check_access(client, message: Message, config) -> bool:
    user_id = message.from_user.id
    if user_id not in config.admin_ids:
        await message.reply_text("Dukhi Atma!", parse_mode=enums.ParseMode.HTML)
        return False
    try:
        await client.get_chat(user_id)
    except Exception:
        bot_username = (await client.get_me()).username
        await message.reply_text(
            f"Please start the bot in DM first.\n"
            f"@{bot_username} - press <b>Start</b>, then try again.",
            parse_mode=enums.ParseMode.HTML,
        )
        return False
    return True


def _find_task_id(task_queue, task_id_part: str) -> str | None:
    matches = [tid for tid in task_queue.tasks.keys() if tid.startswith(task_id_part)]
    return matches[0] if len(matches) == 1 else None


def setup_shift_handlers(app: Client, task_queue, config):
    allowed_group_filter = filters.chat(config.allowed_group_ids)

    @app.on_message(filters.command(["shift"]) & allowed_group_filter)
    async def shift_command(client: Client, message: Message):
        if not await _check_access(client, message, config):
            return

        if len(message.command) < 3:
            await message.reply_text(
                "Usage: <code>/shift &lt;task_id&gt; &lt;position&gt;</code>\n"
                "Position <code>2</code> or higher can be used. Positions <code>0</code> and <code>1</code> are locked.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        task_id_part = message.command[1].strip()
        position_part = message.command[2].strip()

        try:
            next_position = int(position_part)
        except ValueError:
            await message.reply_text(
                "Position must be a number. Example: <code>/shift ab12cd34 2</code>",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        if next_position < 2:
            await message.reply_text(
                "Position must be <code>2</code> or higher. Positions <code>0</code> and <code>1</code> are locked.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        matching_task_id = _find_task_id(task_queue, task_id_part)
        if not matching_task_id:
            await message.reply_text(
                f"Task <code>{task_id_part}</code> was not found, or the prefix matches more than one task.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        ok, reason, final_position = task_queue.shift_task(matching_task_id, next_position)
        if not ok:
            await message.reply_text(
                f"Cannot shift task <code>{matching_task_id[:8]}</code>: {reason}",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        task = task_queue.get_task(matching_task_id) or {}
        filename = (
            task.get("output_filename")
            or task.get("original_file_name")
            or task.get("file_name")
            or "Unknown"
        )
        await message.reply_text(
            f"Shifted <code>{matching_task_id[:8]}</code> to next-queue position <b>{final_position}</b>.\n"
            f"File: <code>{filename}</code>\n"
            "Use <code>/status</code> to see the updated order.",
            parse_mode=enums.ParseMode.HTML,
        )
