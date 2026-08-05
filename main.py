import logging
import os
import time

import docx

from config import load_config
from filler import fill_paragraphs, fill_tables
from parser import paragraphs_of, parse, tables_of


def setup_logging(log_file: str):
    """Настраивает логирование с очисткой лог-файла при каждом запуске."""
    # Очищаем лог-файл
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

    # Настраиваем логирование ПОСЛЕ загрузки конфига
    setup_logging(cfg.log_file)
    logger.info("=" * 80)
    logger.info(f"НАЧАЛО ОБРАБОТКИ | Модель: {cfg.model} | Embeddings: {cfg.embed_model}")
    logger.info(f"Исходник: {cfg.source_path}")
    logger.info(f"Шаблон: {cfg.template_path}")
    logger.info(f"Выход: {cfg.output_path}")
    logger.info(f"Лог-файл: {cfg.log_file}")
    logger.info("=" * 80)

    source_doc = docx.Document(cfg.source_path)
    template_doc = docx.Document(cfg.template_path)

    logger.info("Парсинг исходного документа...")
    source_blocks = parse(source_doc, with_refs=False)

    logger.info("Парсинг шаблонного документа...")
    template_blocks = parse(template_doc, with_refs=True)

    llm_options = dict(
        num_ctx=cfg.num_ctx,
        temperature=cfg.temperature,
        keep_alive=cfg.keep_alive,
        num_predict=cfg.num_predict,
    )

    logger.info("Заполнение таблиц...")
    fill_tables(
        source_tables=tables_of(source_blocks),
        template_tables=tables_of(template_blocks),
        embed_model=cfg.embed_model,
        llm_model=cfg.model,
        llm_options=llm_options,
    )

    logger.info("Заполнение параграфов...")
    fill_paragraphs(
        source_paragraphs=paragraphs_of(source_blocks),
        template_paragraphs=paragraphs_of(template_blocks),
        embed_model=cfg.embed_model,
        llm_model=cfg.model,
        llm_options=llm_options,
    )

    template_doc.save(cfg.output_path)

    elapsed = time.perf_counter() - started
    logger.info("=" * 80)
    logger.info(f"УСПЕШНО: {cfg.output_path}")
    logger.info(f"Общее время обработки: {elapsed:.1f} сек")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()