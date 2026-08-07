"""
Семантический поиск через embeddings-модель Ollama.
"""

import logging
from typing import List

import numpy as np
import ollama

logger = logging.getLogger(__name__)


def embed_texts(texts: List[str], model: str) -> np.ndarray:
    """
    Возвращает L2-нормализованную матрицу эмбеддингов (N x D).
    Логирует в DEBUG: размерность, норму каждого вектора (для проверки качества).
    """
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    response = ollama.embed(model=model, input=texts)
    vectors = np.array(response["embeddings"], dtype=np.float32)

    # Нормализация L2
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = vectors / norms

    logger.debug(f"embed_texts: обработано {len(texts)} текстов, размерность вектора {vectors.shape[1]}")
    for i, (text, norm, vec_norm) in enumerate(zip(texts, norms.flatten(), np.linalg.norm(normalized, axis=1))):
        logger.debug(
            f"  [{i}] '{text[:60]}...': L2норма_raw={norm:.4f}, L2норма_нормализованного={vec_norm:.4f}"
        )

    return normalized


def most_similar(query_vector: np.ndarray, candidates: np.ndarray, top_k: int = 5) -> List[int]:
    """
    Индексы top_k наиболее похожих кандидатов (по убыванию cosine similarity).
    Логирует косинусное сходство каждого кандидата для отладки.
    """
    if candidates.shape[0] == 0:
        return []

    similarities = candidates @ query_vector  # скалярное произведение = косинусное сходство (векторы нормализованы)
    k = min(top_k, len(similarities))
    top_indices = list(np.argsort(similarities)[::-1][:k])

    logger.debug(f"most_similar: найдено {len(similarities)} кандидатов, выбираем top-{k}")
    for rank, idx in enumerate(top_indices, start=1):
        logger.debug(f"  Место {rank}: индекс={idx}, cosine_similarity={similarities[idx]:.4f}")

    return top_indices