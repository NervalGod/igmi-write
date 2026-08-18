"""
Поток-воркер: берёт задания из очереди и выполняет их последовательно.
Интегрирован с psycopg (синхронные функции для сохранения в БД).
"""
import logging
import queue
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import docx

from config import load_config
from filler import (
    apply_paragraph_extraction,
    apply_table_extraction,
    extract_paragraph_values,
    extract_table_values,
)
from jobs import STEP_NAMES, update_job
from parser import paragraphs_of, parse, tables_of

# Импорт синхронных функций для работы с БД из worker-потока
from database import get_or_create_user_sync, add_file_sync

logger = logging.getLogger(__name__)

job_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()


def _build_output_filename(project: str) -> str:
    """Формирует имя файла: ProjectName_YYYYMMDD_HHMMSS.docx"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in project).strip()
    safe = safe[:80] or "project"
    return f"{safe}_{ts}.docx"


def _get_safe_project_dir_name(project: str) -> str:
    """Возвращает безопасное имя папки для проекта."""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in project).strip()
    return safe[:80] or "project"


def _run_job(job_id: str, source_path: str, project: str, user_ip: str) -> None:
    """Основная логика обработки одного задания."""
    started = time.perf_counter()

    print(f"[{datetime.now().strftime('%H:%M:%S')}] ▶ Начало обработки: {project}")
    logger.info(f"[{job_id}] Запуск задания: {project} (ip={user_ip})")

    try:
        cfg = load_config()
        llm_options = dict(
            num_ctx=Config.num_ctx if 'Config' in locals() else cfg.num_ctx, # fallback на cfg
            temperature=cfg.temperature,
            keep_alive=cfg.keep_alive,
            num_predict=cfg.num_predict,
        )

        # === ШАГ 1: парсинг исходника ===
        update_job(job_id, status="running", step=1, step_name=STEP_NAMES[1])
        source_doc = docx.Document(source_path)
        source_blocks = parse(source_doc, with_refs=False)

        # === ШАГ 2: парсинг шаблона ===
        update_job(job_id, step=2, step_name=STEP_NAMES[2])
        template_doc = docx.Document(cfg.template_path)
        template_blocks = parse(template_doc, with_refs=True)
        template_tables = tables_of(template_blocks)
        template_paragraphs = [
            p for p in paragraphs_of(template_blocks) if "<Заполнить>" in p.text
        ]

        # === ШАГ 3: заполнение таблиц ===
        update_job(job_id, step=3, step_name=STEP_NAMES[3])
        table_results = extract_table_values(
            source_tables=tables_of(source_blocks),
            template_tables=template_tables,
            embed_model=cfg.embed_model,
            llm_model=cfg.model,
            llm_options=llm_options,
        )
        apply_table_extraction(template_tables, table_results)

        # === ШАГ 4: заполнение параграфов ===
        update_job(job_id, step=4, step_name=STEP_NAMES[4])
        paragraph_results = extract_paragraph_values(
            source_paragraphs=paragraphs_of(source_blocks),
            targets=template_paragraphs,
            embed_model=cfg.embed_model,
            llm_model=cfg.model,
            llm_options=llm_options,
        )
        apply_paragraph_extraction(template_paragraphs, paragraph_results)

        # === ШАГ 5: сохранение файла и запись в БД ===
        update_job(job_id, step=5, step_name=STEP_NAMES[5])
        
        output_name = _build_output_filename(project)
        safe_project_name = _get_safe_project_dir_name(project)
        
        # Создаём подпапку для проекта, если её нет
        project_dir = Path("output") / safe_project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = project_dir / output_name
        template_doc.save(str(output_path))

        elapsed = time.perf_counter() - started
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✔ Готово за {elapsed:.1f}с: {output_path}")

        update_job(job_id, status="done", step=5, step_name="Готово", filename=output_name)

        # 🆕 Запись метаданных в PostgreSQL
        try:
            # 1. Получаем или создаём пользователя по IP
            user = get_or_create_user_sync(user_ip)
            
            # 2. Формируем относительный путь для БД (например: "Проект_А/Проект_А_20231024.docx")
            # Это нужно, чтобы endpoint /api/download/{rel_path} мог безопасно найти файл
            rel_path = f"{safe_project_name}/{output_name}"
            
            # 3. Добавляем запись о файле
            add_file_sync(
                user_id=str(user["id"]),
                project=project,
                path=rel_path,
                size_bytes=output_path.stat().st_size,
            )
            logger.info(f"[{job_id}] Запись в БД успешна: user={user['id']}, path={rel_path}")
            
        except Exception as db_err:
            # Если БД недоступна, файл всё равно сохранён на диске. 
            # Мы логируем ошибку, но не прерываем задание как failed.
            logger.error(f"[{job_id}] Ошибка записи метаданных в БД: {db_err}")

    except Exception as e:
        logger.exception(f"[{job_id}] Критическая ошибка задания")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✖ Ошибка: {e}")
        update_job(job_id, status="error", error=str(e))
    finally:
        # Всегда очищаем временный загруженный файл
        try:
            Path(source_path).unlink(missing_ok=True)
        except Exception:
            pass


def _worker_loop() -> None:
    logger.info("Worker запущен, ожидает задания...")
    while True:
        task = job_queue.get()
        try:
            _run_job(
                job_id=task["job_id"],
                source_path=task["source_path"],
                project=task["project"],
                user_ip=task.get("user_ip", "unknown"),
            )
        except Exception:
            logger.exception("Необработанная ошибка в worker")
        finally:
            job_queue.task_done()


def start_worker() -> None:
    _worker_loop()