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
from fastapi import Request

from jobs import create_job, get_job, queue_position
from storage import (
    OUTPUT_DIR,
    delete_file,
    get_history,
    get_stats,
    remove_from_history,
    resolve_file_path,
)
from worker import job_queue, start_worker

# ===== Пути =====
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
FRONTEND_DIR = BASE_DIR / "frontend"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ===== Логирование: всё в файл, в консоль только критичное =====
def setup_logging() -> None:
    log_file = BASE_DIR / "server.log"
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.CRITICAL)  # почти ничего в консоль
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


setup_logging()
logger = logging.getLogger(__name__)

# ===== FastAPI =====
app = FastAPI(title="IGMI Document Generator API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# Раздача фронтенда
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def index_root():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/index.html", response_class=HTMLResponse)
async def index_page():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/stats.html", response_class=HTMLResponse)
async def stats_page():
    return FileResponse(FRONTEND_DIR / "stats.html")


@app.get("/style.css")
async def style_css():
    return FileResponse(FRONTEND_DIR / "style.css", media_type="text/css")


@app.get("/app.js")
async def app_js():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")


@app.get("/stats.js")
async def stats_js():
    return FileResponse(FRONTEND_DIR / "stats.js", media_type="application/javascript")


@app.post("/api/generate")
async def generate_document(
    request: Request,                      # ← для получения IP
    file: UploadFile = File(...),
    project: str = Form(""),
):
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Нужен файл .docx")

    # === Определяем IP клиента ===
    # Если сервер за обратным прокси (nginx и т.п.), берём X-Forwarded-For
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

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

    project_name = project.strip() 
    if not project_name:
        raise HTTPException(400, "Название проекта обязательно")
    job = create_job(project_name)

    job_queue.put({
        "job_id": job.id,
        "source_path": str(upload_path),
        "project": project_name,
        "user_ip": client_ip,              # ← передаём IP вместо user
    })

    logger.info(f"Создано задание {job.id} для проекта '{project_name}' (ip={client_ip})")

    return {"job_id": job.id, "project": project_name}

@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
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

    sorted_groups = sorted(
        groups.items(),
        key=lambda x: max(f["date"] for f in x[1]),
        reverse=True,
    )
    return [{"project": p, "files": files} for p, files in sorted_groups]


@app.get("/api/download/{rel_path:path}")
async def download_file(rel_path: str):
    """Скачивание готового файла по относительному пути (проект/файл)."""
    path = resolve_file_path(rel_path)
    if not path:
        raise HTTPException(404, "Файл не найден")

    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.delete("/api/files/{rel_path:path}")
async def delete_file_endpoint(rel_path: str):
    """Удаление файла и записи из истории."""
    delete_file(rel_path)
    remove_from_history(rel_path)
    return {"ok": True}


# ==========================================
# API: статистика
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

    # Единственное приветствие в консоль
    print("=" * 60)
    print("  IGMI Document Generator")
    print("  http://127.0.0.1:8000")
    print("  Логи: server.log")
    print("=" * 60)

    start_worker_thread()
    logger.info("Старт сервера: http://127.0.0.1:8000")

    # Uvicorn сам по себе болтлив — приглушаем его ACCESS-логи
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",  # было "info"
        access_log=False,     # убираем "GET /style.css 200 OK" из консоли
    )