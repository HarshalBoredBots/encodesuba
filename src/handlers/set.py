import os

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode


def setup_set_handlers(app: Client, user_settings, config):

    allowed_filter = filters.chat(config.allowed_group_ids) | filters.private

    @app.on_message(filters.command(["st", "setthumb"]) & allowed_filter)
    async def set_thumbnail_command(client: Client, message: Message):
        user_id = message.from_user.id

        if user_id not in config.admin_ids:
            await message.reply_text("Dukhi Atma!😔")
            return

        replied = message.reply_to_message
        if not replied:
            await message.reply_text(
                "Reply to a photo with <code>/st</code> to set it as your thumbnail.",
                parse_mode=ParseMode.HTML,
            )
            return

        is_photo   = bool(replied.photo)
        is_img_doc = (
            replied.document
            and (replied.document.mime_type or "").startswith("image/")
        )

        if not is_photo and not is_img_doc:
            await message.reply_text(
                "The replied message must be a photo or an image file.",
                parse_mode=ParseMode.HTML,
            )
            return

        try:
            thumb_dir = config.paths.thumbnails
            os.makedirs(thumb_dir, exist_ok=True)
            dest_path = os.path.join(thumb_dir, f"{user_id}.jpg")

            downloaded = await client.download_media(replied, file_name=dest_path)

            if not downloaded or not os.path.exists(downloaded):
                await message.reply_text("Failed to download the image. Please try again.")
                return

            us = user_settings(user_id)
            us.set_thumbnail(os.path.abspath(downloaded))

            await message.reply_text(
                "✅ Thumbnail saved successfully.",
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:
            await message.reply_text(
                f"<b>Error saving thumbnail:</b> <code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )