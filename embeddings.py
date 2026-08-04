"""
Семантический поиск через embeddings-модель Ollama (например, bge-m3).

Используется отдельно от генеративной LLM: эмбеддинги нужны только чтобы
быстро и детерминированно найти релевантный кусок исходника под конкретную
таблицу/параграф шаблона — сама генерация значений остаётся за LLM.

Перед первым запуском модель нужно скачать:
    ollama pull bge-m3
"""

import logging
from typing import List

import numpy as np
import ollama

logger = logging.getLogger(__name__)


def embed_texts(texts: List[str], model: str) -> np.ndarray:
    """Возвращает L2-нормализованную матрицу эмбеддингов (N x D)."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    response = ollama.embed(model=model, input=texts)
    vectors = np.array(response["embeddings"], dtype=np.float32)

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def most_similar(query_vector: np.ndarray, candidates: np.ndarray, top_k: int = 5) -> List[int]:
    """Индексы top_k наиболее похожих кандидатов (по убыванию cosine similarity)."""
    if candidates.shape[0] == 0:
        return []
    similarities = candidates @ query_vector
    k = min(top_k, len(similarities))
    return list(np.argsort(similarities)[::-1][:k])
