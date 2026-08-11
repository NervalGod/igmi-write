"""
Хранилище истории генераций и агрегация статистики.
История хранится в output/history.json, чтобы переживать перезапуски сервера.
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

HISTORY_PATH = Path("output/history.json")
_lock = threading.Lock()


def _load() -> List[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось прочитать историю: {e}")
        return []


def _save(data: List[Dict[str, Any]]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_to_history(
    filename: str,
    project: str,
    user: str = "anonymous",
    size: int = 0,
) -> None:
    """Добавляет запись о сгенерированном документе."""
    with _lock:
        history = _load()
        history.insert(0, {
            "name": filename,
            "project": project,
            "user": user,
            "date": datetime.now().isoformat(),
            "size": size,
        })
        # Храним последние 500 записей
        _save(history[:500])
    logger.info(f"В историю добавлено: {filename} (проект: {project})")


def remove_from_history(filename: str) -> None:
    with _lock:
        history = [h for h in _load() if h.get("name") != filename]
        _save(history)


def get_history() -> List[Dict[str, Any]]:
    """Возвращает историю (новые сверху)."""
    return _load()


def get_stats() -> Dict[str, Any]:
    """
    Сводная статистика для страницы /stats.html:
    - 4 счётчика сверху
    - гистограмма активности за 14 дней
    - топ пользователей
    """
    history = _load()
    now = datetime.now()

    # === Счётчики ===
    total_docs = len(history)

    # Уникальные "пользователи" (по имени user)
    unique_users = len({h.get("user", "anonymous") for h in history})

    # "Текстов переписано" ≈ документы, где есть проект (не черновики)
    rewritten = sum(1 for h in history if h.get("project"))

    # За 30 дней
    thirty_days_ago = now - timedelta(days=30)
    recent_30d = [
        h for h in history
        if datetime.fromisoformat(h["date"]) >= thirty_days_ago
    ]

    # === Активность за 14 дней ===
    activity = []
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_start = datetime.combine(day, datetime.min.time())
        day_end = datetime.combine(day, datetime.max.time())
        count = sum(
            1 for h in history
            if day_start <= datetime.fromisoformat(h["date"]) <= day_end
        )
        activity.append({
            "date": day.strftime("%d.%m"),
            "value": count,
        })

    # === Топ пользователей ===
    user_counts: Dict[str, int] = {}
    for h in history:
        user = h.get("user", "anonymous")
        user_counts[user] = user_counts.get(user, 0) + 1
    top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # === Когда заходят (по часам) ===
    hour_counts = [0] * 24
    for h in history:
        hour = datetime.fromisoformat(h["date"]).hour
        hour_counts[hour] += 1

    return {
        "counters": {
            "page_views": len(history),          # в локальной системе = кол-ву генераций
            "unique_visitors": unique_users,
            "texts_rewritten": rewritten,
            "texts_created": total_docs,
        },
        "activity_14d": activity,
        "top_users": [{"name": name, "count": cnt} for name, cnt in top_users],
        "hours_distribution": hour_counts,
        "recent_30d_count": len(recent_30d),
    }