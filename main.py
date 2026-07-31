import logging
import re
from typing import List, Dict, Any
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

# ==============================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ТАБЛИЦАМИ (без изменений)
# ==============================================================================

def table_to_structured_data(table_block: Dict) -> Dict:
    """Возвращает данные таблицы, считает число ПУСТЫХ ячеек (учитывает маркеры)"""
    data = table_block.get("data", [])
    if not data:
        return {"rows": [], "empty_count": 0}

    empty_count = 0
    rows = []
    empty_markers = {"", "<>", "•", "<Заполнить>", "-"}
    
    for i, row in enumerate(data):
        formatted_cells = []
        for cell in row:
            stripped = cell.strip()
            formatted_cells.append(stripped)
            if not stripped or stripped in empty_markers:
                empty_count += 1
        rows.append(f"  Строка {i+1}: {', '.join(formatted_cells)}")

    return {"rows": rows, "empty_count": empty_count}

def format_source_tables(source_tables: List[Dict]) -> str:
    lines = ["ИСХОДНЫЕ ТАБЛИЦЫ:"]
    for tbl in source_tables:
        lines.append(f"– {tbl['name']}")
        structured = table_to_structured_data(tbl)
        lines.extend(structured["rows"])
    return "\n".join(lines)

def format_target_table(template_table: Dict) -> str:
    structured = table_to_structured_data(template_table)
    lines = [
        f"Название: {template_table['name']}",
        "СТРОКИ:"
    ]
    lines.extend(structured["rows"])
    lines.append(f"ПУСТО: {structured['empty_count']}")
    return "\n".join(lines)

def fill_table_data(table_data: List[List[str]], values: List[str]) -> None:
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

# ==============================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ПАРАГРАФАМИ (ТЕКСТОВЫЙ ФОРМАТ ВМЕСТО JSON)
# ==============================================================================

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
    
    Возвращает словарь {ID: [список значений в порядке появления]}.
    """
    result: Dict[str, List[str]] = {pid: [] for pid in targets}
    
    # Сортируем ID по длине (убывание), чтобы "PARA_10" не матчился как "PARA_1" + "0"
    sorted_ids = sorted(targets.keys(), key=len, reverse=True)
    
    for line in response_text.splitlines():
        line = line.strip()
        if not line:
            continue
        
        # Ищем строку вида "PARA_X: ..."
        for pid in sorted_ids:
            prefix = f"{pid}:"
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                # Отбрасываем возможные кавычки по краям (LLM иногда их ставит)
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
    # 1. Собираем все целевые параграфы с уникальными ID
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

    # 2. Формируем контекст из исходного документа
    source_paragraphs = [
        block["text"].strip() 
        for block in source_blocks 
        if block["type"] == "paragraph" and block["text"].strip()
    ]
    source_context = "\n".join(f"• {p}" for p in source_paragraphs)

    # 3. Формируем список задач для LLM
    tasks_text = ""
    for pid, data in targets.items():
        tasks_text += f"[{pid}] (требуется {data['count']} значений): {data['text']}\n"

    # 4. Составляем промпт с инструкцией по текстовому формату
    prompt = f"""
Ты — ассистент по заполнению документов. 
У тебя есть ИСХОДНЫЕ ДАННЫЕ и список ЦЕЛЕВЫХ ФРАГМЕНТОВ шаблона, в которых нужно заменить тег <Заполнить>.

ИСХОДНЫЕ ДАННЫЕ:
{source_context}

ЦЕЛЕВЫЕ ФРАГМЕНТЫ:
{tasks_text}

ЗАДАЧА:
Для каждого ID найди в исходных данных подходящую по смыслу информацию для замены тега <Заполнить>.
Соблюдай падеж, число и лаконичность.

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

    # 5. Делаем ОДИН запрос к LLM
    try:
        response = request(prompt, model_name=model_name)
        logger.debug(f"Сырой ответ LLM:\n{response}")
        
        # 6. Парсинг текстового ответа
        parsed_data = parse_llm_response(response, targets)

        # 7. Применяем замены к документу
        success_count = 0
        for pid, data in targets.items():
            values = parsed_data.get(pid, [])
            expected_count = data["count"]
            
            # Корректируем количество значений
            if len(values) < expected_count:
                values.extend(["<Заполнить>"] * (expected_count - len(values)))
                logger.warning(f"LLM вернула меньше значений для {pid} ({len(values)}/{expected_count}). Оставлены теги.")
            elif len(values) > expected_count:
                values = values[:expected_count]
                logger.warning(f"LLM вернула больше значений для {pid}. Лишние отброшены.")

            # Заменяем текст
            original_text = data["text"]
            filled_text = replace_placeholders(original_text, values)
            
            # Обновляем docx объект
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

    source_tables = extract_tables(source_blocks)
    template_tables = extract_tables(template_blocks)

    doc = docx.Document(template_path)
    output_tables = doc.tables
    table_index = 0

    for template_table in template_tables:
        logger.info(f"Обработка таблицы: {template_table['name']}")

        target_text = format_target_table(template_table)
        source_text = format_source_tables(source_tables)

        prompt = f"""
Просмотри следующую информацию о таблице:

{target_text}

Найди в следующих данных таблицу, которая семантически и контекстно соответствует исходной, а после перечисли все эти данные по порядку, обязательно через запятую и пробел (, ).
Перечисляемые данные должны быть строго те, которые указаны в ячейке. Если данных нет, то ставь "Нет данных".

{source_text}

Данных должно получиться столько же, сколько написано напротив ПУСТО (Не больше, не меньше). Пояснений и комментариев не добавляй.
"""

        try:
            response = request(prompt, model_name=model_name)
            response_clean = response.strip()
            logger.info(f"LLM ОТВЕТ для '{template_table['name']}': → {response_clean}")

            if not response_clean or response_clean.lower() in ["-", "нет данных", "нет"]:
                values = []
            else:
                values = [v.strip() for v in response_clean.split(", ") if v.strip()]

            empty_count = table_to_structured_data(template_table)["empty_count"]
            if len(values) != empty_count:
                logger.warning(f"Количество значений ({len(values)}) ≠ ожидаемому ({empty_count})")

            if table_index < len(output_tables):
                docx_table = output_tables[table_index]
                fill_table_data(template_table["data"], values)

                for i, row in enumerate(template_table["data"]):
                    if i >= len(docx_table.rows):
                        continue
                    docx_row = docx_table.rows[i]
                    for j, cell_value in enumerate(row):
                        if j >= len(docx_row.cells):
                            continue
                        docx_cell = docx_row.cells[j]
                        docx_cell.paragraphs[0].clear()
                        docx_cell.paragraphs[0].add_run(str(cell_value))
            else:
                logger.error(f"Не хватает таблиц в выходном документе для: {template_table['name']}")

            table_index += 1

        except Exception as e:
            logger.error(f"Ошибка при обработке таблицы '{template_table['name']}': {str(e)}")

    logger.info("Начинаем пакетную обработку параграфов с <Заполнить>")
    process_all_paragraphs_batch(doc, source_blocks, model_name)

    doc.save(output_path)
    logger.info(f"Документ успешно сохранён: {output_path}")
    logger.info("Обработка завершена.")

if __name__ == "__main__":
    main()