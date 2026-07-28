# parser.py

from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
from typing import List, Dict, Any

def parse(file_path: str) -> List[Dict[str, Any]]:
    doc = Document(file_path)
    structure: List[Dict[str, Any]] = []
    
    for item in doc.element.body:
        if item.tag.endswith("p"):
            paragraph = Paragraph(item, doc)
            text = paragraph.text.strip()
            if text:
                structure.append({
                    "type": "paragraph",
                    "text": text
                })

        elif item.tag.endswith("tbl"):
            table = Table(item, doc)
            table_data = []
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                table_data.append(row_data)
            
            structure.append({
                "type": "table",
                "data": table_data
            })

    return structure