import asyncio
import logging
import os
import shutil
import time
from datetime import datetime

from pyrogram.errors import FloodWait

from src.services.downloader import Downloader
from src.services.encoder import Encoder
from src.services.uploader import Uploader
from src.utils.resources import (
    detect_max_concurrent_encodings,
    check_disk_space,
    get_min_free_disk_mb,
    is_low_memory_mode,
)

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, task_queue, user_settings_getter, ffmpeg, client, config):
        self.task_queue = task_queue
        self.user_settings_getter = user_settings_getter
        self.ffmpeg = ffmpeg
        self.client = client
        self.config = config
        self.temp_base = config.paths.tmp
        self.thumbnails_dir = config.paths.thumbnails
        self.running = False

        # ── Concurrency control ───────────────────────────────────────────────
        _max_enc = detect_max_concurrent_encodings()
        logger.info("[Worker] max_concurrent_encodings=%d", _max_enc)
        self._encode_sem = asyncio.Semaphore(_max_enc)

        self._current_task_id: str | None = None
        self._current_asyncio_task: asyncio.Task | None = None

        # Prefetch is kept but disabled in low-memory mode to save disk/RAM.
        self._prefetch_task: asyncio.Task | None = None
        self._prefetch_task_id: str | None = None
        self._low_mem = is_low_memory_mode()

        os.makedirs(self.temp_base, exist_ok=True)
        os.makedirs(self.thumbnails_dir, exist_ok=True)

    async def start(self):
        self.running = True
        self._startup_cleanup()
        await self._worker_loop()

    async def stop(self):
        self.running = False
        if self._current_task_id:
            await self.cancel_task(self._current_task_id)

    def _startup_cleanup(self):
        self.task_queue.purge_stale_tasks()
        if os.path.exists(self.temp_base):
            active_ids = set(self.task_queue.queue)
            for name in os.listdir(self.temp_base):
                folder = os.path.join(self.temp_base, name)
                if os.path.isdir(folder) and name not in active_ids:
                    shutil.rmtree(folder, ignore_errors=True)
                    logger.info("[Worker] Startup: removed orphaned folder %s", name)

    async def _worker_loop(self):
        while self.running:
            task = self._next_queued_task()
            if not task:
                await asyncio.sleep(1)
                continue

            task_id = task["task_id"]
            self._current_task_id = task_id
            self.task_queue.current_task = task_id

            try:
                self._current_asyncio_task = asyncio.create_task(
                    self._run_task(task)
                )
                await self._current_asyncio_task
            except asyncio.CancelledError:
                logger.warning("[JOB %s] CANCELLED", task_id)
                await self._notify_user(
                    task["user_id"], f"⚠️ Task `{task_id[:8]}` was cancelled."
                )
                self.task_queue.remove_task(task_id)
                self._cleanup_task_folder(task_id)
            except Exception as e:
                logger.error("[JOB %s] FAILED: %s", task_id, e, exc_info=True)
                await self._notify_user(
                    task["user_id"],
                    f"❌ Task `{task_id[:8]}` failed.\n{str(e)[:200]}",
                )
                self.task_queue.remove_task(task_id)
                self._cleanup_task_folder(task_id)
            finally:
                self._current_asyncio_task = None
                self._current_task_id = None
                if self.task_queue.current_task == task_id:
                    self.task_queue.current_task = None

    def _next_queued_task(self) -> dict | None:
        for task_id in self.task_queue.queue:
            task = self.task_queue.get_task(task_id)
            if task and task.get("status") in ("queued", "ready"):
                return task
        return None

    async def _run_task(self, task: dict):
        task_id = task["task_id"]
        task["_start_time"] = time.time()
        self.task_queue.update_status(task_id, "starting", 0)
        logger.info("[JOB %s] QUEUED user=%s", task_id, task.get("user_id"))

        # ── Disk space pre-check ──────────────────────────────────────────────
        min_free_mb = get_min_free_disk_mb()
        file_size_bytes = task.get("file_size") or 0
        required_mb = max(min_free_mb, int(file_size_bytes * 2.5 / (1024 * 1024)))
        ok, disk_msg = check_disk_space(self.temp_base, required_mb)
        if not ok:
            raise Exception(f"Disk space check failed: {disk_msg}")

        await self._snapshot_thumbnail(task)
        task["user_is_premium"] = await self._get_user_premium(task["user_id"])

        # Download
        downloaded_path = await self._download(task)
        logger.info("[JOB %s] DOWNLOAD COMPLETE path=%s", task_id, downloaded_path)

        # Optionally prefetch the next task while we encode (skip in low-mem mode)
        if not self._low_mem:
            asyncio.create_task(self._maybe_prefetch_next(task_id))

        jobs = task.get("jobs") or [self._legacy_job(task)]
        task["total_jobs"] = len(jobs)

        logger.info("[JOB %s] ENCODE START jobs=%d", task_id, len(jobs))

        if self._can_encode_jobs_together(jobs):
            encoded_jobs = await self._encode_many(task, downloaded_path, jobs)
        else:
            encoded_jobs = []
            for idx, job in enumerate(jobs, start=1):
                task["current_job"] = idx
                task["resolution"] = job["resolution"]
                task["output_filename"] = job["output_filename"]
                encoded_path = await self._encode(task, downloaded_path, job)
                encoded_jobs.append((job, encoded_path))

        logger.info("[JOB %s] ENCODE COMPLETE", task_id)

        # Remove input now that encoding is finished (save disk before uploading)
        if os.path.exists(downloaded_path):
            try:
                os.remove(downloaded_path)
                logger.info("[JOB %s] Removed input file after encode", task_id)
            except Exception as ex:
                logger.warning("[JOB %s] Could not remove input: %s", task_id, ex)

        logger.info("[JOB %s] UPLOAD START parts=%d", task_id, len(encoded_jobs))

        for idx, (job, encoded_path) in enumerate(encoded_jobs, start=1):
            task["current_job"] = idx
            task["resolution"] = job["resolution"]
            task["output_filename"] = job["output_filename"]
            await self._upload(task, encoded_path, job)
            await self._send_completion_to_group(task, job, encoded_path)
            if os.path.exists(encoded_path):
                os.remove(encoded_path)

        logger.info("[JOB %s] UPLOAD COMPLETE", task_id)

        self.task_queue.remove_task(task_id)
        self._cleanup_task_folder(task_id)
        logger.info("[JOB %s] CLEANUP COMPLETE", task_id)

    # ── Prefetch: download slot-1 while slot-0 encodes ────────────────────────

    async def _maybe_prefetch_next(self, current_task_id: str):
        if self._prefetch_task and not self._prefetch_task.done():
            return
        next_task = self._find_next_queued_task(current_task_id)
        if not next_task:
            return
        next_id = next_task["task_id"]
        logger.info("[Worker] Prefetch starting for %s", next_id[:8])
        await self._snapshot_thumbnail(next_task)
        next_task["user_is_premium"] = await self._get_user_premium(
            next_task["user_id"]
        )
        self._prefetch_task_id = next_id
        self._prefetch_task = asyncio.create_task(self._bg_download(next_task))

    async def _bg_download(self, task: dict):
        task_id = task["task_id"]
        try:
            downloader = Downloader(self.temp_base, self.task_queue, task_id)
            path = await downloader.download(client=self.client, task_data=task)
            if path and os.path.exists(path):
                task["_downloaded_path"] = path
                task["download_completed_at"] = datetime.utcnow().isoformat()
                self.task_queue.update_status(task_id, "ready", 0)
                logger.info("[Worker] Prefetch done for %s", task_id[:8])
            else:
                self.task_queue.update_status(task_id, "queued", 0)
                task.pop("_downloaded_path", None)
        except asyncio.CancelledError:
            self.task_queue.update_status(task_id, "queued", 0)
            task.pop("_downloaded_path", None)
            raise
        except Exception as e:
            logger.warning("[Worker] Prefetch failed for %s: %s", task_id[:8], e)
            self.task_queue.update_status(task_id, "queued", 0)
            task.pop("_downloaded_path", None)
        finally:
            if self._prefetch_task_id == task_id:
                self._prefetch_task_id = None
                self._prefetch_task = None

    def _find_next_queued_task(self, current_task_id: str) -> dict | None:
        for tid in self.task_queue.queue:
            if tid == current_task_id:
                continue
            task = self.task_queue.get_task(tid)
            if task and task.get("status") == "queued":
                return task
        return None

    # ── Download ──────────────────────────────────────────────────────────────

    async def _download(self, task: dict) -> str:
        task_id = task["task_id"]
        logger.info("[JOB %s] DOWNLOAD START", task_id)

        prefetched = task.get("_downloaded_path", "")
        if prefetched and os.path.exists(prefetched):
            logger.info("[JOB %s] Using prefetched file", task_id)
            self.task_queue.update_status(task_id, "ready", 0)
            return prefetched

        if (
            self._prefetch_task_id == task_id
            and self._prefetch_task
            and not self._prefetch_task.done()
        ):
            logger.info("[JOB %s] Waiting for in-progress prefetch", task_id)
            try:
                await self._prefetch_task
            except Exception:
                pass
            prefetched = task.get("_downloaded_path", "")
            if prefetched and os.path.exists(prefetched):
                self.task_queue.update_status(task_id, "ready", 0)
                return prefetched

        self.task_queue.update_status(task_id, "downloading", 0)
        downloader = Downloader(self.temp_base, self.task_queue, task_id)
        path = await downloader.download(client=self.client, task_data=task)

        if not path or not os.path.exists(path):
            raise Exception("Download returned no valid file")

        task["_downloaded_path"] = path
        task["download_completed_at"] = datetime.utcnow().isoformat()
        self.task_queue.update_status(task_id, "ready", 0)
        return path

    # ── Encode ────────────────────────────────────────────────────────────────

    async def _encode(self, task: dict, input_path: str, job: dict) -> str:
        task_id = task["task_id"]
        task["encode_started_at"] = datetime.utcnow().isoformat()
        task["encode_progress"] = {"percentage": 0.0}
        self.task_queue.update_status(task_id, "encoding", 0)

        async with self._encode_sem:
            encoder = Encoder(self.ffmpeg)
            encoded_path = await encoder.encode(
                task_data=task,
                input_path=input_path,
                settings=self._settings_for_job(task, job),
                task_queue=self.task_queue,
            )

        if not encoded_path:
            raise Exception(f"Encoder returned no path for {job['resolution']}")
        return encoded_path

    async def _encode_many(
        self, task: dict, input_path: str, jobs: list[dict]
    ) -> list[tuple[dict, str]]:
        task_id = task["task_id"]
        task["encode_started_at"] = datetime.utcnow().isoformat()
        task["encode_progress"] = {"percentage": 0.0}
        task["resolution"] = " / ".join(j.get("resolution", "?") for j in jobs)
        task["output_filename"] = jobs[0]["output_filename"]
        self.task_queue.update_status(task_id, "encoding", 0)

        async with self._encode_sem:
            encoder = Encoder(self.ffmpeg)
            encoded_jobs = await encoder.encode_many(
                task_data=task,
                input_path=input_path,
                jobs=jobs,
                settings_list=[self._settings_for_job(task, j) for j in jobs],
                task_queue=self.task_queue,
            )

        if not encoded_jobs:
            raise Exception("Encoder returned no paths")
        return encoded_jobs

    def _settings_for_job(self, task: dict, job: dict) -> dict:
        wm = task.get("watermark")
        # If watermark has a MongoDB font sentinel, we can't resolve it here
        # because we don't hold the UserSettings object at this point.
        # The font_path will already be a real tmp path set by the handler
        # that built the task (via us.get_thumbnail_path / get_font_path).
        # No extra action needed — pass watermark as-is.
        return {
            "resolution":      job["resolution"],
            "processing_mode": job.get("processing_mode", "encode"),
            "crf":             job.get("crf"),
            "preset":          job.get("preset"),
            "codec":           job.get("codec"),
            "audio_bitrate":   job.get("audio_bitrate"),
            "metadata":        job.get("metadata", {}),
            "watermark":       wm,
        }

    @staticmethod
    def _can_encode_jobs_together(jobs: list[dict]) -> bool:
        return (
            len(jobs) > 1
            and all(j.get("processing_mode", "encode") == "encode" for j in jobs)
        )

    # ── Upload ────────────────────────────────────────────────────────────────

    async def _upload(self, task: dict, file_path: str, job: dict):
        task_id = task["task_id"]
        self.task_queue.update_status(task_id, "uploading", 0)
        task["upload_progress"] = {"percentage": 0.0}

        upload_data = {
            **task,
            "upload_file_path": file_path,
            "output_filename":  job["output_filename"],
            "send_type":        job.get("send_type", "media"),
            "thumbnail_path":   await self._resolve_thumbnail(task, job),
        }

        uploader = Uploader(
            self.client,
            upload_data,
            self.task_queue,
            tmp_dir=self.temp_base,
            ffmpeg=self.ffmpeg,
            user_is_premium=task.get("user_is_premium", False),
        )
        await uploader.upload()

    # ── Completion message ────────────────────────────────────────────────────

    async def _send_completion_to_group(
        self, task: dict, job: dict, file_path: str
    ):
        source_chat_id = task.get("source_chat_id")
        if not source_chat_id:
            return
        text = ""
        try:
            file_size_bytes = (
                os.path.getsize(file_path) if os.path.exists(file_path) else 0
            )
            if file_size_bytes >= 1024 ** 3:
                size_str = f"{file_size_bytes / (1024**3):.2f} GB"
            elif file_size_bytes >= 1024 ** 2:
                size_str = f"{file_size_bytes / (1024**2):.2f} MB"
            else:
                size_str = f"{file_size_bytes / 1024:.2f} KB"

            elapsed = int(time.time() - task.get("_start_time", time.time()))
            if elapsed < 60:
                elapsed_str = f"{elapsed}s"
            elif elapsed < 3600:
                elapsed_str = f"{elapsed // 60}m {elapsed % 60}s"
            else:
                h, r = divmod(elapsed, 3600)
                elapsed_str = f"{h}h {r // 60}m {r % 60}s"

            text = (
                f"`{job['output_filename']}`\n"
                f"┠ **Elapsed:** {elapsed_str}\n"
                f"➲ File has been Sent to Bot PM (Private)"
            )

            await self.client.send_message(
                chat_id=source_chat_id,
                text=text,
                disable_web_page_preview=True,
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await self.client.send_message(
                    chat_id=source_chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
            except Exception as ex:
                logger.warning("[Worker] Completion retry failed: %s", ex)
        except Exception as e:
            logger.warning("[Worker] Completion message failed: %s", e)

    # ── Thumbnail helpers ─────────────────────────────────────────────────────

    async def _snapshot_thumbnail(self, task: dict):
        src = task.get("thumbnail_path", "")
        if not src or not os.path.exists(src):
            return
        task_folder = os.path.join(self.temp_base, task["task_id"])
        frozen_path = os.path.join(
            task_folder, f"thumbnail_{task['task_id']}.jpg"
        )
        if os.path.abspath(src) == os.path.abspath(frozen_path):
            return
        os.makedirs(task_folder, exist_ok=True)
        try:
            shutil.copy2(src, frozen_path)
            task["thumbnail_path"] = frozen_path
        except Exception as e:
            logger.warning("[Worker] Thumbnail snapshot failed: %s", e)

    async def _resolve_thumbnail(
        self, task: dict, job: dict
    ) -> str | None:
        auto_detect = bool(task.get("auto_detect_thumb", False))
        user_thumb = (
            task.get("thumbnail_path") or job.get("thumbnail_path") or ""
        )
        task_folder = os.path.join(self.temp_base, task["task_id"])

        if not auto_detect:
            return (
                user_thumb if user_thumb and os.path.exists(user_thumb) else None
            )

        source_thumb_id = task.get("source_thumbnail_file_id", "")
        if source_thumb_id:
            dest = os.path.join(task_folder, "_source_thumb.jpg")
            try:
                downloaded = await self.client.download_media(
                    source_thumb_id, file_name=dest
                )
                if downloaded and os.path.exists(downloaded):
                    return os.path.abspath(downloaded)
            except Exception:
                pass

        return (
            user_thumb if user_thumb and os.path.exists(user_thumb) else None
        )

    # ── Misc helpers ──────────────────────────────────────────────────────────

    def _cleanup_task_folder(self, task_id: str):
        folder = os.path.join(self.temp_base, task_id)
        if os.path.exists(folder):
            shutil.rmtree(folder, ignore_errors=True)

    async def _get_user_premium(self, user_id: int) -> bool:
        try:
            user = await self.client.get_users(user_id)
            return bool(getattr(user, "is_premium", False))
        except Exception:
            return False

    def _legacy_job(self, task: dict) -> dict:
        resolution = task.get("resolution", "1080p")
        return {
            "resolution":      resolution,
            "output_filename": task["output_filename"],
            "processing_mode": "encode",
            "crf":             task.get("crf", 28),
            "preset":          task.get("preset", "medium"),
            "codec":           task.get("codec", "libx264"),
            "audio_bitrate":   task.get("audio_bitrate", "128k"),
            "metadata":        task.get("metadata", {}),
            "thumbnail_path":  task.get("thumbnail_path", ""),
            "send_type":       task.get("send_type", "media"),
        }

    async def _notify_user(self, user_id: int, text: str):
        try:
            await self.client.send_message(user_id, text)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await self.client.send_message(user_id, text)
            except Exception as ex:
                logger.warning("[Worker] Notify failed after FloodWait: %s", ex)
        except Exception as e:
            logger.warning("[Worker] Notify failed: %s", e)

    # ── Cancel ────────────────────────────────────────────────────────────────

    async def cancel_task(self, task_id: str):
        if (
            self._prefetch_task_id == task_id
            and self._prefetch_task
            and not self._prefetch_task.done()
        ):
            self._prefetch_task.cancel()
            try:
                await self._prefetch_task
            except (asyncio.CancelledError, Exception):
                pass
            self._prefetch_task = None
            self._prefetch_task_id = None

        task = self.task_queue.get_task(task_id)
        if task_id == self._current_task_id:
            if (
                self._current_asyncio_task
                and not self._current_asyncio_task.done()
            ):
                self._current_asyncio_task.cancel()
        else:
            if task:
                self.task_queue.remove_task(task_id)
                self._cleanup_task_folder(task_id)
                await self._notify_user(
                    task["user_id"], f"⚠️ Task `{task_id[:8]}` cancelled."
                )
