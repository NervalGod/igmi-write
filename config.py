"""Загрузка и валидация конфигурации проекта."""

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Config:
    source_path: str
    template_path: str
    output_path: str
    model: str
    embed_model: str = "bge-m3"
    num_ctx: int = 8192
    temperature: float = 0.0
    keep_alive: str = "30m"
    num_predict: int = -1

def load_config(path: str = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    required = ["source_path", "template_path", "output_path", "model"]
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"В config.yaml отсутствуют обязательные поля: {missing}")

    return Config(
        source_path=raw["source_path"],
        template_path=raw["template_path"],
        output_path=raw["output_path"],
        model=raw["model"],
        embed_model=raw.get("embed_model", "bge-m3"),
        num_ctx=raw.get("num_ctx", 8192),
        temperature=raw.get("temperature", 0.0),
        keep_alive=raw.get("keep_alive", "30m"),
        num_predict=raw.get("num_predict", -1),
    )