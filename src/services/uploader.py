import asyncio
import math
import os
import shutil
import time

MAX_NON_PREMIUM_BYTES = int(1.95 * 1024 ** 3)
MAX_PREMIUM_BYTES = int(3.95 * 1024 ** 3)
_SPEED_UPDATE_INTERVAL = 0.5

class Uploader:
    def __init__(self, client, task_data: dict, task_queue=None, tmp_dir: str = None, ffmpeg=None, user_is_premium: bool = False):
        self.client = client
        self.task_data = task_data
        self.task_queue = task_queue
        self.ffmpeg = ffmpeg
        self.user_is_premium = user_is_premium
        self._tmp_dir = tmp_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "bin", "tmp",
        )

        self._last_time = None
        self._last_bytes = 0
        self._start_time = None
        self._total_uploaded_bytes = 0
        self._grand_total_bytes = 0
        self._current_part_size = 0
        self._cached_speed = 0.0
        self._cached_eta = 0

        self.upload_progress = {
            "total_size": 0,
            "uploaded": 0,
            "percentage": 0.0,
            "speed": 0.0,
            "eta": 0,
            "elapsed": 0,
            "status": "idle",
            "current_part": 1,
            "total_parts": 1,
        }

    def _max_part_size(self) -> int:
        return MAX_PREMIUM_BYTES if self.user_is_premium else MAX_NON_PREMIUM_BYTES

    async def upload(self):
        task_id = self.task_data["task_id"]
        user_id = self.task_data["user_id"]
        output_file_name = self.task_data["output_filename"]
        send_type = self.task_data.get("send_type", "media")

        task_folder = os.path.join(self._tmp_dir, task_id)
        explicit_file_path = self.task_data.get("upload_file_path")

        if explicit_file_path and os.path.exists(explicit_file_path):
            final_file_path = explicit_file_path
        else:
            encoded_files = sorted([
                f for f in os.listdir(task_folder)
                if f.endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))
            ])
            if not encoded_files:
                raise Exception("No encoded file found in task folder")

            current_file = os.path.join(task_folder, encoded_files[0])
            final_file_path = os.path.join(task_folder, output_file_name)
            if current_file != final_file_path:
                shutil.move(current_file, final_file_path)

        if not os.path.exists(final_file_path):
            raise Exception("File not found after renaming")

        file_size = os.path.getsize(final_file_path)
        max_part_sz = self._max_part_size()
        thumb = self.task_data.get("thumbnail_path")

        if file_size > max_part_sz:
            parts = await self._split_file(
                final_file_path, max_part_sz, output_file_name, task_folder
            )
        else:
            parts = [(final_file_path, output_file_name)]

        n_parts = len(parts)
        self._grand_total_bytes = sum(os.path.getsize(p) for p, _ in parts)
        self.upload_progress.update({
            "total_size": self._grand_total_bytes,
            "total_parts": n_parts,
            "status": "uploading",
        })
        self._start_time = time.time()
        self._last_time = None
        self._last_bytes = 0
        self._total_uploaded_bytes = 0
        self._cached_speed = 0.0
        self._cached_eta = 0

        self._write_task_progress(0, self._grand_total_bytes, 0.0, 0, 0, 1, n_parts)

        results = []
        try:
            for part_idx, (part_path, part_name) in enumerate(parts, start=1):
                if part_idx > 1:
                    await asyncio.sleep(3)
                self.upload_progress["current_part"] = part_idx
                self._current_part_size = os.path.getsize(part_path)
                self._last_time = None
                self._last_bytes = 0
                self._cached_speed = 0.0
                self._cached_eta = 0

                caption = f"**{part_name}**"
                if n_parts > 1:
                    caption += f"\n`Part {part_idx} of {n_parts}`"

                if send_type.lower() in ["doc", "document"]:
                    result = await self.client.send_document(
                        chat_id=user_id,
                        document=part_path,
                        thumb=thumb,
                        caption=caption,
                        force_document=True,
                        file_name=part_name,
                        progress=self._progress_callback,
                    )
                else:
                    result = await self.client.send_video(
                        chat_id=user_id,
                        video=part_path,
                        thumb=thumb,
                        caption=caption,
                        file_name=part_name,
                        supports_streaming=True,
                        progress=self._progress_callback,
                    )

                results.append(result)
                self._total_uploaded_bytes += self._current_part_size

            self.upload_progress["status"] = "completed"
            self._write_task_progress(
                self._grand_total_bytes, self._grand_total_bytes,
                100.0, 0, 0, n_parts, n_parts,
            )
            if self.task_queue:
                self.task_queue.update_status(task_id, "uploading", 100)

            return results[-1] if results else None

        except Exception as e:
            self.upload_progress["status"] = "failed"
            raise Exception(f"Upload failed: {e}")

    async def _progress_callback(self, current: int, total: int):
        now = time.time()

        overall_uploaded = self._total_uploaded_bytes + current
        overall_pct = (
            overall_uploaded / self._grand_total_bytes * 100
        ) if self._grand_total_bytes else 0.0
        overall_pct = round(overall_pct, 2)
        if self._last_time is None:
            self._last_time = now
            self._last_bytes = current
            self.upload_progress.update({
                "uploaded": overall_uploaded,
                "percentage": overall_pct,
                "speed": 0.0,
                "eta": 0,
                "elapsed": 0,
            })
            self._write_task_progress(
                overall_uploaded, self._grand_total_bytes,
                overall_pct, 0.0, 0,
                self.upload_progress.get("current_part", 1),
                self.upload_progress.get("total_parts", 1),
            )
            if self.task_queue:
                self.task_queue.update_status(
                    self.task_data["task_id"], "uploading", overall_pct
                )
            return

        interval = now - self._last_time
        if interval >= _SPEED_UPDATE_INTERVAL:
            bytes_delta = current - self._last_bytes
            self._cached_speed = max(0.0, bytes_delta / interval)
            remaining = self._grand_total_bytes - overall_uploaded
            self._cached_eta = int(remaining / self._cached_speed) if self._cached_speed > 0 else 0
            self._last_time = now
            self._last_bytes = current

        total_elapsed = int(now - self._start_time) if self._start_time else 0

        self.upload_progress.update({
            "uploaded": overall_uploaded,
            "percentage": overall_pct,
            "speed": round(self._cached_speed, 2),
            "eta": self._cached_eta,
            "elapsed": total_elapsed,
        })

        if self.task_queue:
            self.task_queue.update_status(
                self.task_data["task_id"], "uploading", overall_pct
            )

        self._write_task_progress(
            overall_uploaded, self._grand_total_bytes,
            overall_pct, round(self._cached_speed, 2), self._cached_eta,
            self.upload_progress["current_part"],
            self.upload_progress["total_parts"],
        )

    def _write_task_progress(self, uploaded: int, total_size: int, percentage: float, speed: float, eta: int, current_part: int = 1, total_parts: int = 1):
        if not self.task_queue:
            return
        task = self.task_queue.tasks.get(self.task_data["task_id"])
        if task is not None:
            task["upload_progress"] = {
                "total_size": total_size,
                "uploaded": uploaded,
                "percentage": percentage,
                "speed": speed,
                "eta": eta,
                "current_part": current_part,
                "total_parts": total_parts,
            }

    async def _split_file(self, file_path: str, max_bytes: int, output_name: str, task_folder: str) -> list[tuple[str, str]]:
        base, ext = os.path.splitext(output_name)
        file_size = os.path.getsize(file_path)
        n_parts = math.ceil(file_size / max_bytes)

        if self.ffmpeg:
            parts = await self._split_ffmpeg(file_path, max_bytes, base, ext, task_folder, n_parts)
            if parts:
                return parts

        return await self._split_bytes(file_path, max_bytes, base, ext, task_folder)

    async def _split_ffmpeg(self, file_path: str, max_bytes: int, base: str, ext: str, task_folder: str, n_parts: int) -> list[tuple[str, str]]:
        try:
            duration = 0.0
            info = await self.ffmpeg.probe_media(file_path)
            try:
                duration = float(info.get("format", {}).get("duration", 0))
            except (TypeError, ValueError):
                pass

            if duration < 1.0:
                return []

            seg_time = duration / n_parts
            pattern = os.path.join(task_folder, f"_seg_%03d{ext}")

            cmd = [
                self.ffmpeg.ffmpeg_path,
                "-i", file_path,
                "-c", "copy",
                "-map", "0",
                "-f", "segment",
                "-segment_time", f"{seg_time:.3f}",
                "-reset_timestamps", "1",
                "-avoid_negative_ts", "make_zero",
                "-y", pattern,
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=self.ffmpeg._env,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                for f in os.listdir(task_folder):
                    if f.startswith("_seg_") and f.endswith(ext):
                        try:
                            os.remove(os.path.join(task_folder, f))
                        except Exception:
                            pass
                return []

            seg_files = sorted(f for f in os.listdir(task_folder) if f.startswith("_seg_") and f.endswith(ext))
            if not seg_files:
                return []

            result = []
            for idx, seg_file in enumerate(seg_files, start=1):
                part_name = f"{base} Part {idx}{ext}"
                part_path = os.path.join(task_folder, part_name)
                os.rename(os.path.join(task_folder, seg_file), part_path)
                result.append((part_path, part_name))

            return result

        except Exception:
            return []

    async def _split_bytes(self, file_path: str, max_bytes: int, base: str, ext: str, task_folder: str) -> list[tuple[str, str]]:
        READ_BUF = 8 * 1024 * 1024
        parts = []
        idx = 1

        with open(file_path, "rb") as src:
            while True:
                part_name = f"{base} Part {idx}{ext}"
                part_path = os.path.join(task_folder, part_name)
                written = 0

                with open(part_path, "wb") as dst:
                    while written < max_bytes:
                        chunk = src.read(min(READ_BUF, max_bytes - written))
                        if not chunk:
                            break
                        dst.write(chunk)
                        written += len(chunk)

                if written == 0:
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass
                    break

                parts.append((part_path, part_name))
                idx += 1

                if written < max_bytes:
                    break

        return parts

    def get_progress(self) -> dict:
        return self.upload_progress.copy()
