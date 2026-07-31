import logging
from typing import List, Dict
import docx

from helper import load_config
from parser import parse
from llm import request

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("generation.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def table_to_compact_text(table_block: Dict, table_id: str) -> str:
    """
    Преобразует таблицу в компактный текстовый формат для LLM.
    Показывает только непустые ячейки и помечает пустые как [ПУСТО].
    """
    data = table_block.get("data", [])
    if not data:
        return f"[{table_id}] Название: {table_block['name']}\n(пустая таблица)\n"
    
    lines = [f"[{table_id}] Название: {table_block['name']}"]
    lines.append("СТРУКТУРА:")
    
    for i, row in enumerate(data):
        row_parts = []
        for j, cell in enumerate(row):
            stripped = cell.strip()
            if not stripped or stripped in ["", "<>", "•", "<Заполнить>", "-"]:
                row_parts.append(f"[ПУСТО]")
            else:
                row_parts.append(stripped)
        lines.append(f"  Строка {i+1}: {' | '.join(row_parts)}")
    
    # Считаем пустые ячейки
    empty_count = sum(1 for row in data for cell in row if not cell.strip() or cell.strip() in ["", "<>", "•", "<Заполнить>", "-"])
    lines.append(f"ТРЕБУЕТСЯ ЗАПОЛНИТЬ ЯЧЕЕК: {empty_count}")
    lines.append("")
    
    return "\n".join(lines)

def format_source_tables_compact(source_tables: List[Dict]) -> str:
    """Форматирует исходные таблицы в компактном виде"""
    lines = ["=== ИСХОДНЫЕ ДАННЫЕ (ТАБЛИЦЫ) ==="]
    for i, tbl in enumerate(source_tables):
        lines.append(table_to_compact_text(tbl, f"SRC_{i}"))
    return "\n".join(lines)

def parse_tables_llm_response(response_text: str, target_tables: List[Dict]) -> Dict[str, List[str]]:
    """
    Парсит текстовый ответ LLM для таблиц.
    Формат ответа:
        TBL_0: значение1
        TBL_0: значение2
        TBL_1: значение3
    
    Возвращает словарь {ID: [список значений в порядке заполнения]}.
    """
    result: Dict[str, List[str]] = {f"TBL_{i}": [] for i in range(len(target_tables))}
    
    # Сортируем ID по длине (убывание), чтобы "TBL_10" не матчился как "TBL_1" + "0"
    sorted_ids = sorted(result.keys(), key=len, reverse=True)
    
    for line in response_text.splitlines():
        line = line.strip()
        if not line:
            continue
        
        # Ищем строку вида "TBL_X: ..."
        for tid in sorted_ids:
            prefix = f"{tid}:"
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                # Отбрасываем возможные кавычки по краям
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if value:
                    result[tid].append(value)
                break
                
    return result

def fill_table_data_batch(table_data: List[List[str]], values: List[str]) -> None:
    """
    Заполняет ПУСТЫЕ ячейки значениями.
    Обход: по строкам сверху вниз, слева направо.
    """
    value_index = 0
    empty_markers = {"", "<>", "•", "<Заполнить>", "-"}
    
    for row in table_data:
        for j in range(len(row)):
            stripped = row[j].strip()
            if not stripped or stripped in empty_markers:
                if value_index < len(values):
                    row[j] = values[value_index].strip()
                    value_index += 1
                else:
                    logger.warning("Недостаточно значений для заполнения таблицы")
                    return
                    
    if value_index < len(values):
        logger.warning(f"Избыток значений: {len(values) - value_index} не использовано")

def extract_tables(blocks: List[Dict]) -> List[Dict]:
    """Извлекает таблицы и пытается найти их название в предыдущих параграфах"""
    tables = []
    for i, block in enumerate(blocks):
        if block["type"] == "table":
            name = f"Таблица {len(tables) + 1}"
            for j in range(max(0, i - 2), i):
                prev = blocks[j]
                if prev["type"] == "paragraph" and "таблица" in prev["text"].lower():
                    name = prev["text"].strip()
                    break
            tables.append({"name": name, "data": block["data"]})
    return tables

def process_all_tables_batch(doc, source_blocks: List[Dict], template_blocks: List[Dict], model_name: str) -> None:
    """
    Находит все таблицы в шаблоне, формирует ОДИН запрос к LLM,
    парсит ответ и заполняет все таблицы.
    """
    # 1. Извлекаем таблицы
    source_tables = extract_tables(source_blocks)
    template_tables = extract_tables(template_blocks)
    
    if not template_tables:
        logger.info("Таблицы в шаблоне не найдены. Пропуск.")
        return
    
    logger.info(f"Найдено {len(template_tables)} таблиц для обработки. Формируем единый запрос к LLM...")
    
    # 2. Формируем компактное представление целевых таблиц
    target_tables_text = "=== ЦЕЛЕВЫЕ ТАБЛИЦЫ (ШАБЛОН) ===\n"
    for i, tbl in enumerate(template_tables):
        target_tables_text += table_to_compact_text(tbl, f"TBL_{i}")
    
    # 3. Формируем компактное представление исходных таблиц
    source_tables_text = format_source_tables_compact(source_tables)
    
    # 4. Считаем ожидаемое количество значений для каждой таблицы
    expected_counts = {}
    for i, tbl in enumerate(template_tables):
        empty_count = sum(1 for row in tbl["data"] for cell in row if not cell.strip() or cell.strip() in ["", "<>", "•", "<Заполнить>", "-"])
        expected_counts[f"TBL_{i}"] = empty_count
    
    # 5. Составляем промпт
    prompt = f"""
Ты — ассистент по заполнению документов.
У тебя есть ИСХОДНЫЕ ТАБЛИЦЫ с данными и ЦЕЛЕВЫЕ ТАБЛИЦЫ из шаблона, которые нужно заполнить.

{source_tables_text}

{target_tables_text}

ЗАДАЧА:
Для каждой целевой таблицы (TBL_0, TBL_1, ...) найди в исходных данных таблицу, которая семантически и контекстно соответствует.
Затем перечисли все значения для заполнения пустых ячеек [ПУСТО] в порядке обхода: сверху вниз, слева направо.
Если данных нет, используй "Нет данных".

ФОРМАТ ОТВЕТА (СТРОГО СОБЛЮДАЙ):
Выводи каждое значение на отдельной строке в формате:
TBL_X: значение

Если в одной таблице несколько пустых ячеек, выведи несколько строк с одинаковым ID в порядке заполнения.
НЕ добавляй никаких пояснений, приветствий, нумерации или markdown-обёрток. Только строки формата "TBL_X: значение".

ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА:
TBL_0: 15.5
TBL_0: 23.1
TBL_0: Нет данных
TBL_1: Иванов И.И.
TBL_1: 01.01.2024
"""

    # 6. Делаем ОДИН запрос к LLM
    try:
        response = request(prompt, model_name=model_name)
        logger.debug(f"Сырой ответ LLM для таблиц:\n{response}")
        
        # 7. Парсинг ответа
        parsed_data = parse_tables_llm_response(response, template_tables)
        
        # 8. Применяем данные к таблицам в документе
        output_tables = doc.tables
        success_count = 0
        
        for i, template_table in enumerate(template_tables):
            tid = f"TBL_{i}"
            values = parsed_data.get(tid, [])
            expected_count = expected_counts[tid]
            
            logger.info(f"Таблица {tid} ('{template_table['name']}'): получено {len(values)} значений, ожидалось {expected_count}")
            
            # Корректируем количество значений
            if len(values) < expected_count:
                values.extend(["Нет данных"] * (expected_count - len(values)))
                logger.warning(f"LLM вернула меньше значений для {tid}. Дополнено 'Нет данных'.")
            elif len(values) > expected_count:
                values = values[:expected_count]
                logger.warning(f"LLM вернула больше значений для {tid}. Лишние отброшены.")
            
            # Заполняем данные в template_table
            fill_table_data_batch(template_table["data"], values)
            
            # Переносим в docx-таблицу
            if i < len(output_tables):
                docx_table = output_tables[i]
                for row_idx, row in enumerate(template_table["data"]):
                    if row_idx >= len(docx_table.rows):
                        continue
                    docx_row = docx_table.rows[row_idx]
                    for col_idx, cell_value in enumerate(row):
                        if col_idx >= len(docx_row.cells):
                            continue
                        docx_cell = docx_row.cells[col_idx]
                        docx_cell.paragraphs[0].clear()
                        docx_cell.paragraphs[0].add_run(str(cell_value))
                
                success_count += 1
                logger.info(f"Таблица {tid} заполнена")
            else:
                logger.error(f"Не хватает таблиц в выходном документе для {tid}")
        
        logger.info(f"Обработка таблиц завершена. Успешно заполнено: {success_count}/{len(template_tables)}")
        
    except Exception as e:
        logger.error(f"Ошибка при пакетной обработке таблиц: {str(e)}")
        logger.warning("Таблицы останутся незаполненными.")

def replace_placeholders(text: str, replacements: List[str]) -> str:
    """Заменяет каждое вхождение <Заполнить> в тексте на значение из списка по порядку"""
    parts = text.split("<Заполнить>")
    if not replacements:
        return text

    result = parts[0]
    for i, part in enumerate(parts[1:], start=1):
        value = replacements[i-1] if (i-1) < len(replacements) else "<Заполнить>"
        result += value + part
    return result

def parse_llm_response(response_text: str, targets: Dict) -> Dict[str, List[str]]:
    """
    Парсит текстовый ответ LLM формата:
        PARA_0: значение1
        PARA_0: значение2
        PARA_1: значение3
    """
    result: Dict[str, List[str]] = {pid: [] for pid in targets}
    sorted_ids = sorted(targets.keys(), key=len, reverse=True)
    
    for line in response_text.splitlines():
        line = line.strip()
        if not line:
            continue
        
        for pid in sorted_ids:
            prefix = f"{pid}:"
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if value:
                    result[pid].append(value)
                break
                
    return result

def process_all_paragraphs_batch(doc, source_blocks: List[Dict], model_name: str) -> None:
    """
    Находит все параграфы с <Заполнить>, формирует ОДИН запрос к LLM,
    парсит текстовый ответ и применяет замены напрямую к объектам docx.Paragraph.
    """
    targets = {}
    para_id = 0
    for p in doc.paragraphs:
        if "<Заполнить>" in p.text:
            targets[f"PARA_{para_id}"] = {
                "text": p.text,
                "count": p.text.count("<Заполнить>"),
                "docx_obj": p
            }
            para_id += 1

    if not targets:
        logger.info("Теги <Заполнить> в параграфах не найдены. Пропуск.")
        return

    logger.info(f"Найдено {len(targets)} параграфов для обработки. Формируем единый запрос к LLM...")

    source_paragraphs = [
        block["text"].strip() 
        for block in source_blocks 
        if block["type"] == "paragraph" and block["text"].strip()
    ]
    source_context = "\n".join(f"• {p}" for p in source_paragraphs)

    tasks_text = ""
    for pid, data in targets.items():
        tasks_text += f"[{pid}] (требуется {data['count']} значений): {data['text']}\n"

    prompt = f"""
Ты — ассистент по заполнению документов. 
У тебя есть ИСХОДНЫЕ ДАННЫЕ и список ЦЕЛЕВЫХ ФРАГМЕНТОВ шаблона, в которых нужно заменить тег <Заполнить>.

ИСХОДНЫЕ ДАННЫЕ:
{source_context}

ЦЕЛЕВЫЕ ФРАГМЕНТЫ:
{tasks_text}

ЗАДАЧА:
Для каждого ID найди в исходных данных подходящую по смыслу информацию для замены тега <Заполнить>.
Соблюдай падеж, число и лаконичность. Если данных нет, используй фразу "Нет данных".

ФОРМАТ ОТВЕТА (СТРОГО СОБЛЮДАЙ):
Выводи каждое значение на отдельной строке в формате:
ID: значение

Если в одном фрагменте несколько тегов <Заполнить>, выведи несколько строк с одинаковым ID в порядке следования тегов.
НЕ добавляй никаких пояснений, приветствий, нумерации или markdown-обёрток. Только строки формата "ID: значение".

ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА:
PARA_0: Иванов Иван Иванович
PARA_0: 15.01.2024
PARA_1: Нет данных
"""

    try:
        response = request(prompt, model_name=model_name)
        logger.debug(f"Сырой ответ LLM:\n{response}")
        
        parsed_data = parse_llm_response(response, targets)

        success_count = 0
        for pid, data in targets.items():
            values = parsed_data.get(pid, [])
            expected_count = data["count"]
            
            if len(values) < expected_count:
                values.extend(["<Заполнить>"] * (expected_count - len(values)))
                logger.warning(f"LLM вернула меньше значений для {pid} ({len(values)}/{expected_count}). Оставлены теги.")
            elif len(values) > expected_count:
                values = values[:expected_count]
                logger.warning(f"LLM вернула больше значений для {pid}. Лишние отброшены.")

            original_text = data["text"]
            filled_text = replace_placeholders(original_text, values)
            
            docx_p = data["docx_obj"]
            docx_p.clear()
            docx_p.add_run(filled_text)
            
            if original_text != filled_text:
                logger.info(f"Заполнено {pid}: '{original_text}' → '{filled_text}'")
                success_count += 1
            else:
                logger.warning(f"{pid} не изменён (значения не найдены)")

        logger.info(f"Обработка параграфов завершена. Успешно заполнено: {success_count}/{len(targets)}")

    except Exception as e:
        logger.error(f"Ошибка при пакетной обработке параграфов: {str(e)}")
        logger.warning("Параграфы останутся с тегами <Заполнить>.")

def main():
    try:
        source_path = load_config("source_path")
        template_path = load_config("template_path")
        output_path = load_config("output_path")
        model_name = load_config("model")
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        return

    logger.info(f"Начало обработки. Модель: {model_name}")

    source_blocks = parse(source_path)
    template_blocks = parse(template_path)

    doc = docx.Document(template_path)

    # ПАКЕТНАЯ обработка всех таблиц
    logger.info("Начинаем пакетную обработку всех таблиц")
    process_all_tables_batch(doc, source_blocks, template_blocks, model_name)

    # ПАКЕТНАЯ обработка параграфов с <Заполнить>
    logger.info("Начинаем пакетную обработку параграфов с <Заполнить>")
    process_all_paragraphs_batch(doc, source_blocks, model_name)

    doc.save(output_path)
    logger.info(f"Документ успешно сохранён: {output_path}")
    logger.info("Обработка завершена.")

if __name__ == "__main__":
    main()