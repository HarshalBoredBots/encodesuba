import asyncio
import uuid
from datetime import datetime

from src.utils.dc_checker import get_file_dc

_ACTIVE_STATUSES = frozenset({
    "starting", "queued", "downloading", "ready", "encoding", "uploading"
})


class TaskQueue:
    def __init__(self):
        self.queue:        list[str]       = []
        self.tasks:        dict[str, dict] = {}
        self.lock          = asyncio.Lock()
        self.processing    = False
        self.current_task: str | None      = None

    def create_task(self, task_data: dict) -> str:
        task_id = str(uuid.uuid4())[:8]
        file_id = task_data.get("file_id", "")
        dc      = get_file_dc(file_id) if file_id else None

        task = {
            "task_id":    task_id,
            "created_at": datetime.utcnow().isoformat(),
            "started_at": None,
            "status":     "queued",
            "progress":   0,
            "dc":         dc,
        }
        task.update(task_data)
        self.tasks[task_id] = task
        self.queue.append(task_id)
        return task_id

    def get_task(self, task_id: str) -> dict | None:
        return self.tasks.get(task_id)

    def update_status(self, task_id: str, status: str, progress: int | float | None = None):
        task = self.tasks.get(task_id)
        if task is None:
            return
        task["status"] = status
        if progress is not None:
            task["progress"] = progress
        if status in ("downloading", "encoding", "uploading") and not task.get("started_at"):
            task["started_at"] = datetime.utcnow().isoformat()
        self._refresh_processing_flag()

    def remove_task(self, task_id: str):
        self.tasks.pop(task_id, None)
        try:
            self.queue.remove(task_id)
        except ValueError:
            pass
        if self.current_task == task_id:
            self.current_task = None
        self._refresh_processing_flag()

    def get_queue_position(self, task_id: str) -> int:
        if task_id == self.current_task:
            return 0

        position = 1
        for tid in self.queue:
            if tid == self.current_task:
                continue
            task = self.tasks.get(tid)
            if task and task.get("status") in _ACTIVE_STATUSES:
                if tid == task_id:
                    return position
                position += 1
        return 0

    def get_next_queue_position(self, task_id: str) -> int:
        return self.get_queue_position(task_id)

    def shift_task(self, task_id: str, next_position: int) -> tuple[bool, str, int]:
        task = self.tasks.get(task_id)
        if not task:
            return False, "Task not found.", 0
        if task_id not in self.queue:
            return False, "Task is not in the queue.", 0
        if task_id == self.current_task:
            return False, "The currently running task cannot be shifted.", 0
        current_position = self.get_next_queue_position(task_id)
        if current_position == 1:
            return False, "Task 1 is locked as the next processing slot.", 0
        if task.get("status") not in {"queued", "ready"}:
            return False, "Only waiting or prefetched tasks can be shifted.", 0
        if int(next_position) < 2:
            return False, "Positions 0 and 1 are locked. Use position 2 or higher.", 0

        next_position = int(next_position)
        self.queue.remove(task_id)

        current_id = self.current_task if self.current_task in self.queue else None
        first_shiftable_index = self.queue.index(current_id) + 1 if current_id else 0
        waiting_ids = [
            tid for tid in self.queue
            if tid != current_id
            and self.tasks.get(tid)
            and self.tasks[tid].get("status") in _ACTIVE_STATUSES
        ]

        next_position = min(next_position, len(waiting_ids) + 1)
        if next_position <= len(waiting_ids):
            insert_at = self.queue.index(waiting_ids[next_position - 1])
        elif waiting_ids:
            insert_at = self.queue.index(waiting_ids[-1]) + 1
        elif current_id:
            insert_at = self.queue.index(current_id) + 1
        else:
            insert_at = 0

        if current_id:
            insert_at = max(insert_at, first_shiftable_index)

        self.queue.insert(insert_at, task_id)
        return True, "Task shifted.", next_position

    def purge_stale_tasks(self):
        _in_progress = frozenset({"starting", "downloading", "ready", "encoding", "uploading"})
        for task in self.tasks.values():
            if task.get("status") in _in_progress:
                task["status"] = "queued"
                task["progress"] = 0
                task.pop("_downloaded_path", None)
                task.pop("encode_progress", None)
                task.pop("upload_progress", None)
                task.pop("progress_details", None)

        queue_set = set(self.queue)
        stale = [
            tid for tid, task in list(self.tasks.items())
            if tid not in queue_set or task.get("status") not in _ACTIVE_STATUSES
        ]
        for tid in stale:
            self.tasks.pop(tid, None)
            try:
                self.queue.remove(tid)
            except ValueError:
                pass

        self.queue = [tid for tid in self.queue if tid in self.tasks]
        if self.current_task and self.current_task not in self.tasks:
            self.current_task = None
        self._refresh_processing_flag()

    def is_processing(self) -> bool:
        return self.processing

    def get_current_task(self) -> dict | None:
        return self.tasks.get(self.current_task) if self.current_task else None

    def _refresh_processing_flag(self):
        self.processing = any(
            t.get("status") in (_ACTIVE_STATUSES - {"queued"})
            for t in self.tasks.values()
        )