"""
Парсинг .docx в список блоков (параграфы и таблицы).

Если with_refs=True, каждый блок хранит ссылку на исходный объект
python-docx (Paragraph / Table), что позволяет потом писать результат
заполнения напрямую в документ без повторного поиска "какой это индекс
в doc.tables/doc.paragraphs" — источник багов в исходной версии.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Union

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

logger = logging.getLogger(__name__)

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
    """Парсит документ и логирует время выполнения."""
    started = time.perf_counter()
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

    elapsed = time.perf_counter() - started
    
    para_count = len([b for b in blocks if isinstance(b, ParagraphBlock)])
    logger.info(
        f"Парсинг завершён за {elapsed:.2f} сек: "
        f"{para_count} параграфов, {table_count} таблиц, всего {len(blocks)} блоков (with_refs={with_refs})"
    )
    
    return blocks


def _is_likely_header(row: List[str], column_count: int) -> bool:
    """
    Эвристика: строка вероятно заголовок, если:
    - большинство ячеек короткие и непустые (как "I", "II", "Январь")
    - количество непустых ячеек примерно совпадает с числом столбцов
    (заголовок обычно полный, данные часто разреженные).
    """
    if not row:
        return False
    non_empty = [c for c in row if c.strip()]
    if len(non_empty) < column_count * 0.7:  # меньше 70% заполнено — вероятно не заголовок
        return False
    avg_len = sum(len(c) for c in non_empty) / len(non_empty) if non_empty else 0
    return avg_len < 20  # средняя длина ячейки < 20 символов — похоже на заголовок


def _find_header_row(data: List[List[str]]) -> int:
    """Ищет индекс заголовочной строки. Возвращает 0, если не найдена."""
    if not data or len(data) < 2:
        return 0
    
    column_count = len(data[0]) if data else 0
    for i, row in enumerate(data[:min(3, len(data))]):  # проверяем первые 3 строки
        if _is_likely_header(row, column_count):
            logger.debug(f"Заголовок найден на строке {i}: {row[:5]}...")
            return i
    
    logger.debug(f"Заголовок не найден, используется первая строка")
    return 0


def table_signature(table: TableBlock) -> str:
    """
    Компактное текстовое представление таблицы для эмбеддинга:
    название + заголовки (ищет реальные заголовки, а не просто первую строку).
    """
    header_idx = _find_header_row(table.data)
    header_row = table.data[header_idx] if table.data else []
    header_text = " | ".join(c.strip() for c in header_row if c.strip())
    sig = f"{table.name}. {header_text}".strip()
    logger.debug(f"Сигнатура таблицы '{table.name}': {sig[:80]}...")
    return sig


def tables_of(blocks: List[Block]) -> List[TableBlock]:
    return [b for b in blocks if isinstance(b, TableBlock)]


def paragraphs_of(blocks: List[Block]) -> List[ParagraphBlock]:
    return [b for b in blocks if isinstance(b, ParagraphBlock)]