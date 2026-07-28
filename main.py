# main.py

from parser import parse
from llm import request
from helper import load_config


def table_to_columns(table_data):
    """Преобразует таблицу (список строк) в список столбцов"""
    if not table_data or not table_data[0]:
        return []
    num_cols = len(table_data[0])
    num_rows = len(table_data)
    columns = []
    for col_idx in range(num_cols):
        column = []
        for row_idx in range(num_rows):
            cell = table_data[row_idx][col_idx].strip() if col_idx < len(table_data[row_idx]) else ""
            column.append(cell)
        columns.append(column)
    return columns


def extract_tables_with_metadata(structure: list):
    """Извлекает таблицы с именами (из предшествующего параграфа 'Таблица ...')"""
    tables = []
    current_name = None

    for block in structure:
        if block["type"] == "paragraph":
            if "таблица" in block["text"].lower():
                current_name = block["text"].strip()

        elif block["type"] == "table" and block["data"]:
            tables.append({
                "name": current_name or "Без названия",
                "data": block["data"]
            })
            current_name = None  # сбрасываем после таблицы

    return tables


def format_table_for_prompt(table_dict: dict, include_data: bool = True) -> str:
    """Форматирует одну таблицу как набор столбцов"""
    lines = [f"📌 Таблица: {table_dict['name']}"]

    if not table_dict["data"]:
        return "\n".join(lines)

    columns = table_to_columns(table_dict["data"])

    for col_idx, col in enumerate(columns):
        lines.append(f"  Столбец {col_idx + 1}:")
        for row_idx, cell in enumerate(col):
            lines.append(f"    Строка {row_idx + 1}: {cell}")

    return "\n".join(lines)


def format_source_for_llm(source_tables: list) -> str:
    """Форматирует все таблицы источника как набор 'Таблица → Столбцы'"""
    return "\n\n".join(format_table_for_prompt(tbl) for tbl in source_tables)


def extract_columns_to_fill(template_tables: list):
    """Извлекает столбцы, содержащие <>, с полным контекстом"""
    columns_to_fill = []

    for tbl in template_tables:
        columns = table_to_columns(tbl["data"])
        for col_idx, col in enumerate(columns):
            if any("<>" in cell for cell in col):
                columns_to_fill.append({
                    "table_name": tbl["name"],
                    "column_index": col_idx,
                    "column": col  # вся колонка целиком
                })

    return columns_to_fill


def main():
    source_structure = parse(load_config('source_path'))
    template_structure = parse(load_config('template_path'))

    source_tables = extract_tables_with_metadata(source_structure)
    template_tables = extract_tables_with_metadata(template_structure)

    columns_to_fill = extract_columns_to_fill(template_tables)
    source_text = format_source_for_llm(source_tables)

    print("📚 ФОРМАТ ДЛЯ LLM (ИСТОЧНИК):")
    print(source_text)
    print("\n" + "="*80)

    results = []

    for col_req in columns_to_fill:
        table_name = col_req["table_name"]
        column = col_req["column"]
        col_idx = col_req["column_index"]

        # Форматируем целевой столбец
        column_lines = "\n".join(f"    Строка {i+1}: {cell}" for i, cell in enumerate(column))

        prompt = f"""
            Просмотри следующие данные таблиц:
            {source_text}
            
            Ты должен найти исходя из предоставленных данных таблицу, которая соответствует следующей таблице с названием: {table_name}
            и столбцом: 
            {column_lines}
            Опираясь на название, количество незаполненных строк столбца найди подходящие на место <> значения и напиши в ответ данные через запятую, которые надо заполнить 
            Никакие примечания, пояснения и прочего лишнего отвечать не нужно. Если нумерация таблиц не совпадает - это нормально."""

        print(f"\n🔍 Заполняем: таблица '{table_name}', столбец {col_idx+1}")
        print("Целевой столбец:")
        for i, cell in enumerate(column):
            print(f"  Строка {i+1}: {cell}")

        response = request(prompt)
        print(f"🎯 Ответ LLM:\n{response}")

        results.append({
            **col_req,
            "filled_column": response
        })

    print("\n" + "="*80)
    print("📋 РЕЗУЛЬТАТЫ:")
    for res in results:
        print(f"  Таблица: '{res['table_name']}', Столбец {res['column_index']+1}")
        print(f"  {res['filled_column']}\n")

if __name__ == "__main__":
    main()
    