# main.py

from parser import parse
from llm import request
import json

def extract_fill_requests(structure: list) -> list:
    requests = []

    for idx, block in enumerate(structure):
        if block["type"] == "paragraph":
            text = block["text"]
            if "<Заполнить>" in text:
                requests.append({
                    "type": "paragraph",
                    "index": idx,
                    "context": text
                })

        elif block["type"] == "table":
            data = block["data"]
            for row_idx, row in enumerate(data):
                for col_idx, cell in enumerate(row):
                    if "<Заполнить>" in cell:
                        requests.append({
                            "type": "table",
                            "table_index": idx,
                            "row": row_idx,
                            "col": col_idx,
                            "context": cell
                        })

    return requests

def build_source_text(structure: list) -> str:
    parts = []
    for block in structure:
        if block["type"] == "paragraph":
            parts.append(block["text"])
        elif block["type"] == "table":
            for row in block["data"]:
                parts.append(" | ".join(row))
    return "\n".join(parts)

def main():
    source_structure = parse("test.docx")        # откуда брать данные
    template_structure = parse("test2.docx")     # куда вставлять

    source_text = build_source_text(source_structure)
    fill_requests = extract_fill_requests(template_structure)

    print("test.docx:")
    print(source_text)

    results = []

    for req in fill_requests:
        context = req["context"]

        prompt = f"""
Прочитай следующие данные:
"{source_text}"
Исходя из прочитанных данных, найди наиболее высокое совпадение по смыслу или контексту и замени <Заполнить> на эту фразу в следующем предложении:
"{context}"
Твой ответ должен содержать только фразу на заполнение, без каких-либо пояснений, причем соотвествовать нормам русского языка (правильное склонение, падеж и так далее) 
"""

        print(f"\nФраза на обработку: {context}")
        replacement = request(prompt)
        print(f"Ответ llm: {replacement}")

        results.append({**req, "replacement": replacement})

if __name__ == "__main__":
    main()