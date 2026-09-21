import asyncio
import logging
import os

logger = logging.getLogger(__name__)

class Encoder:
    def __init__(self, ffmpeg):
        self.ffmpeg = ffmpeg

    async def encode(self, task_data: dict, input_path: str, settings: dict, task_queue=None) -> str:
        task_id         = task_data.get("task_id", "enc")
        output_filename = task_data["output_filename"]
        resolution      = task_data.get("resolution", "1080p")

        task_folder = os.path.dirname(input_path)
        base, ext   = os.path.splitext(output_filename)
        safe_ext    = ext or ".mp4"

        temp_output_path  = os.path.join(task_folder, f"_tmp_{task_id[:8]}_{resolution}{safe_ext}")
        final_output_path = os.path.join(task_folder, output_filename)

        if not os.path.exists(input_path):
            raise Exception(f"Input file not found: {input_path}")

        # ── Probe duration ────────────────────────────────────────────────────
        try:
            media_info = await self.ffmpeg.probe_media(input_path)
        except Exception:
            media_info = {}
        settings["media_info"] = media_info

        duration_secs = self._duration_from_media_info(media_info)
        if duration_secs <= 0:
            duration_secs = await self._probe_duration(input_path, media_info=media_info)
        logger.info("[JOB %s] duration_secs=%.2f", task_id[:8], duration_secs)

        # ── Build and run command ─────────────────────────────────────────────
        cmd = self.ffmpeg.build_command(input_path, temp_output_path, settings)

        def _make_progress_cb(tq, tid):
            async def _cb(pct: float):
                if tq is None:
                    return
                live_task = tq.tasks.get(tid)
                if live_task is not None:
                    pct_r = round(pct, 1)
                    live_task["encode_progress"] = {"percentage": pct_r}
                    logger.debug("[JOB %s] encode_progress=%.1f%%", tid[:8], pct_r)
                else:
                    logger.warning("[JOB %s] not found in task queue", tid)

            return _cb

        progress_cb = _make_progress_cb(task_queue, task_id) if task_queue else None

        success, error = await self.ffmpeg.execute(
            cmd,
            duration_secs=duration_secs,
            progress_cb=progress_cb,
        )

        if not success:
            raise Exception(f"Encoding failed: {error}")

        if not os.path.exists(temp_output_path):
            raise Exception("Output file was not created after encoding")
        if os.path.getsize(temp_output_path) == 0:
            raise Exception("Output file is empty after encoding")

        if os.path.exists(final_output_path):
            os.remove(final_output_path)
        os.rename(temp_output_path, final_output_path)

        if task_queue:
            live_task = task_queue.tasks.get(task_id)
            if live_task is not None:
                live_task["encode_progress"] = {"percentage": 100.0}
            task_queue.update_status(task_id, "encoding", 100)

        return final_output_path

    async def encode_many(self, task_data: dict, input_path: str, jobs: list[dict], settings_list: list[dict], task_queue=None) -> list[tuple[dict, str]]:
        task_id = task_data.get("task_id", "enc")
        task_folder = os.path.dirname(input_path)

        if not jobs:
            return []
        if len(jobs) != len(settings_list):
            raise Exception("Job/settings count mismatch")
        if not os.path.exists(input_path):
            raise Exception(f"Input file not found: {input_path}")

        try:
            media_info = await self.ffmpeg.probe_media(input_path)
        except Exception:
            media_info = {}

        duration_secs = self._duration_from_media_info(media_info)
        if duration_secs <= 0:
            duration_secs = await self._probe_duration(input_path, media_info=media_info)
        logger.info("[JOB %s] multi duration_secs=%.2f", task_id[:8], duration_secs)

        outputs = []
        final_paths: list[tuple[dict, str, str]] = []
        for job, settings in zip(jobs, settings_list):
            output_filename = job["output_filename"]
            resolution = job.get("resolution", "1080p")
            _, ext = os.path.splitext(output_filename)
            safe_ext = ext or ".mp4"
            temp_output_path = os.path.join(task_folder, f"_tmp_{task_id[:8]}_{resolution}{safe_ext}")
            final_output_path = os.path.join(task_folder, output_filename)
            settings["media_info"] = media_info
            outputs.append((temp_output_path, settings))
            final_paths.append((job, temp_output_path, final_output_path))

        cmd = self.ffmpeg.build_multi_command(input_path, outputs)

        def _make_progress_cb(tq, tid):
            async def _cb(pct: float):
                if tq is None:
                    return
                live_task = tq.tasks.get(tid)
                if live_task is not None:
                    live_task["encode_progress"] = {"percentage": round(pct, 1)}
            return _cb

        success, error = await self.ffmpeg.execute(
            cmd,
            duration_secs=duration_secs,
            progress_cb=_make_progress_cb(task_queue, task_id) if task_queue else None,
        )

        if not success:
            raise Exception(f"Encoding failed: {error}")

        result: list[tuple[dict, str]] = []
        for job, temp_output_path, final_output_path in final_paths:
            if not os.path.exists(temp_output_path):
                raise Exception(f"Output file was not created after encoding: {temp_output_path}")
            if os.path.getsize(temp_output_path) == 0:
                raise Exception(f"Output file is empty after encoding: {temp_output_path}")
            if os.path.exists(final_output_path):
                os.remove(final_output_path)
            os.rename(temp_output_path, final_output_path)
            result.append((job, final_output_path))

        if task_queue:
            live_task = task_queue.tasks.get(task_id)
            if live_task is not None:
                live_task["encode_progress"] = {"percentage": 100.0}
            task_queue.update_status(task_id, "encoding", 100)

        return result

    @staticmethod
    def _duration_from_media_info(info: dict) -> float:
        try:
            d = float(info.get("format", {}).get("duration", 0) or 0)
            if d > 0:
                return d
            for stream in info.get("streams", []):
                d = float(stream.get("duration", 0) or 0)
                if d > 0:
                    return d
        except Exception:
            pass
        return 0.0

    async def _probe_duration(self, input_path: str, media_info: dict | None = None) -> float:
        d = self._duration_from_media_info(media_info or {})
        if d > 0:
            return d

        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg.ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout:
                raw = stdout.decode().strip()
                if raw and raw.lower() not in ("n/a", ""):
                    return float(raw)
        except Exception:
            pass

        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg.ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout:
                raw = stdout.decode().strip().splitlines()[0]
                if raw and raw.lower() not in ("n/a", ""):
                    return float(raw)
        except Exception:
            pass
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg.ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-count_packets",
                "-show_entries", "stream=nb_read_packets,r_frame_rate",
                "-of", "json",
                input_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout:
                import json as _json
                data = _json.loads(stdout.decode())
                streams = data.get("streams", [])
                if streams:
                    s = streams[0]
                    n_packets = int(s.get("nb_read_packets", 0) or 0)
                    fps_raw = s.get("r_frame_rate", "0/1")
                    num, _, den = fps_raw.partition("/")
                    fps = float(num) / float(den) if float(den) else 0.0
                    if n_packets > 0 and fps > 0:
                        return n_packets / fps
        except Exception:
            pass

        return 0.0
