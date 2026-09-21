import asyncio
import sys
import os
import logging

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

_original_get_event_loop = asyncio.get_event_loop

def _patched_get_event_loop():
    try:
        return _original_get_event_loop()
    except RuntimeError:
        return _loop

asyncio.get_event_loop = _patched_get_event_loop

from pyrogram import Client, idle, filters
from pyrogram.types import Message

import motor.motor_asyncio
from src import Config, TaskQueue, UserSettings, FFmpeg
from src.core.user_setting import init_db
from src.utils.resources import (
    detect_ffmpeg_threads,
    resource_summary,
    get_temp_dir,
)
from src.services import Worker
from src.handlers.encode import setup_encode_handlers
from src.handlers.settings import setup_settings_handlers
from src.handlers.status import setup_status_handlers
from src.handlers.shift import setup_shift_handlers
from src.handlers.start import setup_start_handler
from src.handlers.cancel import setup_cancel_handlers, set_worker_instance, set_admin_ids
from src.handlers.mi import setup_mediainfo_handlers
from src.handlers.ocean import setup_ocean_handlers
from src.handlers.rename import setup_rename_handler
from src.handlers.set import setup_set_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


async def main():
    config = Config()

    config.paths.makedirs()

    # ── MongoDB ───────────────────────────────────────────────────────────────
    _mongo_uri = config.mongo_uri
    _mongo_client = motor.motor_asyncio.AsyncIOMotorClient(
        _mongo_uri,
        serverSelectionTimeoutMS=10_000,
    )
    _mongo_db = _mongo_client[config.mongo_db_name]
    init_db(_mongo_db)

    # Ensure index on user_id for fast lookups
    await _mongo_db["user_settings"].create_index("user_id", unique=True)
    import logging as _lg2
    _lg2.info("[Boot] MongoDB connected: %s / %s", _mongo_uri.split("@")[-1], config.mongo_db_name)

    import os as _os
    _workers = int(_os.getenv('PYROGRAM_WORKERS', '8'))
    _max_tx  = int(_os.getenv('PYROGRAM_MAX_TX',  '4'))
    app = Client(
        "encode_bot_session",
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token,
        workdir=config.paths.logs,
        workers=_workers,
        max_concurrent_transmissions=_max_tx,
    )

    task_queue = TaskQueue()
    _ffmpeg_threads = detect_ffmpeg_threads()
    import logging as _lg
    _lg.info("[Boot] Resource profile: %s", resource_summary())
    _lg.info("[Boot] FFmpeg threads: %d", _ffmpeg_threads)
    ffmpeg = FFmpeg(
        ffmpeg_path=config.paths.ffmpeg,
        ffprobe_path=config.paths.ffprobe,
        threads=_ffmpeg_threads,
    )

    _user_settings_cache: dict[int, UserSettings] = {}

    _SETTINGS_CACHE_MAX = 500
    def get_user_settings(user_id: int) -> UserSettings:
        if user_id not in _user_settings_cache:
            if len(_user_settings_cache) >= _SETTINGS_CACHE_MAX:
                # evict oldest entry
                _user_settings_cache.pop(next(iter(_user_settings_cache)))
            _user_settings_cache[user_id] = UserSettings(user_id, config.paths)
        return _user_settings_cache[user_id]

    await app.start()

    # ── Register handlers ─────────────────────────────────────────────────────
    setup_ocean_handlers(app=app, user_settings=get_user_settings, config=config)
    setup_set_handlers(app=app, user_settings=get_user_settings, config=config)
    setup_encode_handlers(app=app, task_queue=task_queue, user_settings=get_user_settings, config=config)
    setup_rename_handler(app, task_queue, get_user_settings, config)
    setup_cancel_handlers(app, task_queue, config)
    setup_shift_handlers(app=app, task_queue=task_queue, config=config)
    setup_status_handlers(app=app, task_queue=task_queue, admin_ids=config.admin_ids, config=config)
    setup_start_handler(app, config)  
    setup_mediainfo_handlers(app=app, config=config)
    setup_settings_handlers(app=app, user_settings=get_user_settings, config=config)
    

    # ── Worker ────────────────────────────────────────────────────────────────
    worker = Worker(task_queue, get_user_settings, ffmpeg, app, config)
    set_worker_instance(worker)
    set_admin_ids(config.admin_ids)

    # ── Ready ─────────────────────────────────────────────────────────────────
    me = await app.get_me()
    print(f"""
    ╔══════════════════════════════════╗
    ║  @{me.username:<31}║
    ╠══════════════════════════════════╣
    ║  /start   – Welcome              ║     
    ║  /es      – Encoding settings    ║
    ║  /encode  – Single Encode        ║
    ║  /rename  – Rename a file        ║
    ║  /status  – Queue status         ║
    ║  /mi      – Media Info           ║
    ║  /ocean   – Import settings      ║
    ║  /cancel  – Cancel a task        ║
    ╚══════════════════════════════════╝
    """)

    import signal, sys

    _worker_task = asyncio.create_task(worker.start())

    def _graceful_shutdown(signum, frame):
        import logging as _gl
        _gl.info("[Boot] Received signal %d — shutting down", signum)
        _worker_task.cancel()

    try:
        signal.signal(signal.SIGTERM, _graceful_shutdown)
        signal.signal(signal.SIGINT,  _graceful_shutdown)
    except Exception:
        pass

    await idle()
    await worker.stop()
    await app.stop()


if __name__ == "__main__":
    try:
        _loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        _loop.close()
