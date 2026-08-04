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
    num_predict: int = -1,
) -> str:
    """
    Один запрос к LLM. keep_alive держит модель в памяти между вызовами,
    чтобы не платить за повторную загрузку весов на каждом запросе.

    num_predict=-1 — снимает ограничение Ollama по умолчанию на длину
    ответа (в некоторых версиях это всего 128 токенов). Для задач вроде
    заполнения таблиц, где ожидается длинный структурированный ответ
    (десятки-сотни строк), заниженный лимит незаметно обрезает ответ
    ДО того, как модель успеет написать хоть одну валидную строку —
    и парсер потом видит "0 значений", хотя дело не в модели, а в лимите.
    """
    started = time.perf_counter()
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": num_ctx, "temperature": temperature, "num_predict": num_predict},
            keep_alive=keep_alive,
        )
        elapsed = time.perf_counter() - started
        logger.info(f"LLM: промпт {len(prompt)} симв. -> {elapsed:.1f} сек, ответ {len(response['message']['content'])} симв.")
        logger.debug(f"Сырой ответ LLM:\n{response['message']['content']}")
        return response["message"]["content"].strip()
    except Exception:
        logger.exception("Ошибка при обращении к LLM")
        raise