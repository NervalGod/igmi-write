"""
Заполнение шаблона данными из исходного документа.

Архитектура: РОВНО ДВА вызова LLM за весь прогон — один batch-запрос на
все таблицы, один batch-запрос на все параграфы. Это осознанный выбор:
на практике много мелких вызовов (по одному на элемент) оказалось в разы
медленнее двух пакетных — у каждого вызова к локальной LLM есть заметный
фиксированный оверхед, и дробить его дальше вредно для скорости.

Чтобы не пересылать в каждом из двух вызовов ВЕСЬ исходник (это и есть
основной источник долгого prefill на большом документе), перед сборкой
промпта исходные блоки предварительно фильтруются через эмбеддинги:
для каждой цели (таблица/параграф шаблона) находится top-K похожих по
смыслу исходных элементов, а в промпт идёт объединение (union) всех
таких кандидатов по всем целям — а не весь документ целиком.

Итоговое решение "что чему соответствует и как разложить данные"
по-прежнему полностью остаётся за LLM: она получает несколько
кандидатов на цель и сама выбирает семантически подходящий, даже если
названия/структура не совпадают дословно. Эмбеддинги только сужают
поле поиска, а не подменяют его.
"""

import logging
from typing import Dict, List

from embeddings import embed_texts, most_similar
from llm import request
from parser import ParagraphBlock, TableBlock, is_empty_cell, table_signature

logger = logging.getLogger(__name__)

FALLBACK_VALUE = "Нет данных"


def _select_relevant(
    source_items: List,
    source_signatures: List[str],
    target_signatures: List[str],
    embed_model: str,
    top_k_per_target: int,
) -> List:
    """
    Возвращает подмножество source_items, релевантное хотя бы одной цели
    (union top-K кандидатов по каждой цели), с сохранением исходного
    порядка. Если эмбеддинги недоступны/пусты — возвращает всё как есть
    (безопасный fallback, ничего не теряем).
    """
    if not source_items or not target_signatures:
        return source_items

    try:
        source_vectors = embed_texts(source_signatures, embed_model)
        target_vectors = embed_texts(target_signatures, embed_model)
    except Exception:
        logger.exception("Эмбеддинг-модель недоступна, используем весь исходник без фильтрации")
        return source_items

    relevant_idx = set()
    for tv in target_vectors:
        relevant_idx.update(most_similar(tv, source_vectors, top_k=top_k_per_target))

    kept = sorted(relevant_idx)
    logger.info(f"Отбор контекста: {len(kept)}/{len(source_items)} исходных элементов релевантны целям")
    return [source_items[i] for i in kept]


def _parse_id_response(response: str, ids: List[str]) -> Dict[str, List[str]]:
    """Парсит ответ вида 'ID: значение' построчно, ID отсортированы по длине (убыв.), чтобы TBL_10 не матчился как TBL_1."""
    result: Dict[str, List[str]] = {i: [] for i in ids}
    sorted_ids = sorted(ids, key=len, reverse=True)

    for line in response.splitlines():
        line = line.strip()
        if not line:
            continue
        for tid in sorted_ids:
            prefix = f"{tid}:"
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if value:
                    result[tid].append(value)
                break
    return result


# ---------------------------------------------------------------- tables --

def _table_to_text(table: TableBlock, tid: str) -> str:
    lines = [f"[{tid}] Название: {table.name}"]
    for i, row in enumerate(table.data):
        cells = ["[ПУСТО]" if is_empty_cell(c) else c.strip() for c in row]
        lines.append(f"  Строка {i + 1}: {' | '.join(cells)}")
    return "\n".join(lines)


def _apply_table_values(table: TableBlock, values: List[str]) -> None:
    idx = 0
    docx_table = table.docx_obj
    for row_idx, row in enumerate(table.data):
        for col_idx, cell in enumerate(row):
            if not is_empty_cell(cell):
                continue
            value = values[idx] if idx < len(values) else FALLBACK_VALUE
            idx += 1
            docx_cell = docx_table.rows[row_idx].cells[col_idx]
            docx_cell.paragraphs[0].clear()
            docx_cell.paragraphs[0].add_run(value)


def fill_tables(
    source_tables: List[TableBlock],
    template_tables: List[TableBlock],
    embed_model: str,
    llm_model: str,
    llm_options: dict,
    top_k_per_target: int = 3,
) -> None:
    """
    Один вызов LLM на ВСЕ таблицы шаблона сразу (без разбивки на пакеты —
    дробление только умножает фиксированный оверхед на вызов, не повышая
    качество). Единственное сжатие контекста — эмбеддинг-предфильтр
    исходных таблиц перед сборкой промпта (см. _select_relevant).
    """
    if not template_tables:
        logger.info("Таблицы в шаблоне не найдены, пропуск.")
        return
    if not source_tables:
        logger.warning("В исходнике нет таблиц — заполнять нечем.")
        return

    relevant_sources = _select_relevant(
        source_items=source_tables,
        source_signatures=[table_signature(t) for t in source_tables],
        target_signatures=[table_signature(t) for t in template_tables],
        embed_model=embed_model,
        top_k_per_target=top_k_per_target,
    )

    empty_counts = {}
    target_blocks = []
    for i, t in enumerate(template_tables):
        tid = f"TBL_{i}"
        empty_counts[tid] = sum(is_empty_cell(c) for row in t.data for c in row)
        target_blocks.append(_table_to_text(t, tid))

    source_blocks = [_table_to_text(t, f"SRC_{i}") for i, t in enumerate(relevant_sources)]
    required_ids = ", ".join(tid for tid, cnt in empty_counts.items() if cnt > 0)

    prompt = f"""Ты — ассистент по заполнению документов.
У тебя есть ИСХОДНЫЕ ТАБЛИЦЫ с данными и ЦЕЛЕВЫЕ ТАБЛИЦЫ из шаблона, которые нужно заполнить.

=== ИСХОДНЫЕ ТАБЛИЦЫ ===
{chr(10).join(source_blocks)}

=== ЦЕЛЕВЫЕ ТАБЛИЦЫ (ШАБЛОН) ===
{chr(10).join(target_blocks)}

ЗАДАЧА:
Для каждой целевой таблицы (TBL_0, TBL_1, ...) найди среди исходных таблиц ту,
которая семантически соответствует (названия и заголовки могут не совпадать
дословно — ориентируйся на смысл). Перечисли значения для заполнения пустых
ячеек [ПУСТО] в порядке обхода: сверху вниз, слева направо.

ОБЯЗАТЕЛЬНО выведи хотя бы одну строку для КАЖДОГО из следующих ID, без
исключений: {required_ids}.
Если для какой-то таблицы не нашлось подходящей исходной — всё равно выведи
для неё строки со значением "{FALLBACK_VALUE}" (по одной на каждую пустую
ячейку), а не пропускай этот ID молча.

ФОРМАТ ОТВЕТА (СТРОГО):
Каждое значение на отдельной строке: "TBL_X: значение".
Если пустых ячеек несколько — несколько строк с одинаковым ID по порядку.
Никаких пояснений, приветствий, нумерации или markdown — только строки "TBL_X: значение".

ПРИМЕР:
TBL_0: 15.5
TBL_0: 23.1
TBL_1: Иванов И.И.
TBL_2: {FALLBACK_VALUE}"""

    try:
        response = request(prompt, model=llm_model, **llm_options)
    except Exception:
        logger.exception("Запрос к LLM по таблицам завершился ошибкой — таблицы останутся незаполненными")
        return

    parsed = _parse_id_response(response, list(empty_counts.keys()))

    filled = 0
    for i, template_table in enumerate(template_tables):
        tid = f"TBL_{i}"
        expected = empty_counts[tid]
        if expected == 0:
            continue

        values = parsed.get(tid, [])
        if len(values) < expected:
            logger.warning(
                f"{tid} ('{template_table.name}'): получено {len(values)}/{expected} значений. "
                f"Если 0 — смотрите generation.log (DEBUG) на сырой ответ модели."
            )
            values = values + [FALLBACK_VALUE] * (expected - len(values))
        elif len(values) > expected:
            values = values[:expected]

        _apply_table_values(template_table, values)
        filled += 1
        logger.info(f"Таблица '{template_table.name}' ({tid}) заполнена: {expected} значений")

    logger.info(f"Таблицы: заполнено {filled}/{len(template_tables)}")


# ------------------------------------------------------------ paragraphs --

def _replace_placeholders(text: str, values: List[str]) -> str:
    parts = text.split("<Заполнить>")
    result = parts[0]
    for i, part in enumerate(parts[1:]):
        value = values[i] if i < len(values) else "<Заполнить>"
        result += value + part
    return result


def fill_paragraphs(
    source_paragraphs: List[ParagraphBlock],
    template_paragraphs: List[ParagraphBlock],
    embed_model: str,
    llm_model: str,
    llm_options: dict,
    top_k_per_target: int = 8,
) -> None:
    targets = [p for p in template_paragraphs if "<Заполнить>" in p.text]
    if not targets:
        logger.info("Теги <Заполнить> не найдены, пропуск.")
        return
    if not source_paragraphs:
        logger.warning("В исходнике нет параграфов — заполнять нечем.")
        return

    relevant_sources = _select_relevant(
        source_items=source_paragraphs,
        source_signatures=[p.text for p in source_paragraphs],
        target_signatures=[p.text for p in targets],
        embed_model=embed_model,
        top_k_per_target=top_k_per_target,
    )

    source_context = "\n".join(f"• {p.text}" for p in relevant_sources)

    counts = {}
    tasks_text_parts = []
    for i, t in enumerate(targets):
        pid = f"PARA_{i}"
        counts[pid] = t.text.count("<Заполнить>")
        tasks_text_parts.append(f"[{pid}] (требуется {counts[pid]} значений): {t.text}")

    prompt = f"""Ты — ассистент по заполнению документов.
У тебя есть ИСХОДНЫЕ ДАННЫЕ и список ЦЕЛЕВЫХ ФРАГМЕНТОВ шаблона, в которых
нужно заменить тег <Заполнить>.

ИСХОДНЫЕ ДАННЫЕ:
{source_context}

ЦЕЛЕВЫЕ ФРАГМЕНТЫ:
{chr(10).join(tasks_text_parts)}

ЗАДАЧА:
Для каждого ID найди в исходных данных подходящую по смыслу информацию для
замены тега <Заполнить>. Соблюдай падеж, число и лаконичность.
Если данных нет — используй "{FALLBACK_VALUE}".

ФОРМАТ ОТВЕТА (СТРОГО):
Каждое значение на отдельной строке: "ID: значение".
Если в одном фрагменте несколько тегов — несколько строк с одинаковым ID
в порядке следования тегов.
Никаких пояснений, приветствий, нумерации или markdown — только строки "ID: значение".

ПРИМЕР:
PARA_0: Иванов Иван Иванович
PARA_0: 15.01.2024
PARA_1: {FALLBACK_VALUE}"""

    response = request(prompt, model=llm_model, **llm_options)
    parsed = _parse_id_response(response, list(counts.keys()))

    filled = 0
    for i, target in enumerate(targets):
        pid = f"PARA_{i}"
        expected = counts[pid]
        values = parsed.get(pid, [])

        if len(values) < expected:
            logger.warning(f"{pid}: получено {len(values)}/{expected} значений, оставляем теги")
            values = values + ["<Заполнить>"] * (expected - len(values))
        elif len(values) > expected:
            values = values[:expected]

        filled_text = _replace_placeholders(target.text, values)
        target.docx_obj.clear()
        target.docx_obj.add_run(filled_text)

        if filled_text != target.text:
            filled += 1
            logger.info(f"{pid} заполнен: '{target.text[:50]}...'")
        else:
            logger.warning(f"{pid} не изменён (данные не найдены)")

    logger.info(f"Параграфы: заполнено {filled}/{len(targets)}")