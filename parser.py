"""
Парсинг .docx в список блоков (параграфы и таблицы).

Если with_refs=True, каждый блок хранит ссылку на исходный объект
python-docx (Paragraph / Table), что позволяет потом писать результат
заполнения напрямую в документ без повторного поиска "какой это индекс
в doc.tables/doc.paragraphs" — источник багов в исходной версии.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Union

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

EMPTY_MARKERS = {"", "<>", "•", "<Заполнить>", "-"}


def is_empty_cell(value: str) -> bool:
    stripped = value.strip()
    return not stripped or stripped in EMPTY_MARKERS


@dataclass
class ParagraphBlock:
    text: str
    docx_obj: Optional[DocxParagraph] = None


@dataclass
class TableBlock:
    name: str
    data: List[List[str]]
    docx_obj: Optional[DocxTable] = None


Block = Union[ParagraphBlock, TableBlock]


def _guess_table_name(preceding: List[Block], table_index: int) -> str:
    """Ищет название таблицы в последних 1-2 параграфах перед ней."""
    for block in reversed(preceding[-2:]):
        if isinstance(block, ParagraphBlock) and "таблица" in block.text.lower():
            return block.text.strip()
    return f"Таблица {table_index}"


def parse(document: DocumentObject, with_refs: bool = False) -> List[Block]:
    blocks: List[Block] = []
    table_count = 0

    for item in document.element.body:
        if item.tag.endswith("}p"):
            para = DocxParagraph(item, document)
            text = para.text.strip()
            if text:
                blocks.append(ParagraphBlock(text=text, docx_obj=para if with_refs else None))

        elif item.tag.endswith("}tbl"):
            table = DocxTable(item, document)
            data = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            table_count += 1
            name = _guess_table_name(blocks, table_count)
            blocks.append(TableBlock(name=name, data=data, docx_obj=table if with_refs else None))

    return blocks


def table_signature(table: TableBlock) -> str:
    """Компактное текстовое представление таблицы для эмбеддинга: название + заголовки."""
    header_row = table.data[0] if table.data else []
    header_text = " | ".join(c.strip() for c in header_row if c.strip())
    return f"{table.name}. {header_text}".strip()


def tables_of(blocks: List[Block]) -> List[TableBlock]:
    return [b for b in blocks if isinstance(b, TableBlock)]


def paragraphs_of(blocks: List[Block]) -> List[ParagraphBlock]:
    return [b for b in blocks if isinstance(b, ParagraphBlock)]
