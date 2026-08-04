"""Клиент генеративной LLM (Ollama chat)."""

import logging
import time

import ollama

logger = logging.getLogger(__name__)


def request(
    prompt: str,
    model: str,
    num_ctx: int = 8192,
    temperature: float = 0.0,
    keep_alive: str = "30m",
) -> str:
    """
    Один запрос к LLM. keep_alive держит модель в памяти между вызовами,
    чтобы не платить за повторную загрузку весов на каждом запросе.
    """
    started = time.perf_counter()
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": num_ctx, "temperature": temperature},
            keep_alive=keep_alive,
        )
        elapsed = time.perf_counter() - started
        logger.info(f"LLM: промпт {len(prompt)} симв. -> {elapsed:.1f} сек")
        return response["message"]["content"].strip()
    except Exception:
        logger.exception("Ошибка при обращении к LLM")
        raise
