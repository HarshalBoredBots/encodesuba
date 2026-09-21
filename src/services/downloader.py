import os
import logging
import asyncio
import time

logger = logging.getLogger(__name__)

class Downloader:
    def __init__(self, temp_base: str, task_queue=None, task_id=None):
        self.temp_base = temp_base
        self.task_queue = task_queue
        self.task_id = task_id
        self._start_time = None
        self._last_cb_time = None
        self._last_cb_bytes = 0
        self._declared_size = 0

        os.makedirs(self.temp_base, exist_ok=True)

        self.download_progress = {
            "total_size": 0,
            "downloaded": 0,
            "percentage": 0,
            "speed": 0,
            "eta": 0,
            "elapsed": 0,
            "status": "idle",
        }

    async def download(self, client, task_data: dict) -> str:
        task_id = task_data["task_id"]
        file_id = task_data["file_id"]
        original_file_name = task_data.get("original_file_name") or f"video_{task_id}.mkv"
        self.task_id = task_id
        self._declared_size = task_data.get("file_size") or 0

        task_folder = os.path.join(self.temp_base, task_id)
        os.makedirs(task_folder, exist_ok=True)

        desired_path = os.path.join(task_folder, original_file_name)

        self._reset_progress()
        self.download_progress["status"] = "downloading"
        self._start_time = time.time()

        try:
            actual_path = await client.download_media(
                file_id,
                file_name=desired_path,
                progress=self._progress_callback,
            )

            if not actual_path:
                raise Exception("download_media returned None")

            actual_path = os.path.abspath(actual_path)

            if not os.path.exists(actual_path):
                raise Exception(f"File not found after download: {actual_path}")

            if os.path.getsize(actual_path) == 0:
                raise Exception(f"Downloaded file is empty: {actual_path}")

            desired_abs = os.path.abspath(desired_path)
            if actual_path != desired_abs:
                os.replace(actual_path, desired_abs)
                actual_path = desired_abs

            self.download_progress["status"] = "completed"
            if self.task_queue and self.task_id:
                self.task_queue.update_status(self.task_id, "downloading", 100)
                task = self.task_queue.tasks.get(self.task_id)
                if task is not None:
                    task["progress_details"] = {
                        "total_size": self.download_progress["total_size"],
                        "downloaded": self.download_progress["total_size"],
                        "percentage": 100,
                        "speed": 0,
                        "eta": 0,
                    }
                    task["progress"] = 100.0

            return actual_path

        except asyncio.CancelledError:
            self.download_progress["status"] = "cancelled"
            raise
        except Exception as e:
            self.download_progress["status"] = "failed"
            logger.error("[Downloader] Download failed: %s", e)
            raise Exception(f"Download failed: {e}")

    async def _progress_callback(self, current: int, total: int):
        now = time.time()

        if not total:
            total = getattr(self, "_declared_size", 0)

        if self.download_progress["total_size"] == 0 and total:
            self.download_progress["total_size"] = total

        # Throttle task_queue updates to once per second
        if self._last_cb_time is not None and (now - self._last_cb_time) < 1.0:
            return

        total_elapsed = int(now - self._start_time) if self._start_time else 1

        if self._last_cb_time is None:
            self._last_cb_time = now
            self._last_cb_bytes = current
            speed = 0.0
        else:
            interval = now - self._last_cb_time
            if interval >= 0.5:
                speed = max(0.0, (current - self._last_cb_bytes) / interval)
                self._last_cb_time = now
                self._last_cb_bytes = current
            else:
                speed = self.download_progress.get("speed", 0.0)

        percentage = (current / total * 100) if total > 0 else 0
        eta = int((total - current) / speed) if speed > 0 and total > current else 0

        self.download_progress.update({
            "downloaded": current,
            "percentage": round(percentage, 2),
            "speed": round(speed, 2),
            "eta": eta,
            "elapsed": total_elapsed,
            "status": "downloading",
        })

        if self.task_queue and self.task_id:
            self.task_queue.update_status(self.task_id, "downloading", round(percentage, 2))
            task = self.task_queue.tasks.get(self.task_id)
            if task is not None:
                task["progress_details"] = {
                    "total_size": total or self.download_progress["total_size"],
                    "downloaded": current,
                    "percentage": round(percentage, 2),
                    "speed": round(speed, 2),
                    "eta": eta,
                }
                task["progress"] = round(percentage, 2)

    def _reset_progress(self):
        self.download_progress = {
            "total_size": 0,
            "downloaded": 0,
            "percentage": 0,
            "speed": 0,
            "eta": 0,
            "elapsed": 0,
            "status": "idle",
        }
        self._start_time = None
        self._last_cb_time = None
