"""
FastAPI-сервер: принимает файлы, создаёт задания, отдаёт статус, файлы и статистику.
"""
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from jobs import create_job, get_job, queue_position
from storage import get_history, get_stats, remove_from_history
from worker import job_queue, start_worker

# ===== Пути =====
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
FRONTEND_DIR = BASE_DIR / "frontend"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ===== Логирование =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ===== FastAPI =====
app = FastAPI(title="IGMI Document Generator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ==========================================
# Раздача фронтенда
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/stats.html", response_class=HTMLResponse)
async def stats_page():
    return FileResponse(FRONTEND_DIR / "stats.html")


# ==========================================
# API: генерация
# ==========================================
@app.post("/api/generate")
async def generate_document(
    file: UploadFile = File(...),
    project: str = Form(""),
    user: str = Form("anonymous"),
):
    """
    Загружает файл и создаёт задание.
    Возвращает job_id для polling'а статуса.
    """
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Нужен файл .docx")

    # Сохраняем загруженный файл
    safe_filename = "".join(
        c for c in file.filename if c.isalnum() or c in " _.-"
    ).strip()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_name = f"{ts}_{safe_filename}"
    upload_path = UPLOAD_DIR / upload_name

    try:
        with upload_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        raise HTTPException(500, f"Не удалось сохранить файл: {e}")

    # Название проекта: по умолчанию — имя файла без расширения
    project_name = project.strip() or Path(safe_filename).stem

    # Создаём Job
    job = create_job(project_name)

    # Кладём в очередь воркера
    job_queue.put({
        "job_id": job.id,
        "source_path": str(upload_path),
        "project": project_name,
        "user": user or "anonymous",
    })

    logger.info(f"Создано задание {job.id} для проекта '{project_name}' (user={user})")

    return {
        "job_id": job.id,
        "project": project_name,
    }


@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    """Статус задания — polling с фронта."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Задание не найдено")

    return {
        "id": job.id,
        "project": job.project,
        "status": job.status,
        "step": job.step,
        "step_name": job.step_name,
        "error": job.error,
        "filename": job.filename,
        "queue_position": queue_position(job_id),
    }


# ==========================================
# API: готовые файлы
# ==========================================
@app.get("/api/files")
async def list_files():
    """История сгенерированных файлов, сгруппированная по проектам."""
    history = get_history()

    groups: dict = {}
    for item in history:
        project = item.get("project", "Без названия")
        groups.setdefault(project, []).append(item)

    # Проекты — по дате самого свежего файла (новые сверху)
    sorted_groups = sorted(
        groups.items(),
        key=lambda x: max(f["date"] for f in x[1]),
        reverse=True,
    )
    return [{"project": p, "files": files} for p, files in sorted_groups]


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """Скачивание готового файла."""
    safe_name = Path(filename).name  # защита от path traversal
    path = OUTPUT_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Файл не найден")

    return FileResponse(
        path,
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.delete("/api/files/{filename}")
async def delete_file(filename: str):
    """Удаление файла и записи из истории."""
    safe_name = Path(filename).name
    path = OUTPUT_DIR / safe_name
    if path.exists():
        path.unlink()
    remove_from_history(safe_name)
    return {"ok": True}


# ==========================================
# API: статистика (для /stats.html)
# ==========================================
@app.get("/api/stats")
async def statistics():
    return get_stats()


# ==========================================
# Запуск
# ==========================================
def start_worker_thread() -> None:
    t = threading.Thread(target=start_worker, daemon=True)
    t.start()
    logger.info("Worker-поток запущен")


if __name__ == "__main__":
    import uvicorn
    start_worker_thread()
    logger.info("Старт сервера: http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")