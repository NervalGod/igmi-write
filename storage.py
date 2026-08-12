"""
Хранилище истории генераций и статические счётчики.

Структура:
    output/
    ├── history.json          метаданные файлов (для списка в UI)
    ├── counters.json         статические счётчики + дневная активность
    └── {project}/
        └── *.docx

Дневная активность — список фиксированной длины (голова = сегодня).
При каждой генерации инкрементируется голова; при смене даты в голову
добавляется новый элемент, пропущенные дни заполняются нулями,
хвост обрезается до DAILY_ACTIVITY_DAYS.
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")
HISTORY_PATH = OUTPUT_DIR / "history.json"
COUNTERS_PATH = OUTPUT_DIR / "counters.json"
_lock = threading.Lock()

DAILY_ACTIVITY_DAYS = 30


# ==========================================
# Структуры по умолчанию
# ==========================================
def _default_daily_activity() -> List[Dict[str, Any]]:
    """Список дневной активности за DAILY_ACTIVITY_DAYS дней. Голова — сегодня."""
    today = datetime.now().date()
    return [
        {"date": (today - timedelta(days=i)).isoformat(), "count": 0}
        for i in range(DAILY_ACTIVITY_DAYS)
    ]


def _default_counters() -> Dict[str, Any]:
    return {
        "total_documents": 0,
        "unique_ips": [],
        "documents_by_ip": {},
        "daily_activity": _default_daily_activity(),
    }


# ==========================================
# Чтение / запись
# ==========================================
def _load_history() -> List[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось прочитать историю: {e}")
        return []


def _save_history(data: List[Dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_counters() -> Dict[str, Any]:
    default = _default_counters()
    if not COUNTERS_PATH.exists():
        return default
    try:
        with COUNTERS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Не удалось прочитать счётчики: {e}")
        return default
    # Дозаполняем отсутствующие обязательные поля дефолтами
    for key, value in default.items():
        data.setdefault(key, value)
    return data


def _save_counters(data: Dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with COUNTERS_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==========================================
# Дневная активность (голова = сегодня)
# ==========================================
def _normalize_daily_activity(daily_activity: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Актуализирует окно БЕЗ изменения счётчиков:
    - пустой/битый список → новый
    - голова в прошлом → добавляет пропущенные дни с нулями в голову
    - обрезает хвост до DAILY_ACTIVITY_DAYS
    """
    today = datetime.now().date()

    if not daily_activity:
        return _default_daily_activity()

    try:
        head_date = datetime.fromisoformat(daily_activity[0].get("date", "")).date()
    except (ValueError, TypeError):
        return _default_daily_activity()

    if head_date > today:
        return _default_daily_activity()

    if head_date < today:
        days_gap = (today - head_date).days
        for i in range(days_gap):
            new_date = today - timedelta(days=i)
            daily_activity.insert(0, {"date": new_date.isoformat(), "count": 0})

    return daily_activity[:DAILY_ACTIVITY_DAYS]


# ==========================================
# История (для списка файлов в UI)
# ==========================================
def safe_project_name(project: str) -> str:
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in project).strip()
    return safe[:80] or "Без_названия"


def get_project_dir(project: str) -> Path:
    project_dir = OUTPUT_DIR / safe_project_name(project)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def add_to_history(filename: str, project: str, user_ip: str, size: int = 0) -> None:
    rel_path = f"{safe_project_name(project)}/{filename}"
    with _lock:
        history = _load_history()
        history.insert(0, {
            "name": filename,
            "rel_path": rel_path,
            "project": project,
            "user_ip": user_ip,
            "date": datetime.now().isoformat(),
            "size": size,
        })
        _save_history(history[:500])
    logger.info(f"В историю добавлено: {rel_path} (ip={user_ip})")


def get_history() -> List[Dict[str, Any]]:
    return _load_history()


def remove_from_history(rel_path: str) -> None:
    with _lock:
        history = [h for h in _load_history() if h.get("rel_path") != rel_path]
        _save_history(history)


def resolve_file_path(rel_path: str) -> Optional[Path]:
    parts = Path(rel_path).parts
    if len(parts) != 2:
        return None
    project_dir_name, filename = parts
    if ".." in project_dir_name or ".." in filename:
        return None
    full_path = (OUTPUT_DIR / project_dir_name / filename).resolve()
    try:
        full_path.relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return None
    return full_path if full_path.is_file() else None


def delete_file(rel_path: str) -> bool:
    path = resolve_file_path(rel_path)
    if path and path.exists():
        path.unlink()
        return True
    return False


# ==========================================
# Регистрация документа (инкремент счётчиков)
# ==========================================
def register_document(user_ip: str) -> None:
    """
    Вызывается при успешной генерации документа.
    Инкрементирует общий счётчик, счётчик по IP и голову дневной активности.
    """
    with _lock:
        counters = _load_counters()
        counters["daily_activity"] = _normalize_daily_activity(counters.get("daily_activity", []))

        counters["total_documents"] = counters.get("total_documents", 0) + 1

        unique_ips = counters.get("unique_ips", [])
        if user_ip and user_ip not in unique_ips:
            unique_ips.append(user_ip)
        counters["unique_ips"] = unique_ips

        docs_by_ip = counters.get("documents_by_ip", {})
        docs_by_ip[user_ip] = docs_by_ip.get(user_ip, 0) + 1
        counters["documents_by_ip"] = docs_by_ip

        counters["daily_activity"][0]["count"] = (
            counters["daily_activity"][0].get("count", 0) + 1
        )

        _save_counters(counters)

    logger.info(
        f"Счётчики обновлены: total={counters['total_documents']}, "
        f"today={counters['daily_activity'][0]['count']}, "
        f"unique_ips={len(counters['unique_ips'])}"
    )


# ==========================================
# Статистика (статическая)
# ==========================================
def get_stats() -> Dict[str, Any]:
    """Читает готовые счётчики из counters.json, без перебора файлов."""
    with _lock:
        counters = _load_counters()
        counters["daily_activity"] = _normalize_daily_activity(counters.get("daily_activity", []))
        _save_counters(counters)  # фиксируем актуальное окно на диске

    daily_activity = counters["daily_activity"]
    today_count = daily_activity[0].get("count", 0) if daily_activity else 0
    documents_30d = sum(d.get("count", 0) for d in daily_activity)

    # График за 14 дней: голова = сегодня; разворот, чтобы старые были слева
    activity_14d = [
        {"date": datetime.fromisoformat(d["date"]).strftime("%d.%m"), "value": d.get("count", 0)}
        for d in daily_activity[:14][::-1]
    ]

    docs_by_ip = counters.get("documents_by_ip", {})
    top_users = sorted(docs_by_ip.items(), key=lambda x: x[1], reverse=True)[:10]

    # Собираем счётчики с двойными именами — чтобы и documents_30d, и recent_30d_count работали
    counters_block = {
        "texts_created": counters.get("total_documents", 0),
        "unique_visitors": len(counters.get("unique_ips", [])),
        "documents_30d": documents_30d,
        "today_count": today_count,
        # Алиасы — для обратной совместимости со stats.js
        "recent_30d_count": documents_30d,
        "texts_rewritten": counters.get("total_documents", 0),
        "page_views": documents_30d,
    }

    return {
        "counters": counters_block,
        "activity_14d": activity_14d,
        "top_users": [{"name": ip, "count": cnt} for ip, cnt in top_users],
    }