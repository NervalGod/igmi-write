"""
FastAPI-сервер: принимает файлы, создаёт задания, отдаёт статус, файлы и статистику.
Полностью переведён на работу с PostgreSQL через database.py.
"""
import logging
import shutil
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from jobs import create_job, get_job, queue_position
from database import (
    init_async_pool,
    close_async_pool,
    init_sync_pool,
    close_sync_pool,
    get_files_grouped,
    get_stats,
    get_async_conn,  # Используем для удаления записи из БД
)
from worker import job_queue, start_worker

# ===== Пути =====
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
FRONTEND_DIR = BASE_DIR / "frontend"
OUTPUT_DIR = BASE_DIR / "output"

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
    console_handler.setLevel(logging.CRITICAL)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


setup_logging()
logger = logging.getLogger(__name__)


# ==========================================
# Lifespan: инициализация пулов БД
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_async_pool()
    init_sync_pool()
    logger.info("Пулы подключений к БД инициализированы")
    yield
    # Shutdown
    await close_async_pool()
    close_sync_pool()
    logger.info("Пулы подключений к БД закрыты")


# ===== FastAPI =====
app = FastAPI(title="IGMI Document Generator API", version="1.3.0", lifespan=lifespan)

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


# ==========================================
# API: генерация
# ==========================================
@app.post("/api/generate")
async def generate_document(
    request: Request,
    file: UploadFile = File(...),
    project: str = Form(""),
):
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Нужен файл .docx")

    # === Определяем IP клиента ===
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
        "user_ip": client_ip,
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
# API: готовые файлы (из БД)
# ==========================================
@app.get("/api/files")
async def list_files():
    """История сгенерированных файлов, сгруппированная по проектам."""
    return await get_files_grouped()


# ==========================================
# API: скачивание и удаление (без зависимости от storage.py)
# ==========================================
def _get_safe_file_path(rel_path: str) -> Path | None:
    """
    Проверяет и возвращает безопасный путь к файлу внутри OUTPUT_DIR.
    Защищает от атак path traversal (например, ../../../etc/passwd).
    """
    parts = Path(rel_path).parts
    if len(parts) != 2:
        return None
    
    file_path = (OUTPUT_DIR / parts[0] / parts[1]).resolve()
    
    # Проверяем, что разрешённый путь действительно находится внутри OUTPUT_DIR
    try:
        file_path.relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return None
        
    return file_path if file_path.exists() and file_path.is_file() else None


@app.get("/api/download/{rel_path:path}")
async def download_file(rel_path: str):
    """Скачивание готового файла по относительному пути (проект/файл)."""
    file_path = _get_safe_file_path(rel_path)
    if not file_path:
        raise HTTPException(404, "Файл не найден")

    return FileResponse(
        file_path,
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.delete("/api/files/{rel_path:path}")
async def delete_file_endpoint(rel_path: str):
    """Удаление файла с диска и записи из БД."""
    # 1. Удаляем физический файл
    file_path = _get_safe_file_path(rel_path)
    if file_path:
        try:
            file_path.unlink()
        except Exception as e:
            logger.warning(f"Не удалось удалить файл {file_path}: {e}")

    # 2. Удаляем запись из БД
    try:
        async with get_async_conn() as conn:
            await conn.execute("DELETE FROM files WHERE path = %s", (rel_path,))
    except Exception as e:
        logger.error(f"Ошибка удаления записи из БД для {rel_path}: {e}")
        raise HTTPException(500, "Ошибка при удалении записи из базы данных")

    return {"ok": True}


# ==========================================
# API: статистика (из БД)
# ==========================================
@app.get("/api/stats")
async def statistics():
    """Статистика из БД."""
    return await get_stats()


# ==========================================
# Запуск
# ==========================================
def start_worker_thread() -> None:
    t = threading.Thread(target=start_worker, daemon=True)
    t.start()
    logger.info("Worker-поток запущен")


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  IGMI Document Generator")
    print("  http://127.0.0.1:8000")
    print("  Логи: server.log")
    print("=" * 60)

    start_worker_thread()
    logger.info("Старт сервера: http://127.0.0.1:8000")

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        access_log=False,
    )