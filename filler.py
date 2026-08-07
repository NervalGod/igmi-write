"""
Заполнение шаблона данными из исходного документа.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from embeddings import embed_texts, most_similar
from llm import request
from parser import ParagraphBlock, TableBlock, is_empty_cell, table_signature

logger = logging.getLogger(__name__)

FALLBACK_VALUE = "?"

@dataclass
class TableExtractionResult:
    """Чистые данные для одной целевой таблицы"""
    table_index: int            # позиция в списке template_tables
    table_name: str             # логирование
    values: List[str]           # значения по порядку обхода пустых ячеек


@dataclass
class ParagraphExtractionResult:
    """Чистые данные для одного целевого параграфа — без всякой связи с docx."""
    para_index: int             # позиция в списке targets (параграфов с <Заполнить>)
    original_text: str          # логирование
    values: List[str]           # значения по порядку тегов <Заполнить>


def save_extraction(
    path: str,
    table_results: List[TableExtractionResult],
    paragraph_results: List[ParagraphExtractionResult],
) -> None:
    """Сохраняет результат извлечения в JSON - чтобы не гонять LLM заново при сбое на этапе записи в docx."""
    data = {
        "tables": [asdict(r) for r in table_results],
        "paragraphs": [asdict(r) for r in paragraph_results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"Результаты извлечения сохранены в кэш: {path}")


def load_extraction(path: str) -> Tuple[List[TableExtractionResult], List[ParagraphExtractionResult]]:
    """Загружает результат извлечения из JSON вместо повторного вызова LLM."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    table_results = [TableExtractionResult(**d) for d in data.get("tables", [])]
    paragraph_results = [ParagraphExtractionResult(**d) for d in data.get("paragraphs", [])]
    logger.info(
        f"Результаты извлечения загружены из кэша: {path} "
        f"({len(table_results)} таблиц, {len(paragraph_results)} параграфов)"
    )
    return table_results, paragraph_results

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
    порядка. Если эмбеддинги недоступны/пусты - возвращает всё как есть
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
    logger.debug(f"_select_relevant: ищу релевантные по {len(target_signatures)} целям из {len(source_items)} исходных")

    for target_i, tv in enumerate(target_vectors):
        top_indices = most_similar(tv, source_vectors, top_k=top_k_per_target)
        logger.debug(f"  Цель {target_i} '{target_signatures[target_i][:60]}...' -> источники {top_indices}")
        relevant_idx.update(top_indices)

    kept = sorted(relevant_idx)
    logger.info(f"Отбор контекста: выбрано {len(kept)}/{len(source_items)} исходных элементов (индексы {kept})")
    for idx in kept:
        if idx < len(source_signatures):
            logger.debug(f"  Выбран источник {idx}: '{source_signatures[idx][:60]}...'")

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

def _collapse_consecutive_duplicates(row: List[str], min_run: int = 3) -> List[str]:
    """
    Очистка данных заголовков таблиц для последующей подачи в промпт (для объединённых ячеек)
    Схлопывает повторы начиная с min_run
    """
    result = list(row)
    n = len(result)
    i = 0
    while i < n:
        val = result[i].strip()
        if not val:
            i += 1
            continue
        j = i
        while j + 1 < n and result[j + 1].strip() == val:
            j += 1
        run_len = j - i + 1
        if run_len >= min_run:
            for k in range(i + 1, j + 1):
                result[k] = ""
        i = j + 1
    return result


def _table_to_text(table: TableBlock, tid: str) -> str:
    """
    Markdown-таблица
    """
    lines = [f"**[{tid}] {table.name}**", ""]

    if table.data:
        col_count = max(len(r) for r in table.data)
        for row_num, row in enumerate(table.data):
            collapsed = _collapse_consecutive_duplicates(row)
            display = []
            for original, shown in zip(row, collapsed):
                if is_empty_cell(original):
                    display.append("[ПУСТО]")
                else:
                    display.append(shown.strip())
            display += [""] * (col_count - len(display))
            lines.append("| " + " | ".join(display) + " |")
            if row_num == 0:
                lines.append("|" + "---|" * col_count)

    return "\n".join(lines)


def extract_table_values(
    source_tables: List[TableBlock],
    template_tables: List[TableBlock],
    embed_model: str,
    llm_model: str,
    llm_options: dict,
    top_k_per_target: int = 3,
) -> List[TableExtractionResult]:
    """
    Первая часть алгоритма 
    Возвращает ответ llm
    """
    results: List[TableExtractionResult] = []

    if not template_tables:
        logger.info("Таблицы в шаблоне не найдены, пропуск.")
        return results
    if not source_tables:
        logger.warning("В исходнике нет таблиц - заполнять нечем.")
        return results

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

    if not required_ids:
        logger.info("Все таблицы шаблона уже заполнены, LLM не вызываем.")
        return results

    prompt = f"""Ты - ассистент по заполнению документов.
У тебя есть ИСХОДНЫЕ ТАБЛИЦЫ с данными и ЦЕЛЕВЫЕ ТАБЛИЦЫ из шаблона, которые нужно заполнить.

=== ИСХОДНЫЕ ТАБЛИЦЫ ===
{chr(10).join(source_blocks)}

=== ЦЕЛЕВЫЕ ТАБЛИЦЫ (ШАБЛОН) ===
{chr(10).join(target_blocks)}

ЗАДАЧА:
Для каждой целевой таблицы (TBL_0, TBL_1, ...) найди среди исходных таблиц ту,
которая семантически соответствует (названия и заголовки могут не совпадать
дословно - ориентируйся на смысл). Перечисли значения для заполнения пустых
ячеек [ПУСТО] в порядке обхода: сверху вниз, слева направо.

ОБЯЗАТЕЛЬНО выведи хотя бы одну строку для КАЖДОГО из следующих ID, без
исключений: {required_ids}.
Если для какой-то таблицы не нашлось подходящей исходной — всё равно выведи
для неё строки со значением "{FALLBACK_VALUE}" (по одной на каждую пустую
ячейку), а не пропускай этот ID молча.

ФОРМАТ ОТВЕТА (СТРОГО):
Каждое значение на отдельной строке: "TBL_X: значение".
Если пустых ячеек несколько - несколько строк с одинаковым ID по порядку.
Никаких пояснений, приветствий, нумерации или markdown - только строки "TBL_X: значение".

ПРИМЕР:
TBL_0: 15.5
TBL_0: 23.1
TBL_1: Иванов И.И.
TBL_2: {FALLBACK_VALUE}"""

    try:
        response = request(prompt, model=llm_model, **llm_options)
    except Exception:
        logger.exception("Запрос к LLM по таблицам завершился ошибкой - извлечение не выполнено")
        return results

    parsed = _parse_id_response(response, list(empty_counts.keys()))

    for i, template_table in enumerate(template_tables):
        tid = f"TBL_{i}"
        expected = empty_counts[tid]
        if expected == 0:
            continue

        values = parsed.get(tid, [])
        if len(values) < expected:
            logger.warning(
                f"{tid} ('{template_table.name}'): получено {len(values)}/{expected} значений. "
                f"Если 0 - смотрите generation.log (DEBUG) на сырой ответ модели."
            )
            values = values + [FALLBACK_VALUE] * (expected - len(values))
        elif len(values) > expected:
            values = values[:expected]

        results.append(TableExtractionResult(table_index=i, table_name=template_table.name, values=values))

    logger.info(f"Извлечение таблиц завершено: {len(results)}/{len(template_tables)} таблиц с данными")
    return results


def apply_table_extraction(template_tables: List[TableBlock], results: List[TableExtractionResult]) -> int:
    """
    Вторая часть алгоритма - парсинг в docx
    """
    filled = 0
    for result in results:
        if result.table_index >= len(template_tables):
            logger.warning(f"Индекс таблицы {result.table_index} вне диапазона (всего {len(template_tables)}), пропуск")
            continue

        table = template_tables[result.table_index]
        idx = 0
        docx_table = table.docx_obj
        for row_idx, row in enumerate(table.data):
            for col_idx, cell in enumerate(row):
                if not is_empty_cell(cell):
                    continue
                value = result.values[idx] if idx < len(result.values) else FALLBACK_VALUE
                idx += 1
                docx_cell = docx_table.rows[row_idx].cells[col_idx]
                docx_cell.paragraphs[0].clear()
                docx_cell.paragraphs[0].add_run(value)

        filled += 1
        logger.info(f"Таблица '{table.name}' заполнена: {len(result.values)} значений")

    return filled


def fill_tables(
    source_tables: List[TableBlock],
    template_tables: List[TableBlock],
    embed_model: str,
    llm_model: str,
    llm_options: dict,
    top_k_per_target: int = 3,
) -> None:
    """Тонкая обёртка: extract + apply одним вызовом, без промежуточного кэширования."""
    results = extract_table_values(
        source_tables, template_tables, embed_model, llm_model, llm_options, top_k_per_target
    )
    filled = apply_table_extraction(template_tables, results)
    logger.info(f"Таблицы: заполнено {filled}/{len(template_tables)}")

def _replace_placeholders(text: str, values: List[str]) -> str:
    parts = text.split("<Заполнить>")
    result = parts[0]
    for i, part in enumerate(parts[1:]):
        value = values[i] if i < len(values) else "<Заполнить>"
        result += value + part
    return result


def extract_paragraph_values(
    source_paragraphs: List[ParagraphBlock],
    targets: List[ParagraphBlock],
    embed_model: str,
    llm_model: str,
    llm_options: dict,
    top_k_per_target: int = 8,
) -> List[ParagraphExtractionResult]:
    """
    Первая часть алгоритма работы с параграфами
    Возвращает ответ llm
    """
    results: List[ParagraphExtractionResult] = []

    if not targets:
        logger.info("Теги <Заполнить> не найдены, пропуск.")
        return results
    if not source_paragraphs:
        logger.warning("В исходнике нет параграфов - заполнять нечем.")
        return results

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

    prompt = f"""Ты - ассистент по заполнению документов.
У тебя есть ИСХОДНЫЕ ДАННЫЕ и список ЦЕЛЕВЫХ ФРАГМЕНТОВ шаблона, в которых
нужно заменить тег <Заполнить>.

ИСХОДНЫЕ ДАННЫЕ:
{source_context}

ЦЕЛЕВЫЕ ФРАГМЕНТЫ:
{chr(10).join(tasks_text_parts)}

ЗАДАЧА:
Для каждого ID найди в исходных данных подходящую по смыслу информацию для
замены тега <Заполнить>. Соблюдай падеж, число и лаконичность.
Если данных нет - используй "{FALLBACK_VALUE}".

ФОРМАТ ОТВЕТА (СТРОГО):
Каждое значение на отдельной строке: "ID: значение".
Если в одном фрагменте несколько тегов - несколько строк с одинаковым ID
в порядке следования тегов.
Никаких пояснений, приветствий, нумерации или markdown - только строки "ID: значение".

ПРИМЕР:
PARA_0: Иванов Иван Иванович
PARA_0: 15.01.2024
PARA_1: {FALLBACK_VALUE}"""

    try:
        response = request(prompt, model=llm_model, **llm_options)
    except Exception:
        logger.exception("Запрос к LLM по параграфам завершился ошибкой - извлечение не выполнено")
        return results

    parsed = _parse_id_response(response, list(counts.keys()))

    for i, target in enumerate(targets):
        pid = f"PARA_{i}"
        expected = counts[pid]
        values = parsed.get(pid, [])

        if len(values) < expected:
            logger.warning(f"{pid}: получено {len(values)}/{expected} значений, оставляем теги")
            values = values + ["<Заполнить>"] * (expected - len(values))
        elif len(values) > expected:
            values = values[:expected]

        results.append(ParagraphExtractionResult(para_index=i, original_text=target.text, values=values))

    logger.info(f"Извлечение параграфов завершено: {len(results)}/{len(targets)}")
    return results


def apply_paragraph_extraction(targets: List[ParagraphBlock], results: List[ParagraphExtractionResult]) -> int:
    """
    Вторая часть работы алгоритма с параграфами - парсинг в docx
    """
    filled = 0
    for result in results:
        if result.para_index >= len(targets):
            logger.warning(f"Индекс параграфа {result.para_index} вне диапазона (всего {len(targets)}), пропуск")
            continue

        target = targets[result.para_index]
        filled_text = _replace_placeholders(target.text, result.values)
        target.docx_obj.clear()
        target.docx_obj.add_run(filled_text)

        if filled_text != target.text:
            filled += 1
            logger.info(f"Параграф заполнен: '{target.text[:50]}...'")
        else:
            logger.warning(f"Параграф не изменён (данные не найдены): '{target.text[:50]}...'")

    return filled


def fill_paragraphs(
    source_paragraphs: List[ParagraphBlock],
    template_paragraphs: List[ParagraphBlock],
    embed_model: str,
    llm_model: str,
    llm_options: dict,
    top_k_per_target: int = 8,
) -> None:
    """Тонкая обёртка: extract + apply одним вызовом, без промежуточного кэширования."""
    targets = [p for p in template_paragraphs if "<Заполнить>" in p.text]
    results = extract_paragraph_values(
        source_paragraphs, targets, embed_model, llm_model, llm_options, top_k_per_target
    )
    filled = apply_paragraph_extraction(targets, results)
    logger.info(f"Параграфы: заполнено {filled}/{len(targets)}")