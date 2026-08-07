import logging
import os
import time

import docx

from config import load_config
from filler import (
    apply_paragraph_extraction,
    apply_table_extraction,
    extract_paragraph_values,
    extract_table_values,
    load_extraction,
    save_extraction,
)
from parser import paragraphs_of, parse, tables_of


def setup_logging(log_file: str):
    """Настраивает логирование с очисткой лог-файла при каждом запуске."""
    if os.path.exists(log_file):
        os.remove(log_file)

    _formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _file_handler = logging.FileHandler(log_file, encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(_formatter)

    _console_handler = logging.StreamHandler()
    _console_handler.setLevel(logging.INFO)
    _console_handler.setFormatter(_formatter)

    logging.basicConfig(level=logging.DEBUG, handlers=[_file_handler, _console_handler])


logger = logging.getLogger(__name__)


def main() -> None:
    started = time.perf_counter()

    try:
        cfg = load_config()
    except Exception as e:
        logging.error(f"Ошибка загрузки конфигурации: {e}")
        return

    setup_logging(cfg.log_file)
    logger.info("=" * 80)
    logger.info(f"НАЧАЛО ОБРАБОТКИ | Модель: {cfg.model} | Embeddings: {cfg.embed_model}")
    logger.info(f"Исходник: {cfg.source_path}")
    logger.info(f"Шаблон: {cfg.template_path}")
    logger.info(f"Выход: {cfg.output_path}")
    logger.info("=" * 80)

    source_doc = docx.Document(cfg.source_path)
    template_doc = docx.Document(cfg.template_path)

    logger.info("Парсинг исходного документа...")
    source_blocks = parse(source_doc, with_refs=False)

    logger.info("Парсинг шаблонного документа...")
    template_blocks = parse(template_doc, with_refs=True)

    template_tables = tables_of(template_blocks)
    # targets вычисляем ОДИН раз и переиспользуем для extract И apply —
    # para_index в результатах извлечения ссылается на позиции именно в этом списке.
    template_paragraph_targets = [p for p in paragraphs_of(template_blocks) if "<Заполнить>" in p.text]

    llm_options = dict(
        num_ctx=cfg.num_ctx,
        temperature=cfg.temperature,
        keep_alive=cfg.keep_alive,
        num_predict=cfg.num_predict,
    )

    # --- ШАГ 1: извлечение (либо из кэша, либо реальными вызовами LLM) ---
    if cfg.use_extraction_cache and cfg.extraction_cache_path and os.path.exists(cfg.extraction_cache_path):
        logger.info(f"Кэш найден — загружаем результаты извлечения вместо вызова LLM: {cfg.extraction_cache_path}")
        table_results, paragraph_results = load_extraction(cfg.extraction_cache_path)
    else:
        logger.info("Извлечение данных: таблицы...")
        table_results = extract_table_values(
            source_tables=tables_of(source_blocks),
            template_tables=template_tables,
            embed_model=cfg.embed_model,
            llm_model=cfg.model,
            llm_options=llm_options,
        )

        logger.info("Извлечение данных: параграфы...")
        paragraph_results = extract_paragraph_values(
            source_paragraphs=paragraphs_of(source_blocks),
            targets=template_paragraph_targets,
            embed_model=cfg.embed_model,
            llm_model=cfg.model,
            llm_options=llm_options,
        )

        if cfg.extraction_cache_path:
            save_extraction(cfg.extraction_cache_path, table_results, paragraph_results)

    # --- ШАГ 2: запись в docx (чисто механическая, LLM тут больше не участвует) ---
    logger.info("Запись в шаблон: таблицы...")
    filled_tables = apply_table_extraction(template_tables, table_results)
    logger.info(f"Таблицы: заполнено {filled_tables}/{len(template_tables)}")

    logger.info("Запись в шаблон: параграфы...")
    filled_paragraphs = apply_paragraph_extraction(template_paragraph_targets, paragraph_results)
    logger.info(f"Параграфы: заполнено {filled_paragraphs}/{len(template_paragraph_targets)}")

    template_doc.save(cfg.output_path)

    elapsed = time.perf_counter() - started
    logger.info("=" * 80)
    logger.info(f"УСПЕШНО: {cfg.output_path}")
    logger.info(f"Общее время обработки: {elapsed:.1f} сек")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()