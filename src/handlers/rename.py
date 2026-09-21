import copy
import os
import re
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import Message
import logging

from src.utils.dc_checker import is_dc_allowed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi",
    ".mpeg", ".mpg", ".wmv", ".flv", ".3gp",
}

_SUPPORTED_PLACEHOLDERS = {"season", "episode"}
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


async def _safe_edit(msg, text: str):
    try:
        await msg.edit_text(text)
    except MessageNotModified:
        pass
    except FloodWait as e:
        import asyncio
        await asyncio.sleep(e.value)
        try:
            await msg.edit_text(text)
        except Exception:
            pass
    except Exception:
        pass


async def fetch_media_group(client: Client, chat_id: int, replied: Message) -> list:
    media_group_id = replied.media_group_id
    start_id = max(1, replied.id - 3)
    end_id   = replied.id + 20
    ids      = list(range(start_id, end_id + 1))
    try:
        messages = await client.get_messages(chat_id, ids)
    except Exception as exc:
        logger.error(f"[encode] fetch_media_group error: {exc}")
        return []
    group = [
        m for m in messages
        if m
        and not getattr(m, "empty", True)
        and m.media_group_id == media_group_id
        and (m.video or m.document)
    ]
    group.sort(key=lambda m: m.id)
    return group


async def fetch_sequential_messages(
    client: Client, chat_id: int, start_id: int, count: int
) -> list:
    ids = list(range(start_id, start_id + count))
    try:
        messages = await client.get_messages(chat_id, ids)
    except Exception as exc:
        logger.error(f"[rename] fetch_sequential_messages error: {exc}")
        return []
    result = [
        m for m in messages
        if m and not getattr(m, "empty", True) and (m.video or m.document)
    ]
    result.sort(key=lambda m: m.id)
    return result


async def _check_access(client, message: Message, config) -> bool:
    user_id = message.from_user.id
    if user_id not in config.admin_ids:
        await message.reply_text("Dukhi Atma!😔")
        return False
    try:
        await client.get_chat(user_id)
    except Exception:
        await message.reply_text(
            "⚠️ Please start the bot in DM before giving tasks here."
        )
        return False
    return True


def _valid_extension(filename: str) -> bool:
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    return bool(ext) and ext in ALLOWED_VIDEO_EXTENSIONS


def _file_info(media_msg: Message):
    if media_msg.video:
        v = media_msg.video
        return (
            v.file_id,
            v.file_name or f"video_{v.file_id[:8]}.mp4",
            v.file_size,
        )
    d = media_msg.document
    ext = os.path.splitext(d.file_name or "")[1].lower() or ".mkv"
    return (
        d.file_id,
        d.file_name or f"video_{d.file_id[:8]}{ext}",
        d.file_size,
    )


def _source_thumbnail_file_id(msg: Message) -> str:
    media = msg.video or msg.document
    thumbs = getattr(media, "thumbs", None) or []
    if not thumbs:
        return ""
    return getattr(thumbs[-1], "file_id", "") or ""


def _is_video_message(msg: Message) -> bool:
    if msg.video:
        return True
    if msg.document:
        ext = os.path.splitext(msg.document.file_name or "")[1].lower()
        return ext in ALLOWED_VIDEO_EXTENSIONS
    return False


def _parse_rename_command(message):
    message_text = message.text or ""
    text = re.sub(r"^/\S+\s*", "", message_text).strip()

    is_batch    = False
    batch_count = None  

    if text.startswith("-b"):
        rest = text[2:]  
        num_match = re.match(r"^\s+(\d+)\s*(.*)", rest, re.DOTALL)
        if num_match:
            batch_count = int(num_match.group(1))
            if batch_count < 2:
                return False, None, None, "`-b <N>` requires N ≥ 2."
            rest = num_match.group(2).strip()
        else:
            rest = rest.lstrip()

        if not rest:
            return True, None, None, "Please provide a filename template after `-b`."

        is_batch = True
        text = rest

    if not text:
        return is_batch, batch_count, None, "Please provide a filename."
    if (text.startswith('"') and text.endswith('"')) or \
       (text.startswith("'") and text.endswith("'")):
        text = text[1:-1]

    if not text:
        return is_batch, batch_count, None, "Filename cannot be empty."

    return is_batch, batch_count, text, None


def _validate_batch_template(template: str):
    found = {m.group(1) for m in _PLACEHOLDER_RE.finditer(template)}
    unsupported = found - _SUPPORTED_PLACEHOLDERS
    if unsupported:
        bad = ", ".join(f"`{{{p}}}`" for p in sorted(unsupported))
        return False, (
            f"Unsupported placeholder(s): {bad}\n"
            "Only `{season}` and `{episode}` are allowed in batch rename filenames."
        )
    return True, None


def _resolve_template(template: str, season_str: str, ep_str: str) -> str:
    filename = template
    filename = filename.replace("{season}",  season_str)
    filename = filename.replace("{episode}", ep_str)
    return filename


def _build_task(
    *,
    message: Message,
    file_id: str,
    original_file_name: str,
    file_size: int,
    source_thumbnail_file_id: str,
    output_filename: str,
    settings: dict,
    watermark: dict,
    created_at: str,
    batch: bool = False,
) -> dict:
    job = {
        "resolution":      "rename",
        "output_filename": output_filename,
        "processing_mode": "rename",
        "audio_bitrate":   None,
        "metadata":        settings.get("metadata", {}),
        "thumbnail_path":  settings_obj.get_thumbnail_path() or "",
        "send_type":       settings.get("send_type", "media"),
    }
    return {
        "user_id":                   message.from_user.id,
        "first_name":                message.from_user.first_name,
        "username":                  message.from_user.username,
        "chat_id":                   message.chat.id,
        "source_chat_id":            message.chat.id,
        "message_id":                message.id,
        "file_id":                   file_id,
        "original_file_name":        original_file_name,
        "requested_output_filename": output_filename,
        "output_filename":           output_filename,
        "resolution":                "rename",
        "created_at":                created_at,
        "file_size":                 file_size,
        "send_type":                 settings.get("send_type", "media"),
        "auto_detect_thumb":         bool(settings.get("auto_detect_thumb", False)),
        "source_thumbnail_file_id":  source_thumbnail_file_id,
        "resolutions":               ["rename"],
        "jobs":                      [job],
        "total_jobs":                1,
        "current_job":               0,
        "current_stage":             "queued",
        "thumbnail_path":            settings_obj.get_thumbnail_path() or "",
        "watermark":                 {},
        "settings_snapshot":         settings,
        "task_type":                 "rename",
        "batch_rename":              batch,
    }


async def _process_single_rename(
    client: Client,
    message: Message,
    filename: str,
    task_queue,
    user_settings,
):
    if not message.reply_to_message:
        await message.reply_text(
            'Reply to a video file.\nUsage: `/rename "movie.mkv"`'
        )
        return

    replied = message.reply_to_message

    if not _is_video_message(replied):
        await message.reply_text(
            f"Only video files are supported.\n"
            f"Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"
        )
        return

    if not _valid_extension(filename):
        await message.reply_text(
            f"Invalid file extension.\n"
            f"Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"
        )
        return

    file_id, original_file_name, file_size = _file_info(replied)

    if not is_dc_allowed(file_id):
        return

    user_id      = message.from_user.id
    settings_obj = user_settings(user_id)
    settings     = copy.deepcopy(settings_obj.get())
    watermark    = settings_obj.get_watermark()

    created_at = datetime.utcnow().isoformat()

    task_data = _build_task(
        message=message,
        file_id=file_id,
        original_file_name=original_file_name,
        file_size=file_size,
        source_thumbnail_file_id=_source_thumbnail_file_id(replied),
        output_filename=filename,
        settings=settings,
        watermark=watermark,
        created_at=created_at,
        batch=False,
    )

    task_id  = task_queue.create_task(task_data)
    position = task_queue.get_queue_position(task_id)

    mode = "rename + metadata"

    await message.reply_text(
        f"Task `{filename}` queued at position **[{position}]**\n"
        f"Task ID : `{task_id}`\n"
        f"**Mode:** {mode}\n"
        f"**Output will be delivered to your DM.** Please wait patiently."
    )


async def _process_batch_rename(
    client: Client,
    message: Message,
    template: str,
    batch_count,     
    task_queue,
    user_settings,
):
    if not message.reply_to_message:
        await message.reply_text(
            "Reply to the **first** file.\n\n"
            "Usage:\n"
            "  Media group : `/rename -b [S{season}-E{episode}] Show Name.mkv`\n"
            "  Sequential  : `/rename -b 6 [S{season}-E{episode}] Show Name.mkv`"
        )
        return

    replied = message.reply_to_message

    ok, err = _validate_batch_template(template)
    if not ok:
        await message.reply_text(f"❌ {err}")
        return

    if not _valid_extension(template):
        await message.reply_text(
            f"Invalid file extension in template.\n"
            f"Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"
        )
        return

    user_id      = message.from_user.id
    settings_obj = user_settings(user_id)
    settings     = copy.deepcopy(settings_obj.get())
    watermark    = settings_obj.get_watermark()

    season_raw  = str(settings.get("default_season",        "1"))
    episode_raw = str(settings.get("default_start_episode", "1"))

    season_width = max(len(season_raw), 1)
    ep_width     = max(len(episode_raw), 2)
    season_int   = int(season_raw)
    ep_int       = int(episode_raw)
    season_str   = str(season_int).zfill(season_width)

    if batch_count is not None:
        status_msg  = await message.reply_text(f"⏳ Fetching {batch_count} messages…")
        raw_msgs    = await fetch_sequential_messages(
            client, message.chat.id, replied.id, batch_count
        )
    else:
        if not replied.media_group_id:
            await message.reply_text(
                "The replied message is not part of a media group (album).\n"
                "Send your files together as an album and reply to the first one,\n"
                "or use `-b <N>` to grab N individual messages starting from the replied one."
            )
            return
        status_msg  = await message.reply_text("⏳ Fetching media group…")
        raw_msgs    = await fetch_media_group(client, message.chat.id, replied)

    if not raw_msgs:
        await _safe_edit(
            status_msg,
            "Could not find any media messages.\n"
            + ("Make sure you replied to the first file of the group." if batch_count is None
               else f"No video/document messages found in the next {batch_count} message IDs.")
        )
        return

    valid_files: list[Message] = []
    skipped = 0
    for mg_msg in raw_msgs:
        if _is_video_message(mg_msg) and is_dc_allowed(_file_info(mg_msg)[0]):
            valid_files.append(mg_msg)
        else:
            skipped += 1

    if not valid_files:
        await _safe_edit(
            status_msg,
            f"No supported video files found.\n"
            f"Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"
        )
        return

    created_at = datetime.utcnow().isoformat()
    task_ids:  list[str] = []
    positions: list[int] = []
    episodes:  list[int] = []

    for index, mg_msg in enumerate(valid_files):
        ep_num = ep_int + index
        ep_str = str(ep_num).zfill(ep_width)
        episodes.append(ep_num)

        output_filename = _resolve_template(template, season_str, ep_str)
        file_id, original_file_name, file_size = _file_info(mg_msg)

        task_data = _build_task(
            message=message,
            file_id=file_id,
            original_file_name=original_file_name,
            file_size=file_size,
            source_thumbnail_file_id=_source_thumbnail_file_id(mg_msg),
            output_filename=output_filename,
            settings=settings,
            watermark=watermark,
            created_at=created_at,
            batch=True,
        )

        task_id  = task_queue.create_task(task_data)
        position = task_queue.get_queue_position(task_id)
        task_ids.append(task_id)
        positions.append(position)

    ep_start = str(episodes[0]).zfill(ep_width)
    ep_end   = str(episodes[-1]).zfill(ep_width)
    pos_min  = min(positions)
    pos_max  = max(positions)
    pos_text = f"[{pos_min}]" if pos_min == pos_max else f"[{pos_min} – {pos_max}]"

    mode = "rename + metadata"

    mode_label = f"sequential ({batch_count} msgs)" if batch_count else "media group"
    lines = [
    f"Added {len(valid_files)} rename task(s) to the queue {pos_text}. ({mode_label})\n",
    f"Season: {season_str}",
    f"Episodes: {ep_start} to {ep_end}",
    f"Mode: {mode}",
]
    if skipped:
        lines.append(f"**Skipped:** {skipped} non-video file(s)")
    lines.append("\n**Output will be delivered to your DM.** Please wait patiently.")

    await _safe_edit(status_msg, "\n".join(lines))


async def process_rename_command(
    client: Client,
    message: Message,
    task_queue,
    user_settings,
):
    is_batch, batch_count, filename, parse_error = _parse_rename_command(message)

    if parse_error:
        await message.reply_text(
            f"❌ {parse_error}\n\n"
            "Usage:\n"
            "  Single     : `/rename movie.mkv`\n"
            "  Batch group: `/rename -b [S{season}-E{episode}] Show.mkv`\n"
            "  Batch seq  : `/rename -b 6 [S{season}-E{episode}] Show.mkv`"
        )
        return

    if is_batch:
        await _process_batch_rename(
            client, message, filename, batch_count, task_queue, user_settings
        )
    else:
        await _process_single_rename(client, message, filename, task_queue, user_settings)


def setup_rename_handler(app: Client, task_queue, user_settings, config):
    allowed_group_filter = filters.chat(config.allowed_group_ids)

    @app.on_message(filters.command(["r", "rename"]) & allowed_group_filter)
    async def rename_command(client: Client, message: Message):
        if not await _check_access(client, message, config):
            return
        await process_rename_command(client, message, task_queue, user_settings)