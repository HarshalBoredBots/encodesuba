import asyncio
from html import escape
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from datetime import datetime
from math import ceil
import humanize
import psutil
import time

BOT_START_TIME = time.time()

_active_status: dict[int, int]            = {}
_active_page:   dict[int, int]            = {}
_last_content:  dict[int, str]            = {}
_refresh_tasks: dict[int, asyncio.Task]   = {}
_last_progress: dict[tuple[str, str], float] = {}

AUTO_REFRESH_INTERVAL = 3
_BAR_LEN = 10

_ACTIVE_STATUSES = frozenset({
    "starting", "queued", "downloading", "ready", "encoding", "uploading"
})


def _progress_bar(pct: float) -> str:
    pct    = _clean_pct(pct)
    filled = round(_BAR_LEN * pct / 100)
    empty  = _BAR_LEN - filled
    return f"[{'█' * filled}{'░' * empty}] {pct:.1f}%"


def _build_keyboard(page: int, total_pages: int, has_tasks: bool) -> InlineKeyboardMarkup | None:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"status_page:{page - 1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="status_page:noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"status_page:{page + 1}"))

    rows = []
    if nav:
        rows.append(nav)
    if has_tasks:
        rows.append([InlineKeyboardButton("🗑 Cancel All", callback_data="status_cancel_all:confirm")])

    return InlineKeyboardMarkup(rows) if rows else None


async def _check_access(client, message: Message, admin_ids: list) -> bool:
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.reply_text("Dukhi Atma!😔", parse_mode=enums.ParseMode.HTML)
        return False
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


def setup_status_handlers(app: Client, task_queue, admin_ids, config):
    allowed_filter = filters.chat(config.allowed_group_ids)

    @app.on_message(filters.command(["s", "status"]) & allowed_filter)
    async def status_command(client: Client, message: Message):
        if not await _check_access(client, message, admin_ids):
            return

        chat_id = message.chat.id
        _cancel_refresh(chat_id)

        old_id = _active_status.pop(chat_id, None)
        if old_id:
            try:
                await client.delete_messages(chat_id, old_id)
            except Exception:
                pass

        _active_page[chat_id] = 0
        sent = await _send_status(client, message, task_queue, page=0)
        if sent:
            _active_status[chat_id] = sent.id
            _refresh_tasks[chat_id] = asyncio.create_task(
                _auto_refresh_loop(client, chat_id, sent, task_queue)
            )

    @app.on_callback_query(filters.regex(r"^status_page:"))
    async def status_page_callback(client: Client, callback_query):
        chat_id = callback_query.message.chat.id
        if chat_id not in config.allowed_group_ids:
            return
        if callback_query.from_user.id not in admin_ids:
            await callback_query.answer("⛔ Not authorised.", show_alert=True)
            return

        await callback_query.answer()

        raw = callback_query.data.split(":", 1)[1]
        if raw == "noop":
            return

        try:
            page = int(raw)
        except ValueError:
            return

        if _active_status.get(chat_id) != callback_query.message.id:
            return

        _active_page[chat_id] = page
        _last_content.pop(chat_id, None)
        await show_status(client, callback_query.message, task_queue, page=page, is_callback=True)

    @app.on_callback_query(filters.regex(r"^status_cancel_all:confirm$"))
    async def cancel_all_confirm_callback(client: Client, callback_query):
        chat_id = callback_query.message.chat.id
        if chat_id not in config.allowed_group_ids:
            return
        if callback_query.from_user.id not in admin_ids:
            await callback_query.answer("⛔ Not authorised.", show_alert=True)
            return
        if _active_status.get(chat_id) != callback_query.message.id:
            return

        await callback_query.answer()
        try:
            await callback_query.message.edit_text(
                "⚠️ <b>Are you sure you want to cancel ALL active tasks?</b>\nThis cannot be undone.",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Yes, Cancel All", callback_data="status_cancel_all:yes"),
                    InlineKeyboardButton("❌ No",              callback_data="status_cancel_all:no"),
                ]]),
            )
            _last_content.pop(chat_id, None)
        except Exception as e:
            print(f"[status] cancel_all confirm edit failed: {e}")

    @app.on_callback_query(filters.regex(r"^status_cancel_all:(yes|no)$"))
    async def cancel_all_execute_callback(client: Client, callback_query):
        chat_id = callback_query.message.chat.id
        if chat_id not in config.allowed_group_ids:
            return
        if callback_query.from_user.id not in admin_ids:
            await callback_query.answer("⛔ Not authorised.", show_alert=True)
            return
        if _active_status.get(chat_id) != callback_query.message.id:
            return

        await callback_query.answer()
        action = callback_query.data.split(":")[1]

        if action == "no":
            _last_content.pop(chat_id, None)
            await show_status(client, callback_query.message, task_queue,
                              page=_active_page.get(chat_id, 0), is_callback=True)
            return

        from src.handlers.cancel import get_worker_instance
        worker = get_worker_instance()

        cancelled = 0
        for tid in list(task_queue.queue):
            task = task_queue.get_task(tid)
            if not task or task.get("status") not in _ACTIVE_STATUSES:
                continue
            try:
                if task.get("status") == "queued":
                    task_queue.remove_task(tid)
                elif worker:
                    await worker.cancel_task(tid)
                else:
                    task_queue.remove_task(tid)
                cancelled += 1
            except Exception as e:
                print(f"[status] failed to cancel task {tid}: {e}")

        try:
            await callback_query.message.edit_text(
                f"✅ <b>Cancelled {cancelled} task(s).</b>",
                parse_mode=enums.ParseMode.HTML,
            )
            _last_content.pop(chat_id, None)
        except Exception as e:
            print(f"[status] cancel_all result edit failed: {e}")

        await asyncio.sleep(2)
        _active_page[chat_id] = 0
        _last_content.pop(chat_id, None)
        await show_status(client, callback_query.message, task_queue, page=0, is_callback=True)


def _cancel_refresh(chat_id: int):
    t = _refresh_tasks.pop(chat_id, None)
    if t and not t.done():
        t.cancel()
    _last_content.pop(chat_id, None)


async def _auto_refresh_loop(client, chat_id: int, status_msg, task_queue):
    try:
        while True:
            await asyncio.sleep(AUTO_REFRESH_INTERVAL)
            if _active_status.get(chat_id) != status_msg.id:
                break
            try:
                page = _active_page.get(chat_id, 0)
                await show_status(client, status_msg, task_queue, page=page, is_callback=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[status] auto-refresh error: {e}")
    except asyncio.CancelledError:
        pass


async def _send_status(client, message, task_queue, page=0) -> Message | None:
    text, total_pages, has_tasks = _build_status_content(task_queue, page)
    keyboard = _build_keyboard(page, total_pages, has_tasks)
    try:
        return await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        print(f"[status] send failed: {e}")
        return None


async def show_status(client, message, task_queue, page=0, is_callback=False):
    text, total_pages, has_tasks = _build_status_content(task_queue, page)
    keyboard = _build_keyboard(page, total_pages, has_tasks)

    if is_callback:
        chat_id = message.chat.id
        if _last_content.get(chat_id) == text:
            return
        try:
            await message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)
            _last_content[chat_id] = text
        except MessageNotModified:
            _last_content[chat_id] = text
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            print(f"[status] edit failed: {e}")
    else:
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=keyboard)


def _build_status_content(task_queue, page: int) -> tuple[str, int, bool]:
    all_active = []
    active_ids = set()
    for tid in list(task_queue.queue):
        task = task_queue.get_task(tid)
        if task and task.get("status") in _ACTIVE_STATUSES:
            all_active.append(task)
            active_ids.add(tid)

    for key in list(_last_progress):
        if key[0] not in active_ids:
            _last_progress.pop(key, None)

    items_per_page = 5
    total_pages    = ceil(len(all_active) / items_per_page) if all_active else 1
    page           = min(max(page, 0), total_pages - 1)
    start_idx      = page * items_per_page
    page_tasks     = all_active[start_idx: start_idx + items_per_page]

    lines: list[str] = []
    for i, task in enumerate(page_tasks):
        queue_pos = task_queue.get_queue_position(task["task_id"])
        lines.append(_build_task_block(queue_pos, task))
        if i < len(page_tasks) - 1:
            lines.append("▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁")

    if not all_active:
        lines.append("✅ No tasks in queue.\n")

    cpu      = psutil.cpu_percent(interval=None)
    mem      = psutil.virtual_memory()
    disk     = psutil.disk_usage("/")
    disk_pct = disk.used / disk.total * 100
    uptime   = _fmt_uptime(int(time.time() - BOT_START_TIME))

    lines.append(
        f"\n<b>⌬ Bot Stats</b>\n"
        f"┠ Tasks: {len(all_active)}\n"
        f"┠ CPU: {cpu:.1f}%  Disk: {disk_pct:.1f}%\n"
        f"┖ RAM: {mem.percent:.1f}%  Uptime: {uptime}"
    )

    return "\n".join(lines), total_pages, bool(all_active)


def _build_task_block(queue_pos: int, task: dict) -> str:
    status   = task.get("status", "queued")
    user_str = f"@{task['username']}" if task.get("username") else task.get("first_name", "Unknown")
    user_str = escape(str(user_str))
    task_id  = task.get("task_id", "????????")

    filename = (
        task.get("original_file_name") or task.get("file_name") or "Unknown"
        if status == "downloading"
        else task.get("output_filename") or task.get("original_file_name") or task.get("file_name") or "Unknown"
    )

    size_str     = _build_size_str(task)
    res_line     = _build_resolution_line(task)
    status_label = _build_status_label(task)
    pct, speed_str, eta_str = _build_progress_info(task)

    title = "Task 0 (Running)" if queue_pos == 0 else f"Task {queue_pos}"
    b  = f"<b>{title}</b>\n"
    b += f"┃ File: <code>{escape(str(filename))}</code>\n"
    b += f"┃ Size: {size_str}\n"
    b += f"┠ Resolution : {res_line}\n"
    b += f"┠ Status : {status_label}\n"

    if pct is not None:
        b += f"┠ {_progress_bar(pct)}\n"

    if speed_str or eta_str:
        parts = []
        if speed_str: parts.append(f"Speed: {speed_str}")
        if eta_str:   parts.append(f"ETA: {eta_str}")
        b += f"┠ {' | '.join(parts)}\n"

    dc = task.get("dc")
    if dc:
        b += f"┠ DC: DC{dc}\n"

    b += f"┠ Elapsed: {_elapsed_for_task(task)}\n"
    b += f"┠ User: {user_str}\n"
    b += f"┠ ID: <code>{escape(str(task.get('user_id', '?')))}</code>\n"
    b += f"┖ <code>/cancel {escape(str(task_id[:8]))}</code>"
    if queue_pos > 1:
        b += f"  |  <code>/shift {escape(str(task_id[:8]))} 2</code>"
    return b


def _build_resolution_line(task: dict) -> str:
    jobs        = task.get("jobs") or []
    current_res = task.get("resolution", "")
    status      = task.get("status", "")

    if task.get("task_type") == "rename":
        return "<u>Renaming</u>" if status in ("encoding", "uploading") else "Renaming"

    all_res = [j.get("resolution", "?") for j in jobs] if jobs else (
        task.get("resolutions") or [task.get("resolution", "?")]
    )

    parts = []
    for res in all_res:
        if res == current_res and status in ("encoding", "uploading", "downloading"):
            parts.append(f"<u>{escape(str(res))}</u>")
        else:
            parts.append(escape(str(res)))

    return "  ||  ".join(parts) if parts else "—"


def _build_status_label(task: dict) -> str:
    status = task.get("status", "queued")
    mode   = task.get("current_job_mode", "encode")

    cur_job   = task.get("current_job", 0)
    total_job = task.get("total_jobs") or len(task.get("jobs", []))
    job_tag   = f"  <i>(Job {cur_job}/{total_job})</i>" if total_job and total_job > 1 and cur_job else ""

    up          = task.get("upload_progress", {})
    total_parts = up.get("total_parts", 1)
    part_tag    = f"  <i>Part {up.get('current_part', 1)}/{total_parts}</i>" if total_parts > 1 else ""

    labels = {
        "queued":      "Queued",
        "starting":    "Starting…",
        "ready":       "Waiting for Encode",
        "downloading": "Downloading",
    }
    if status in labels:
        return labels[status]
    if status == "encoding":
        return f"Renaming{job_tag}" if mode == "rename" else f"Encoding{job_tag}"
    if status == "uploading":
        return f"Uploading{job_tag}{part_tag}"
    return status.capitalize()


def _build_size_str(task: dict) -> str:
    status = task.get("status", "")
    if status == "downloading":
        size = task.get("progress_details", {}).get("total_size") or task.get("file_size", 0)
    elif status == "uploading":
        size = task.get("upload_progress", {}).get("total_size") or task.get("file_size", 0)
    else:
        size = task.get("file_size", 0)
    return humanize.naturalsize(size, binary=True) if size else "unknown"


def _build_progress_info(task: dict) -> tuple[float | None, str, str]:
    status  = task.get("status", "")
    task_id = str(task.get("task_id", ""))
    stage_key = f"{status}:{task.get('current_job', 0)}"

    if status == "downloading":
        pd        = task.get("progress_details", {})
        pct       = _display_pct(task_id, stage_key, pd.get("percentage", task.get("progress", 0)))
        speed_str = _fmt_speed(pd.get("speed", 0))
        eta_str   = _fmt_eta(pd.get("eta", 0)) if pct < 99 else ""
        return pct, speed_str, eta_str

    if status == "encoding":
        ep  = task.get("encode_progress", {})
        pct = _display_pct(task_id, stage_key, ep.get("percentage", task.get("progress", 0)))
        return pct, "", ""

    if status == "uploading":
        up         = task.get("upload_progress", {})
        upload_key = f"{stage_key}:{up.get('current_part', 1)}"
        pct        = _display_pct(task_id, upload_key, up.get("percentage", task.get("progress", 0)))
        speed_str  = _fmt_speed(up.get("speed", 0))
        eta_str    = _fmt_eta(up.get("eta", 0)) if pct < 99 else ""
        return pct, speed_str, eta_str

    return None, "", ""


def _clean_pct(value) -> float:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return 0.0
    if pct != pct:
        return 0.0
    return max(0.0, min(100.0, pct))


def _display_pct(task_id: str, status: str, value) -> float:
    pct = _clean_pct(value)
    if not task_id:
        return pct
    key      = (task_id, status)
    previous = _last_progress.get(key)
    if previous is not None and pct < previous and previous < 100.0:
        pct = previous
    _last_progress[key] = pct
    return pct


def _fmt_speed(bps: float) -> str:
    try:
        bps = float(bps)
    except (TypeError, ValueError):
        return ""
    if not bps or bps < 100:
        return ""
    return f"{humanize.naturalsize(bps, binary=True)}/s"


def _fmt_eta(seconds: int) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if not seconds or seconds <= 0:
        return ""
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def _fmt_secs(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h: return f"{h}h {m}m"
    if m: return f"{m}m {s}s"
    return f"{s}s"


def _elapsed_for_task(task: dict) -> str:
    status     = task.get("status", "")
    started_at = task.get("started_at")

    if status in ("queued", "starting") or not started_at:
        return "—"

    try:
        started = datetime.fromisoformat(started_at)
    except Exception:
        return "—"

    now = datetime.utcnow()

    if status == "downloading":
        return _fmt_secs(max(0, int((now - started).total_seconds())))

    if status == "ready":
        completed_at = task.get("download_completed_at")
        if completed_at:
            try:
                completed = datetime.fromisoformat(completed_at)
                return _fmt_secs(max(0, int((completed - started).total_seconds())))
            except Exception:
                pass
        return _fmt_secs(max(0, int((now - started).total_seconds())))

    if status in ("encoding", "uploading"):
        encode_started_at = task.get("encode_started_at")
        if encode_started_at:
            try:
                encode_started = datetime.fromisoformat(encode_started_at)
                return _fmt_secs(max(0, int((now - encode_started).total_seconds())))
            except Exception:
                pass
        return _fmt_secs(max(0, int((now - started).total_seconds())))

    return _fmt_secs(max(0, int((now - started).total_seconds())))


def _fmt_uptime(secs: int) -> str:
    parts = []
    for unit, label in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= unit:
            parts.append(f"{secs // unit}{label}")
            secs %= unit
    return " ".join(parts) if parts else f"{secs}s"