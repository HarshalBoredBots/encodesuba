from __future__ import annotations

import os
import re
import logging

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

log = logging.getLogger(__name__)

# ── Position label → storage key ─────────────────────────────────────────────
_LABEL_TO_POS: dict[str, str] = {
    "top left":  "top_left",
    "top mid":   "top_mid",
    "top right": "top_right",
    "mid left":  "mid_left",
    "mid right": "mid_right",
    "bot left":  "bot_left",
    "bot right": "bot_right",
}


def _pos_key(raw: str) -> str:
    """Convert a position label like '🡔 Top Left' → 'top_left'."""
    # strip arrows / emoji and lowercase
    cleaned = re.sub(r"[^\w\s]", "", raw).strip().lower()
    # collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned)
    return _LABEL_TO_POS.get(cleaned, "bot_right")


# ── Settings text parser ──────────────────────────────────────────────────────

def _parse_settings(text: str) -> dict:
    """
    Parse a settings dump (both HTML and plain-text variants are handled)
    and return a dict ready to be merged into UserSettings.
    """
    # strip HTML tags so we work with plain text
    plain = re.sub(r"<[^>]+>", "", text)

    result: dict = {}

    # ── Resolutions ───────────────────────────────────────────────────────────
    m = re.search(r"Resolutions\s*[:\-]\s*(.+)", plain, re.IGNORECASE)
    if m:
        resolutions = [r.strip() for r in m.group(1).split(",") if r.strip()]
        if resolutions:
            result["resolutions"] = resolutions

    # ── Quality Profiles ──────────────────────────────────────────────────────
    # e.g.  "1080p  CRF 24 · veryfast · libx264 · 128k"
    profiles: dict[str, dict] = {}
    for res in ("1080p", "720p", "480p"):
        pattern = rf"{res}\s+CRF\s+(\d+)\s*[·\·\|]\s*(\S+)\s*[·\·\|]\s*(\S+)\s*[·\·\|]\s*(\S+)"
        pm = re.search(pattern, plain, re.IGNORECASE)
        if pm:
            profiles[res] = {
                "crf":           int(pm.group(1)),
                "preset":        pm.group(2),
                "codec":         pm.group(3),
                "audio_bitrate": pm.group(4),
            }
    if profiles:
        result["profiles"] = profiles

    # ── Send Type ─────────────────────────────────────────────────────────────
    m = re.search(r"Send\s*Type\s*[:\-]\s*(\S+)", plain, re.IGNORECASE)
    if m:
        raw = m.group(1).strip().lower()
        result["send_type"] = "media" if raw == "media" else "document"

    # ── Auto Detect Thumb ─────────────────────────────────────────────────────
    m = re.search(r"Auto\s*Detect\s*Thumb\s*[:\-]\s*(\S+)", plain, re.IGNORECASE)
    if m:
        result["auto_detect_thumb"] = m.group(1).strip().lower() == "on"

    # ── Metadata ──────────────────────────────────────────────────────────────
    meta: dict[str, str] = {}
    for field in ("title", "author", "encoder"):
        pm = re.search(rf"{field}\s*[:\-]\s*(.+)", plain, re.IGNORECASE)
        if pm:
            val = pm.group(1).strip()
            if val and val != "-":
                meta[field] = val
    if meta:
        result["metadata"] = meta

    # ── Start Episode ─────────────────────────────────────────────────────────
    m = re.search(r"Start\s*Episode\s*[:\-]?\s*(\d+)", plain, re.IGNORECASE)
    if m:
        result["default_start_episode"] = m.group(1).lstrip("0") or "1"

    # ── Watermark ─────────────────────────────────────────────────────────────
    wm: dict = {}

    m = re.search(r"Status\s*[:\-]\s*(\S+)", plain, re.IGNORECASE)
    if m:
        wm["enabled"] = m.group(1).strip().lower() == "enabled"

    m = re.search(r"Text\s*[:\-]\s*(.+)", plain, re.IGNORECASE)
    if m:
        wm["text"] = m.group(1).strip()

    m = re.search(r"Color\s*[:\-]\s*(\S+)", plain, re.IGNORECASE)
    if m:
        wm["color"] = m.group(1).strip().lower()

    m = re.search(r"Font\s*Size\s*[:\-]\s*(\d+)\s*px", plain, re.IGNORECASE)
    if m:
        wm["font_size"] = int(m.group(1))

    m = re.search(r"Padding\s*[:\-]\s*(\d+)\s*%", plain, re.IGNORECASE)
    if m:
        wm["padding"] = int(m.group(1))

    # Position — prefer the explicit "Position  : …" line (not "Position set to …")
    # Try "Position  : 🡔 Top Left" first
    m = re.search(r"^Position\s*[:\-]\s*(.+)$", plain, re.IGNORECASE | re.MULTILINE)
    if not m:
        m = re.search(r"Position\s+set\s+to\s+(.+?)(?:\s*[✓✔]|$)", plain, re.IGNORECASE)
    if m:
        wm["position"] = _pos_key(m.group(1).strip())

    # Font name (plain text only — actual font file is asked separately)
    m = re.search(r"^Font\s*[:\-]\s*(.+)$", plain, re.IGNORECASE | re.MULTILINE)
    if m:
        wm["font_name"] = m.group(1).strip()

    # Timing
    tm = re.search(r"Timing\s*[:\-]\s*(.+)", plain, re.IGNORECASE)
    if tm:
        timing_raw = tm.group(1).strip()
        if re.search(r"full", timing_raw, re.IGNORECASE):
            wm["timing_mode"] = "full"
        elif re.search(r"range|→|->", timing_raw, re.IGNORECASE):
            wm["timing_mode"] = "range"
            times = re.findall(r"(\d{1,2}:\d{2})", timing_raw)
            if len(times) >= 2:
                def mmss(t):
                    p = t.split(":")
                    return int(p[0]) * 60 + int(p[1])
                wm["start"] = mmss(times[0])
                wm["end"]   = mmss(times[1])
        else:
            wm["timing_mode"] = "random_duration"
            rm = re.search(r"(\d+)\s*[×x]", timing_raw)
            if rm:
                wm["repeat_count"] = int(rm.group(1))
            dm = re.search(r"(\d+)\s*s\b", timing_raw)
            if dm:
                wm["duration"] = int(dm.group(1))

    if wm:
        result["watermark"] = wm

    return result


# ── Apply parsed settings to UserSettings ────────────────────────────────────

def _apply_parsed(us, parsed: dict) -> list[str]:
    """
    Write every recognised field from *parsed* into *us* (UserSettings).
    Returns a human-readable list of what was applied.
    """
    applied: list[str] = []

    if "resolutions" in parsed:
        us.update("resolutions", parsed["resolutions"])
        applied.append(f"Resolutions → {', '.join(parsed['resolutions'])}")

    if "profiles" in parsed:
        # Merge with existing profiles so missing resolutions keep their values
        current = us.get().get("profiles", {})
        current.update(parsed["profiles"])
        us.update("profiles", current)
        for res, p in parsed["profiles"].items():
            applied.append(
                f"{res} profile → CRF {p['crf']} · {p['preset']} · {p['codec']} · {p['audio_bitrate']}"
            )

    if "send_type" in parsed:
        us.update("send_type", parsed["send_type"])
        applied.append(f"Send type → {parsed['send_type'].capitalize()}")

    if "auto_detect_thumb" in parsed:
        us.update("auto_detect_thumb", parsed["auto_detect_thumb"])
        applied.append(f"Auto detect thumb → {'On' if parsed['auto_detect_thumb'] else 'Off'}")

    if "metadata" in parsed:
        current_meta = us.get().get("metadata", {})
        current_meta.update(parsed["metadata"])
        us.update("metadata", current_meta)
        for k, v in parsed["metadata"].items():
            applied.append(f"Metadata {k.title()} → {v}")

    if "default_start_episode" in parsed:
        us.update("default_start_episode", parsed["default_start_episode"])
        applied.append(f"Start episode → {parsed['default_start_episode']}")

    if "watermark" in parsed:
        wm_data = parsed["watermark"]
        us.update_watermark(**wm_data)
        applied.append("Watermark settings applied")

    return applied


# ── Message builders ──────────────────────────────────────────────────────────

def _thumb_question() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "✅ <b>Settings applied!</b>\n\n"
        "Do you have a <b>thumbnail</b> to upload?\n"
        "<i>If No, the existing / default thumbnail will be used.</i>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data="set_import_thumb_yes"),
        InlineKeyboardButton("❌ No",  callback_data="set_import_thumb_no"),
    ]])
    return text, kb


def _font_question() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "Do you have a custom <b>font</b> file (.ttf / .otf) "
        "for the watermark?\n"
        "<i>If No, the current / default font will be kept.</i>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data="set_import_font_yes"),
        InlineKeyboardButton("❌ No",  callback_data="set_import_font_no"),
    ]])
    return text, kb


def _done_text(applied: list[str]) -> str:
    lines = "\n".join(f"  • {a}" for a in applied) if applied else "  (nothing recognised)"
    return (
        "🎉 <b>Import complete!</b>\n\n"
        "<b>Applied settings:</b>\n"
        f"{lines}"
    )


# ── Handler setup ─────────────────────────────────────────────────────────────

def setup_ocean_handlers(app: Client, user_settings, config):

    # ── /set command ──────────────────────────────────────────────────────────
    @app.on_message(
        filters.command("ocean")
        & (filters.private | filters.chat(config.allowed_group_ids))
    )
    async def cmd_set(client: Client, message: Message):
        user_id = message.from_user.id

        if user_id not in config.admin_ids:
            await message.reply_text("Dukhi Atma!😔", parse_mode=ParseMode.HTML)
            return

        # Must be a reply
        if not message.reply_to_message:
            await message.reply_text(
                "⚠️ <b>Reply to a settings message</b> with /set to import those settings.",
                parse_mode=ParseMode.HTML,
            )
            return

        replied = message.reply_to_message
        raw_text = replied.text or replied.caption or ""

        if not raw_text.strip():
            await message.reply_text(
                "⚠️ The replied message has no text to parse.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Parse & apply
        parsed = _parse_settings(raw_text)

        if not parsed:
            await message.reply_text(
                "⚠️ Could not recognise any settings in that message.\n"
                "Make sure you reply to a proper settings dump.",
                parse_mode=ParseMode.HTML,
            )
            return

        us = user_settings(user_id)
        applied = _apply_parsed(us, parsed)

        # Store applied list + chat_id so we can show it at the end
        us.temp_state[user_id] = {
            "state":   "set_import_thumb_prompt",
            "applied": applied,
            "chat_id": message.chat.id,
        }

        text, kb = _thumb_question()
        # Prepend applied summary as a preview
        preview = "📋 <b>Parsed & applied:</b>\n" + "\n".join(f"  • {a}" for a in applied) + "\n\n"
        await message.reply_text(preview + text, reply_markup=kb, parse_mode=ParseMode.HTML)

    # ── Callback: thumbnail Yes ───────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^set_import_thumb_yes$"))
    async def cb_thumb_yes(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        us = user_settings(user_id)
        state_data = us.temp_state.get(user_id, {})

        prompt = await cq.message.reply_text(
            "📷 Please send the <b>thumbnail image</b> now.",
            parse_mode=ParseMode.HTML,
        )

        us.temp_state[user_id] = {
            **state_data,
            "state":             "set_import_waiting_thumb",
            "prompt_message_id": prompt.id,
        }
        await cq.answer()
        # Remove the Yes/No buttons
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    # ── Callback: thumbnail No ────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^set_import_thumb_no$"))
    async def cb_thumb_no(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        us = user_settings(user_id)

        await cq.answer()
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        font_text, font_kb = _font_question()
        prompt = await cq.message.reply_text(font_text, reply_markup=font_kb, parse_mode=ParseMode.HTML)

        state_data = us.temp_state.get(user_id, {})
        us.temp_state[user_id] = {
            **state_data,
            "state":             "set_import_font_prompt",
            "prompt_message_id": prompt.id,
        }

    # ── Callback: font Yes ────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^set_import_font_yes$"))
    async def cb_font_yes(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        us = user_settings(user_id)
        state_data = us.temp_state.get(user_id, {})

        prompt = await cq.message.reply_text(
            "🔤 Please send your <b>font file</b> (.ttf or .otf) now.",
            parse_mode=ParseMode.HTML,
        )

        us.temp_state[user_id] = {
            **state_data,
            "state":             "set_import_waiting_font",
            "prompt_message_id": prompt.id,
        }
        await cq.answer()
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    # ── Callback: font No → done ──────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^set_import_font_no$"))
    async def cb_font_no(client: Client, cq: CallbackQuery):
        user_id = cq.from_user.id
        us = user_settings(user_id)
        state_data = us.temp_state.pop(user_id, {})
        applied = state_data.get("applied", [])

        await cq.answer("✅ Done!")
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.message.reply_text(
            _done_text(applied),
            parse_mode=ParseMode.HTML,
        )

    # ── Photo handler: thumbnail during import ────────────────────────────────
    @app.on_message(
        filters.photo & (filters.private | filters.chat(config.allowed_group_ids))
    )
    async def handle_set_thumbnail(client: Client, message: Message):
        user_id = message.from_user.id
        us = user_settings(user_id)
        state_data = us.temp_state.get(user_id, {})

        if state_data.get("state") != "set_import_waiting_thumb":
            return  # not our state – let other handlers deal with it

        prompt_message_id = state_data.get("prompt_message_id")

        try:
            thumb_dir = config.paths.thumbnails
            os.makedirs(thumb_dir, exist_ok=True)
            dest = os.path.join(thumb_dir, f"{user_id}.jpg")
            downloaded = await client.download_media(message, file_name=dest)

            if not downloaded or not os.path.exists(downloaded):
                await message.reply_text(
                    "❌ Failed to save the thumbnail. Please try again.",
                    parse_mode=ParseMode.HTML,
                )
                return

            us.set_thumbnail(os.path.abspath(downloaded))

            # Clean up the "send thumbnail" prompt and the photo
            try:
                await client.delete_messages(
                    chat_id=message.chat.id,
                    message_ids=[m for m in [prompt_message_id, message.id] if m],
                )
            except Exception:
                pass

        except Exception as e:
            await message.reply_text(
                f"❌ <b>Error saving thumbnail:</b> <code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Move to font question
        font_text, font_kb = _font_question()
        prompt = await client.send_message(
            chat_id=message.chat.id,
            text="✅ Thumbnail saved!\n\n" + font_text,
            reply_markup=font_kb,
            parse_mode=ParseMode.HTML,
        )

        us.temp_state[user_id] = {
            **state_data,
            "state":             "set_import_font_prompt",
            "prompt_message_id": prompt.id,
        }

    # ── Document handler: font file during import ─────────────────────────────
    @app.on_message(
        filters.document & (filters.private | filters.chat(config.allowed_group_ids))
    )
    async def handle_set_font(client: Client, message: Message):
        user_id = message.from_user.id
        us = user_settings(user_id)
        state_data = us.temp_state.get(user_id, {})

        if state_data.get("state") != "set_import_waiting_font":
            return  # not our state

        prompt_message_id = state_data.get("prompt_message_id")
        doc = message.document
        if not doc:
            return

        file_name = doc.file_name or ""
        ext = os.path.splitext(file_name)[1].lower()

        if ext not in (".ttf", ".otf"):
            await message.reply_text(
                "⚠️ Only <code>.ttf</code> and <code>.otf</code> font files are accepted.",
                parse_mode=ParseMode.HTML,
            )
            return

        try:
            fonts_dir = config.paths.fonts
            os.makedirs(fonts_dir, exist_ok=True)
            tmp_path = os.path.join(fonts_dir, f"tmp_{user_id}{ext}")
            downloaded = await client.download_media(message, file_name=tmp_path)

            if not downloaded or not os.path.exists(downloaded):
                await message.reply_text(
                    "❌ Failed to download font. Please try again.",
                    parse_mode=ParseMode.HTML,
                )
                return

            font_name = us.set_watermark_font(os.path.abspath(downloaded))

            try:
                if os.path.exists(downloaded) and downloaded == tmp_path:
                    os.remove(downloaded)
            except Exception:
                pass

            try:
                await client.delete_messages(
                    chat_id=message.chat.id,
                    message_ids=[m for m in [prompt_message_id, message.id] if m],
                )
            except Exception:
                pass

        except Exception as e:
            await message.reply_text(
                f"❌ <b>Error saving font:</b> <code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        applied = state_data.get("applied", [])
        applied.append(f"Watermark font → {font_name}")
        us.temp_state.pop(user_id, None)

        await client.send_message(
            chat_id=message.chat.id,
            text=_done_text(applied),
            parse_mode=ParseMode.HTML,
        )