"""Клиент генеративной LLM (Ollama chat)."""

import logging
import time

import ollama

logger = logging.getLogger(__name__)

# Грубая оценка: средний токен ~4 символа для английского, ~1.5 для русского.
# Это очень приблизительно, но для отладки достаточно.
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
    """
    Один запрос к LLM. keep_alive держит модель в памяти между вызовами,
    чтобы не платить за повторную загрузку весов на каждом запросе.

    num_predict=-1 — снимает ограничение Ollama по умолчанию на длину
    ответа (в некоторых версиях это всего 128 токенов).

    think=False — КРИТИЧНО для моделей семейства Qwen3.x (в т.ч. qwen3.6):
    по умолчанию они генерируют внутренние рассуждения в блоке <think>...</think>
    ПЕРЕД фактическим ответом. Если бюджет токенов (num_predict/num_ctx)
    исчерпывается во время размышлений, модель до реального ответа просто
    не доходит — content возвращается ПУСТЫМ, при этом время и вычисления
    были потрачены полностью (задокументированный баг/особенность:
    ollama/ollama issues #10976, #14793 — thinking tokens молча съедают
    весь бюджет). think=False отключает эту стадию, отдавая весь бюджет
    сразу под фактический ответ.
    """
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

        if not answer_text:
            # Явный сигнал в лог: пустой ответ при ненулевом времени обработки —
            # почти наверняка бюджет токенов был съеден размышлениями/другой
            # служебной генерацией, а не тем, что модель "промолчала по формату".
            logger.warning(
                f"Пустой ответ LLM при времени обработки {elapsed:.1f} сек — "
                f"похоже, весь бюджет токенов был потрачен ДО фактического ответа "
                f"(например, на thinking). Проверьте think=False и запас num_ctx."
            )

        return answer_text
    except Exception as e:
        logger.exception(f"Ошибка при обращении к LLM: {e}")
        raise