import logging
import time

import docx

from config import load_config
from filler import fill_paragraphs, fill_tables
from parser import paragraphs_of, parse, tables_of

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("generation.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def main() -> None:
    started = time.perf_counter()

    try:
        cfg = load_config()
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        return

    logger.info(f"Модель: {cfg.model} | embeddings: {cfg.embed_model} | num_ctx: {cfg.num_ctx}")

    source_doc = docx.Document(cfg.source_path)
    template_doc = docx.Document(cfg.template_path)

    # with_refs=True для шаблона — блоки хранят прямую ссылку на объекты
    # python-docx, чтобы писать результат без повторного поиска по индексам.
    source_blocks = parse(source_doc, with_refs=False)
    template_blocks = parse(template_doc, with_refs=True)

    llm_options = dict(num_ctx=cfg.num_ctx, temperature=cfg.temperature, keep_alive=cfg.keep_alive)

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
    logger.info(f"Готово: {cfg.output_path} (за {elapsed:.1f} сек)")


if __name__ == "__main__":
    main()
