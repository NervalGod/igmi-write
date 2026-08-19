"""
Хранилище состояний заданий обработки документов.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

STEP_NAMES = {
    1: "Анализ документа",
    2: "Заполнение таблиц",
    3: "Заполнение параграфов",
    4: "Сохранение файла",
}


@dataclass
class Job:
    id: str
    project: str
    status: str = "queued"  # queued | running | done | error
    step: int = 0
    step_name: str = ""
    error: Optional[str] = None
    filename: Optional[str] = None
    created_at: float = field(default_factory=time.time)


_jobs: Dict[str, Job] = {}
_lock = threading.Lock()


def create_job(project: str) -> Job:
    job = Job(id=str(uuid.uuid4()), project=project)
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def update_job(job_id: str, **kwargs) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        for key, value in kwargs.items():
            setattr(job, key, value)


def queue_position(job_id: str) -> int:
    """Сколько заданий стоит перед этим в статусе queued (для отображения на фронте)."""
    with _lock:
        target = _jobs.get(job_id)
        if target is None or target.status != "queued":
            return 0
        return sum(
            1 for j in _jobs.values()
            if j.status == "queued" and j.created_at < target.created_at
        )