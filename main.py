# main.py

import yaml
import docx
from typing import List, Dict, Any
from helper import load_config
from parser import parse
from llm import request
import logging
from datetime import datetime

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

def table_to_structured_data(table_block: Dict) -> Dict:
    """Возвращает данные таблицы, считает число ПУСТЫХ ячеек"""
    data = table_block["data"]
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
    """Форматирует ВСЕ исходные таблицы — по строкам, как есть"""
    lines = ["ИСХОДНЫЕ ТАБЛИЦЫ:"]
    for tbl in source_tables:
        lines.append(f"– {tbl['name']}")
        structured = table_to_structured_data(tbl)
        lines.extend(structured["rows"])
    return "\n".join(lines)

def format_target_table(template_table: Dict) -> str:
    """Форматирует целевую таблицу — название, строки, число пустых"""
    structured = table_to_structured_data(template_table)
    lines = [
        f"Название: {template_table['name']}",
        "СТРОКИ:"
    ]
    lines.extend(structured["rows"])
    lines.append(f"ПУСТО: {structured['empty_count']}")
    return "\n".join(lines)

def fill_table_data(table_data: List[List[str]], values: List[str]) -> None:
    """Заполняет ПУСТЫЕ ячейки значениями. Обход: сверху вниз, слева направо."""
    value_index = 0
    empty_markers = {"", "<>", "•", "<Заполнить>", "-"}
    
    for row in table_data:
        for j in range(len(row)):
            stripped = row[j].strip()
            if not stripped or stripped in empty_markers:
                if value_index < len(values):
                    # Прямое присваивание вместо replace
                    row[j] = values[value_index].strip()
                    value_index += 1
                else:
                    logger.warning("Недостаточно значений для заполнения таблицы")
                    return
                    
    if value_index < len(values):
        logger.warning(f"Избыток значений: {len(values) - value_index} не использовано")


#Параграфы
def extract_paragraphs(blocks: List[Dict]) -> List[str]:
    """Извлекает все непустые параграфы из блоков (без таблиц)"""
    paragraphs = []
    for block in blocks:
        if block["type"] == "paragraph":
            text = block["text"].strip()
            if text:
                paragraphs.append(text)
    return paragraphs

def replace_placeholders(text: str, replacements: List[str]) -> str:
    """Заменяет каждое вхождение <Заполнить> в тексте на значение из списка по порядку"""
    parts = text.split("<Заполнить>")
    if len(replacements) == 0:
        return text

    result = parts[0]
    for i, part in enumerate(parts[1:], start=1):
        value = replacements[i-1] if i-1 < len(replacements) else "<Заполнить>"
        result += value + part
    return result

def process_paragraph_with_llm(paragraph_text: str, source_paragraphs: List[str], model_name: str) -> str:
    """
    Находит все <Заполнить> в параграфе, запрашивает значения у LLM, возвращает заполненный текст.
    """
    placeholders = paragraph_text.count("<Заполнить>")
    if placeholders == 0:
        return paragraph_text

    source_context = "\n".join(f"• {p}" for p in source_paragraphs)

    prompt = f"""
Просмотри следующий фрагмент из шаблона:
{paragraph_text}

Найди в следующих данных информацию, которая по контексту и смыслу подходит шаблону и замени на неё тег <Заполнить>.
Если таких тегов несколько, то напиши ответы через запятую. 
{source_context}

Отвечай без пояснений, ничего лишнего кроме фразы, которую необходимо подставить в шаблоне вместо <Заполнить>. Соблюдай падеж, склонение и смотри чтобы лаконично вписывалось в предложение.
"""

    try:
        response = request(prompt, model_name=model_name)
        values = [line.strip() for line in response.strip().split("\n") if line.strip()]
        if len(values) == 1 and "," in values[0]:
            values = [v.strip() for v in values[0].split(",")]
        # Ограничиваем число значений числу тегов
        values = values[:placeholders]
        return replace_placeholders(paragraph_text, values)
    except Exception as e:
        logger.error(f"Ошибка при обработке параграфа '{paragraph_text}': {str(e)}")
        return replace_placeholders(paragraph_text, ["<Заполнить>"] * placeholders)

def main():
    source_path = load_config("source_path")
    template_path = load_config("template_path")
    output_path = load_config("output_path")
    model_name = load_config("model")

    # Парсим оба документа
    source_blocks = parse(source_path)
    template_blocks = parse(template_path)

    # Извлекаем таблицы с именами
    def extract_tables(blocks):
        tables = []
        for i, block in enumerate(blocks):
            if block["type"] == "table":
                name = f"Таблица {len(tables) + 1}" # Резервное имя
                # Ищем название только среди 2 предыдущих блоков, чтобы не зацепить чужое
                for j in range(max(0, i - 2), i):
                    prev = blocks[j]
                    if prev["type"] == "paragraph" and "таблица" in prev["text"].lower():
                        name = prev["text"].strip()
                        break
                tables.append({"name": name, "data": block["data"]})
        return tables

    source_tables = extract_tables(source_blocks)
    template_tables = extract_tables(template_blocks)

    # Копируем шаблон для редактирования
    doc = docx.Document(template_path)
    output_tables = doc.tables  # Соответствуют порядку в документе
    table_index = 0  # Индекс таблицы в output

    # Обрабатываем каждую таблицу в шаблоне
    for template_table in template_tables:
        logger.info(f"Обработка таблицы: {template_table['name']}")

        # Форматируем контекст
        target_text = format_target_table(template_table)
        source_text = format_source_tables(source_tables)

        prompt = f"""
Просмотри следующую информацию о таблице:

{target_text}

Найди в следующих данных таблицу, которая семантически и контекстно соответствует исходной, а после перечисли все эти данные по порядку, обязательно через запятую и пробел (, ).
Перечисляемые данные должны быть строго те, которые указаны в ячейке. Если данных нет, то ставь "Нет данных".

{source_text}

Данных должно получиться столько же, сколько написано напротив ПУСТО (Не больше, не меньше). Пояснений и комментариев не добавляй. Обязательно обращай внимание чтобы данные были логичны (например, если просят температуру, то надо написать число)
"""

        try:
            logger.debug(f"Запрос к LLM сформирован для таблицы: {template_table['name']}")
            response = request(prompt, model_name=model_name)
            response_clean = response.strip()
            logger.info(f"LLM ОТВЕТ для '{template_table['name']}':")
            logger.info(f"→ {response_clean}")

            # Парсим ответ
            if response_clean == "-" or not response_clean:
                values = []
            else:
                values = [v.strip() for v in response_clean.split(", ")]

            # Получаем ожидаемое число пустых ячеек
            empty_count = table_to_structured_data(template_table)["empty_count"]
            if len(values) != empty_count:
                logger.warning(f"Количество значений ({len(values)}) ≠ ожидаемому ({empty_count})")

            # Заполняем таблицу в документе
            if table_index < len(output_tables):
                docx_table = output_tables[table_index]
                
                # Заполняем по порядку в template_table["data"]
                fill_table_data(template_table["data"], values)

                # Теперь переносим данные обратно в docx-таблицу
                for i, row in enumerate(template_table["data"]):
                    if i >= len(docx_table.rows):
                        continue
                    docx_row = docx_table.rows[i]
                    for j, cell_value in enumerate(row):
                        if j >= len(docx_row.cells):
                            continue
                        docx_cell = docx_row.cells[j]
                        
                        # Очищаем и устанавливаем новое значение 
                        # (используем только первый параграф ячейки, чтобы избежать дублирования текста)
                        docx_cell.paragraphs[0].clear()
                        docx_cell.paragraphs[0].add_run(str(cell_value))

            else:
                logger.error(f"Не хватает таблиц в выходном документе для: {template_table['name']}")

            table_index += 1

        except Exception as e:
            logger.error(f"Ошибка при обработке таблицы '{template_table['name']}': {str(e)}")

    logger.info("Начинаем обработку параграфов с <Заполнить>")

    # Извлекаем исходные параграфы для контекста
    source_paragraphs = extract_paragraphs(source_blocks)

    # Проходим по всем параграфам в документе и заменяем <Заполнить>
    for p in doc.paragraphs:
        if "<Заполнить>" in p.text:
            original_text = p.text
            filled_text = process_paragraph_with_llm(original_text, source_paragraphs, model_name)
            p.clear()
            p.add_run(filled_text)
            logger.info(f"Заменено: '{original_text}' → '{filled_text}'")

    # Сохраняем результат
    doc.save(output_path)
    logger.info(f"Документ сохранён: {output_path}")

    logger.info("Обработка завершена.")

if __name__ == "__main__":
    main()