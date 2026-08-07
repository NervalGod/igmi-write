"""Клиент генеративной LLM (Ollama chat)."""

import logging
import time

import ollama

logger = logging.getLogger(__name__)

# Грубая оценка: средний токен ~4 символа для английского, ~1.5 для русского.
def estimate_tokens(text: str) -> int:
    rus_chars = sum(1 for c in text if ord(c) > 127)
    eng_chars = len(text) - rus_chars
    return (rus_chars // 1.5) + (eng_chars // 4)


def request(
    prompt: str,
    model: str,
    num_ctx: int = 8192,
    temperature: float = 0.0,
    keep_alive: str = "30m",
    num_predict: int = -1,
    think: bool = False,
) -> str:
    estimated_tokens = estimate_tokens(prompt)
    logger.info(
        f"LLM запрос: {len(prompt)} симв. (~{estimated_tokens} токенов), "
        f"параметры: num_ctx={num_ctx}, temperature={temperature}, num_predict={num_predict}, think={think}"
    )
    logger.debug(f"Содержимое промпта:\n{prompt}\n{'='*80}")

    started = time.perf_counter()
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": num_ctx, "temperature": temperature, "num_predict": num_predict},
            keep_alive=keep_alive,
            think=think,
        )
        elapsed = time.perf_counter() - started
        answer_text = response["message"]["content"].strip()
        answer_tokens = estimate_tokens(answer_text)

        logger.info(
            f"LLM ответ: {len(answer_text)} симв. (~{answer_tokens} токенов), "
            f"время обработки {elapsed:.1f} сек"
        )
        logger.debug(f"Содержимое ответа:\n{answer_text}\n{'='*80}")

        return answer_text
    except Exception as e:
        logger.exception(f"Ошибка при обращении к LLM: {e}")
        raise