import os
from pyrogram import filters, enums
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from src import Config


# ── Style constants ────────────────────────────────────────────────────────────

_D  = "━━━━━━━━━━━━━━━━━━"
_Ds = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
_B  = "◆"
_S  = "◈"
_A  = "↳"
_H  = "■"


# ── Start screen ───────────────────────────────────────────────────────────────

START_TEXT = (
    "<b>TOJI-Encode</b>  <i>— Video Encoding &amp; Renaming Bot</i>\n"
    "\n"
    "<i>Drop a video, configure settings, get perfectly\n"
    "processed files — without leaving Telegram.</i>\n"
    "\n"
    f"{_D}\n"
    "\n"
    f"{_B}  Multi-resolution encoding  <i>(up to 4 at once)</i>\n"
    f"{_B}  Batch encode or rename entire albums\n"
    f"{_B}  Per-resolution quality profiles\n"
    f"{_B}  Custom watermark with timing control\n"
    f"{_B}  Metadata embedding on every output\n"
    f"{_B}  Live queue and real-time progress\n"
    "\n"
    f"{_D}\n"
    "\n"
    f"<code>/es</code>  to configure  ·  tap <b>Guide →</b> for full reference"
)


# ── Help pages  (each MUST stay ≤ 1024 chars) ─────────────────────────────────

PAGES = [

# ══════════════════════════════════════════════════════════════════════════════
# Page 1 — Table of Contents
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>TOJI-Encode  —  Complete Guide</b>

Navigate page by page using the arrows below.

{_D}
<b>{_H} Command Index</b>

<b>Settings</b>
  <code>/es</code>      — Configure all bot settings
  <code>/st</code>      — Set thumbnail

<b>Encoding</b>
  <code>/encode</code>  <code>/e</code>  — Encode a video

<b>Renaming</b>
  <code>/rename</code>  <code>/r</code>  — Rename a file

<b>Utility</b>
  <code>/mi</code>      — MediaInfo report
  <code>/status</code>  <code>/s</code>  — Live queue &amp; bot stats
  <code>/cancel</code>  <code>/c</code>  — Stop a running task

<b>Reference</b>
  How to Encode  ·  How to Rename
  Tips &amp; Best Practices  ·  Do's and Don'ts
{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 2 — /es Settings (1/2)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /es  —  Settings  (1/2)</b>

<i>Opens the settings panel. All changes are saved to your account.</i>

{_D}

<b>{_S} Resolution</b>
Select one or more output resolutions (up to 4).
<code>HDRip  ·  1080p  ·  720p  ·  480p</code>

<b>HDRip</b> = stream-copy only. No re-encode — source quality kept.

{_Ds}

<b>{_S} Quality Profile  <i>(per resolution)</i></b>
Each resolution has its own encoding profile.

  <b>CRF</b>     — 0–51  (lower = better, larger file)
  <b>Preset</b>  — ultrafast → veryslow
  <b>Codec</b>   — <code>libx264</code>  or  <code>libx265</code>
  <b>Audio</b>   — <code>48k 64k 96k 128k 192k 256k 320k</code>

<b>Defaults:</b>
  <code>1080p</code>  CRF 23 · medium · libx264 · 192k
  <code>720p</code>   CRF 26 · medium · libx264 · 128k
  <code>480p</code>   CRF 28 · fast   · libx264 · 96k

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 3 — /es Settings (2/2)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /es  —  Settings  (2/2)</b>

{_D}

<b>{_S} Send Type</b>
<code>Media</code> — streamable  ·  <code>Document</code> — raw file

{_Ds}

<b>{_S} Metadata</b>
  <b>Title · Author · Encoder</b>
  Embedded in every output automatically.

{_Ds}

<b>{_S} Thumbnail</b>
  Set via <code>/st</code> command (reply to a photo).
  Reused for all encoded and renamed files.

{_Ds}

<b>{_S} Watermark</b>
  Text burned onto the video.
  Fields: Text · Font · Size · Color · Padding · Position
  Modes: <code>full</code> · <code>range</code> · <code>random</code>

{_Ds}

<b>{_S} Placeholders</b>
  <b>Episode</b> — default start episode for <code>{{episode}}</code> in templates
  <b>Season</b>  — default season for <code>{{season}}</code> in templates

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 4 — /encode (single)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /encode  /e  —  Encode a Video</b>

<i>Reply to a video and send the command with a filename template.</i>

{_D}

<b>{_S} Usage</b>
<code>/e &lt;filename template.ext&gt;</code>

{_Ds}

<b>{_S} Template Rules</b>
{_B}  Must contain <code>{{quality}}</code> — filled per resolution
{_B}  May contain <code>{{episode}}</code> — filled from settings
{_B}  Must end with a valid video extension

{_Ds}

<b>{_S} Examples</b>
<code>/e [S1-E03] Pokemon [{{quality}}].mkv</code>
<code>/e [S1-E{{episode}}] Naruto [{{quality}}] [SUB].mkv</code>
<code>/e Movie Name [{{quality}}] @Source.mkv</code>

{_Ds}

<b>{_S} Notes</b>
{_B}  Reply to a video before sending the command
{_B}  All selected resolutions encode in one task
{_B}  <code>{{episode}}</code> fills from <code>/es → Placeholders</code>
{_B}  Output delivered to your DM

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 5 — /encode -b (batch encode)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /encode -b  —  Batch Encode</b>

{_D}

<b>Media group</b> — reply to first file of a Telegram album:
<code>/e -b &lt;template.ext&gt;</code>

<b>Sequential</b> — reply to first, grab N individual messages:
<code>/e -b N &lt;template.ext&gt;</code>

{_Ds}

<b>{_S} Examples</b>
<code>/e -b [S1-E{{episode}}] Pokemon [{{quality}}].mkv</code>
<i>↳ album: episodes auto-increment from default</i>

<code>/e -b 6 [S1-E{{episode}}] One Piece [{{quality}}].mkv</code>
<i>↳ sequential: 6 msgs from replied</i>

{_Ds}

<b>{_S} Notes</b>
{_B}  <code>-b</code> alone = media group (album)
{_B}  <code>-b N</code> = grab N sequential messages  <i>(N ≥ 2)</i>
{_B}  <code>{{episode}}</code> auto-increments by 1 per file
{_B}  Start episode set in <code>/es → Placeholders</code>
{_B}  Non-video files are skipped automatically

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 6 — /rename (single)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /rename  /r  —  Rename a Single File</b>

<i>Reply to any video file with the exact new filename. No re-encoding.</i>

{_D}

<b>{_S} Usage</b>
<code>/rename &lt;new filename.ext&gt;</code>

Write the full filename exactly as you want it.
No placeholders — just the literal name.

{_Ds}

<b>{_S} Example</b>
<code>/rename [S01-E05] Pokemon [1080p].mkv</code>
{_A} Output: <code>[S01-E05] Pokemon [1080p].mkv</code>

{_Ds}

<b>{_S} What Gets Applied</b>
{_B}  Thumbnail from your settings
{_B}  Metadata  <i>(title, author, encoder)</i>
{_B}  No watermark on rename

{_Ds}

<b>{_S} Supported Extensions</b>
<code>.mp4  .mkv  .webm  .mov  .avi</code>
<code>.mpeg .mpg  .wmv   .flv  .3gp</code>

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 7 — /rename -b (batch rename)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /rename -b  —  Batch Rename</b>

{_D}

<b>Media group</b> — reply to first file of a Telegram album:
<code>/rename -b &lt;filename template.ext&gt;</code>

<b>Sequential</b> — reply to first, grab N individual messages:
<code>/rename -b N &lt;filename template.ext&gt;</code>

{_Ds}

<b>{_S} Placeholders</b>  <i>(only these two are valid)</i>
<code>{{season}}</code>   — season number  <i>(from /es, zero-padded)</i>
<code>{{episode}}</code>  — episode number  <i>(auto-incremented)</i>

{_Ds}

<b>{_S} Examples</b>
<code>/rename -b [S{{season}}-E{{episode}}] Show.mkv</code>
<i>↳ album of 3: [S01-E01].mkv, [S01-E02].mkv…</i>

<code>/rename -b 6 [S{{season}}-E{{episode}}] Show.mkv</code>
<i>↳ sequential: 6 messages from replied</i>

{_Ds}

<b>{_S} Notes</b>
{_B}  Season &amp; start episode set in <code>/es → Placeholders</code>
{_B}  Non-video files are skipped automatically

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 8 — /mi
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /mi  —  MediaInfo Report</b>

<i>Generates a MediaInfo report and posts it to a permanent Telegraph page.</i>

{_D}

<b>{_S} Usage</b>
Reply to a media file, then send:
<code>/mi</code>

That's it — no extra flags needed.

{_Ds}

<b>{_S} Example Output</b>
📄 <b>MediaInfo</b>
<b>File:</b> <code>Movie.mkv</code>
<b>Link:</b> https://telegra.ph/...

{_Ds}

<b>{_S} Report Includes</b>
{_B}  General  <i>(format, size, duration, bitrate)</i>
{_B}  Video    <i>(codec, resolution, fps, colour)</i>
{_B}  Audio    <i>(codec, channels, bitrate, language)</i>
{_B}  Subtitles &amp; chapters if present

{_Ds}

<b>{_S} Notes</b>
{_B}  Only the first ~3 MB is downloaded for analysis
{_B}  Telegraph link is permanent and shareable

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 9 — /status
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /status  /s  —  Live Queue &amp; Stats</b>

<i>Shows all active tasks with real-time progress. Auto-refreshes every 5 seconds.</i>

{_D}

<b>{_S} Usage</b>
<code>/status</code>  or  <code>/s</code>

{_Ds}

<b>{_S} Example</b>
<b>Task 1</b>
┃ File: <code>Movie [1080p].mkv</code>
┃ Size: 2.4 GB
┠ Resolution : <u>1080p</u>  ||  720p
┠ Status : Encoding  <i>(Job 1/2)</i>
┠ [████████░░] 48.3%
┠ Elapsed: 4m 12s
┖ /cancel <code>a1b2c3d4</code>

{_Ds}

<b>{_S} Fields</b>
<b>Resolution</b> — underlined = currently active
<b>Progress</b>   — bar + percentage
<b>Speed/ETA</b>  — shown during download &amp; upload
<b>Bot Stats</b>  — CPU · RAM · Disk · Uptime

{_Ds}

<b>{_S} Notes</b>
{_B}  Auto-refreshes every 5 s
{_B}  One status message per group at a time
{_B}  Use <code>/cancel &lt;id&gt;</code> from task block to cancel

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 10 — /cancel
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /cancel  /c  —  Cancel a Task</b>

<i>Stops a running or queued task. Temp files are cleaned up automatically.</i>

{_D}

<b>{_S} Usage</b>
<code>/cancel &lt;task_id&gt;</code>  or  <code>/c &lt;task_id&gt;</code>

{_Ds}

<b>{_S} Finding the Task ID</b>
Run <code>/status</code> — the task ID is shown at the bottom of each block.

{_A} Example:  <code>┖ /cancel a1b2c3d4</code>

Only the first few characters are needed.

{_Ds}

<b>{_S} Example</b>
{_A} Input:  <code>/cancel a1b2</code>
{_A} Output: ✅ Task <code>a1b2</code> cancelled.

{_Ds}

<b>{_S} Notes</b>
{_B}  You can only cancel your own tasks
{_B}  Admins can cancel any task
{_B}  Works at any stage: queued → uploading
{_B}  Temp files deleted on cancellation

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 11 — /st (Set Thumbnail)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} /st  /setthumb  —  Set Thumbnail</b>

<i>Set a custom thumbnail that is applied to all encoded and renamed files.</i>

{_D}

<b>{_S} Usage</b>
Reply to a <b>photo</b> or <b>image file</b>, then send:
<code>/st</code>

{_Ds}

<b>{_S} Example</b>
1. Send a JPG/PNG image to the group
2. Reply to that image with <code>/st</code>
3. Bot confirms: ✅ Thumbnail saved

{_Ds}

<b>{_S} Notes</b>
{_B}  Admin only command
{_B}  Accepts photo messages or image documents
{_B}  Saved thumbnail is reused for all tasks
{_B}  To remove thumbnail, clear it via <code>/es</code>
{_B}  Works in both group and private chat

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 12 — How to Encode (step by step)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} How to Encode  —  Step by Step</b>

{_D}

<b>Step 1 — Configure settings</b>
Send <code>/es</code> and set:
  · Resolutions  · CRF / Preset / Codec
  · Audio bitrate  · Metadata  · Thumbnail

{_Ds}

<b>Step 2 — Write your template</b>
The filename you want, with placeholders:
  <code>{{quality}}</code> — <b>required</b>, fills the resolution
  <code>{{episode}}</code> — optional, fills from your default episode

Example template:
<code>[S1-E{{episode}}] Show Name [{{quality}}] [SUB].mkv</code>

{_Ds}

<b>Step 3 — Send the command</b>
Reply to the video, then send:
<code>/e [S1-E{{episode}}] Show Name [{{quality}}] [SUB].mkv</code>

{_Ds}

<b>Step 4 — Wait for delivery</b>
Check <code>/status</code> for progress.
Output files arrive in your DM.

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 13 — How to Rename (step by step)
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} How to Rename  —  Step by Step</b>

{_D}

<b>Single File Rename</b>

Step 1 — Reply to a video file
Step 2 — Send the exact filename you want:
<code>/rename [S01-E05] Show Name [1080p].mkv</code>
Step 3 — Done. File delivered to your DM.

No placeholders in single mode — just type the name as-is.

{_Ds}

<b>Batch Rename</b>

Step 1 — Set season &amp; start episode in <code>/es → Placeholders</code>
Step 2 — Reply to the <u>first</u> file of the album
Step 3 — Send with a template:
<code>/rename -b [S{{season}}-E{{episode}}] Show.mkv</code>

For sequential messages (not an album):
<code>/rename -b 6 [S{{season}}-E{{episode}}] Show.mkv</code>

Step 4 — Bot queues all files. Output in your DM.

{_Ds}

<b>Notes</b>
{_B}  Only <code>{{season}}</code> and <code>{{episode}}</code> work in batch
{_B}  Episode auto-increments per file

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 14 — Tips & Best Practices
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} Tips &amp; Best Practices</b>

{_D}

<b>{_S} Before Encoding</b>
{_B}  Configure <code>/es</code> first — saves time every task
{_B}  Select only the resolutions you need
{_B}  Set metadata once — applies to all tasks
{_B}  Use <code>/st</code> to set thumbnail once, reused forever

{_Ds}

<b>{_S} Choosing Settings</b>
<b>Archival:</b>  CRF 18–20 · slow · libx265
<b>Streaming:</b> CRF 23–26 · medium · libx264
<b>Fast:</b>      CRF 26–28 · fast · libx264
<b>Lossless:</b>  Use <code>HDRip</code>

{_Ds}

<b>{_S} Watermark Tips</b>
{_B}  <code>range</code> mode — watermark the intro only
{_B}  <code>random</code> mode — anti-piracy overlays
{_B}  Use TTF/OTF fonts from Google Fonts

{_Ds}

<b>{_S} Queue &amp; Batch Tips</b>
{_B}  Tasks run one at a time in order
{_B}  Use <code>/status</code> to monitor position and ETA
{_B}  For batch, reply to the <u>first</u> file of the album
{_B}  Test with a single file before batch

{_D}""",


# ══════════════════════════════════════════════════════════════════════════════
# Page 15 — Do's and Don'ts
# ══════════════════════════════════════════════════════════════════════════════
f"""<b>{_H} Do's and Don'ts</b>

{_D}

<b>◆ Do</b>
✦  Run <code>/es</code> before queuing any task
✦  Always include <code>{{quality}}</code> in <code>/encode</code> templates
✦  Use <code>/st</code> to set your thumbnail once
✦  Use <code>-b</code> for batch encode/rename on albums
✦  Use <code>-b N</code> for N sequential individual messages
✦  Reply to the <u>first</u> file of the album for batch
✦  Start the bot in DM before using in a group

{_D}

<b>◇ Don't</b>
✦  Don't skip <code>{{quality}}</code> in encode templates — required
✦  Don't use placeholders in <code>/rename</code> single mode
✦  Don't use unsupported placeholders in <code>/rename -b</code>
✦  Don't select HDRip if you need a re-encode
✦  Don't use <code>/mi</code> without replying to a file

{_Ds}

<b>{_S} Quick Reminders</b>
{_B}  Lower CRF = better quality, larger file
{_B}  <code>libx265</code> compresses better than <code>libx264</code>
{_B}  Use <code>/mi</code> to inspect files before encoding

{_D}""",

]

TOTAL_PAGES = len(PAGES)


# ── Sanity-check all pages at import time ──────────────────────────────────────

for _i, _p in enumerate(PAGES):
    _l = len(_p)
    if _l > 1024:
        import warnings
        warnings.warn(f"[start.py] Page {_i + 1} exceeds 1024 chars ({_l})", stacklevel=2)


# ── Keyboard builders ──────────────────────────────────────────────────────────

def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Developer", url="https://t.me/Suubaru_bhai"),
            InlineKeyboardButton("Guide →",  callback_data="help_open:0"),
        ],
        [
            InlineKeyboardButton("Close",  callback_data="start_close"),
        ],
    ])


def _help_keyboard(page: int) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⮜", callback_data=f"help_page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1} / {TOTAL_PAGES}", callback_data="help_noop"))
    if page < TOTAL_PAGES - 1:
        nav.append(InlineKeyboardButton("→", callback_data=f"help_page:{page + 1}"))

    return InlineKeyboardMarkup([
        nav,
        [
            InlineKeyboardButton("Back",  callback_data="help_back"),
            InlineKeyboardButton("Close", callback_data="start_close"),
        ],
    ])


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _edit_message(msg, text: str, kb: InlineKeyboardMarkup):
    try:
        if msg.photo:
            await msg.edit_caption(
                caption=text,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await msg.edit_text(
                text=text,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
    except Exception as e:
        print(f"[EncodeBot] edit failed: {e}")


async def _show_start(message: Message, config: Config, edit: bool = False):
    kb   = _start_keyboard()
    text = START_TEXT

    if edit:
        await _edit_message(message, text, kb)
        return

    try:
        if config.paths.start_image.startswith(("http://", "https://")):
            await message.reply_photo(
                photo=config.paths.start_image,
                caption=text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML,
            )
        elif os.path.exists(config.paths.start_image):
            await message.reply_photo(
                photo=config.paths.start_image,
                caption=text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception as e:
        print(f"[Start] send failed: {e}")
        await message.reply_text(
            f"Welcome {message.from_user.first_name}!  Tap <b>Guide →</b> for all commands.",
            reply_markup=kb,
            parse_mode=enums.ParseMode.HTML,
        )


async def _show_help(message: Message, page: int, config: Config, edit: bool = False):
    kb   = _help_keyboard(page)
    text = PAGES[page]

    if edit:
        await _edit_message(message, text, kb)
        return

    if config.paths.help_banner and os.path.exists(config.paths.help_banner):
        try:
            await message.reply_photo(
                photo=config.paths.help_banner,
                caption=text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML,
            )
            return
        except Exception as e:
            print(f"[Help] photo send failed, falling back to text: {e}")

    await message.reply_text(
        text=text,
        reply_markup=kb,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ── Handler registration ───────────────────────────────────────────────────────

def setup_start_handler(app, config: Config):

    @app.on_message(
        filters.command(["start", "help"])
        & (filters.private | filters.chat(config.allowed_group_ids))
    )
    async def start_handler(client, message: Message):
        await _show_start(message, config)

    # Guide button on /start screen ────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^help_open:(\d+)$"))
    async def help_open_callback(client, callback_query: CallbackQuery):
        page = int(callback_query.data.split(":")[1])
        page = max(0, min(page, TOTAL_PAGES - 1))
        await _show_help(callback_query.message, page=page, config=config, edit=True)
        await callback_query.answer()

    # Pagination inside guide ──────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^help_page:(\d+)$"))
    async def help_page_callback(client, callback_query: CallbackQuery):
        page = int(callback_query.data.split(":")[1])
        page = max(0, min(page, TOTAL_PAGES - 1))
        await _show_help(callback_query.message, page=page, config=config, edit=True)
        await callback_query.answer()

    # Back → /start screen ─────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^help_back$"))
    async def help_back_callback(client, callback_query: CallbackQuery):
        await _show_start(callback_query.message, config=config, edit=True)
        await callback_query.answer()

    # Close → delete the message ───────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^start_close$"))
    async def start_close_callback(client, callback_query: CallbackQuery):
        try:
            await callback_query.message.delete()
        except Exception:
            await callback_query.answer("Couldn't close.", show_alert=False)
            return
        await callback_query.answer()

    # Page counter button (no-op) ──────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^help_noop$"))
    async def help_noop(client, callback_query: CallbackQuery):
        await callback_query.answer()