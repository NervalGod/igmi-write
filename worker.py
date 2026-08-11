"""
Поток-воркер: берёт задания из очереди и выполняет их последовательно.

Последовательность важна: Ollama на одном GPU с моделью 35B не тянет
параллельные тяжёлые запросы (см. комментарий в jobs.py).
"""
import logging
import os
import queue
import threading
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
from storage import add_to_history

logger = logging.getLogger(__name__)

# Глобальная очередь заданий (пополняется из server.py)
job_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()


def _build_output_filename(project: str) -> str:
    """Формирует имя: ProjectName_YYYYMMDD_HHMMSS.docx"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in project).strip()
    safe = safe[:80] or "project"
    return f"{safe}_{ts}.docx"


def _run_job(job_id: str, source_path: str, project: str, user: str) -> None:
    """Основная логика обработки одного задания."""
    started = time.perf_counter()
    logger.info(f"[{job_id}] Запуск задания: {project}")

    try:
        cfg = load_config()
        llm_options = dict(
            num_ctx=cfg.num_ctx,
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

        # === ШАГ 5: сохранение ===
        update_job(job_id, step=5, step_name=STEP_NAMES[5])
        output_name = _build_output_filename(project)
        output_path = Path("output") / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        template_doc.save(str(output_path))

        # Успех
        elapsed = time.perf_counter() - started
        logger.info(f"[{job_id}] Готово за {elapsed:.1f}с: {output_name}")

        update_job(
            job_id,
            status="done",
            step=5,
            step_name="Готово",
            filename=output_name,
        )

        # В историю
        add_to_history(
            filename=output_name,
            project=project,
            user=user,
            size=output_path.stat().st_size,
        )

    except Exception as e:
        logger.exception(f"[{job_id}] Ошибка задания")
        update_job(job_id, status="error", error=str(e))
    finally:
        # Чистим исходник из uploads/
        try:
            Path(source_path).unlink(missing_ok=True)
        except Exception:
            pass


def _worker_loop() -> None:
    """Бесконечный цикл воркера: берёт задание → выполняет → берёт следующее."""
    logger.info("Worker запущен, ожидает задания...")
    while True:
        task = job_queue.get()
        try:
            _run_job(
                job_id=task["job_id"],
                source_path=task["source_path"],
                project=task["project"],
                user=task.get("user", "anonymous"),
            )
        except Exception:
            logger.exception("Необработанная ошибка в worker")
        finally:
            job_queue.task_done()


def start_worker() -> None:
    """Запускает цикл воркера (блокирующая функция — запускать в отдельном потоке)."""
    _worker_loop()