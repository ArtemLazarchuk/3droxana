"""Тренувальні дані для модуля аналізу емоцій."""

from assistant_core.data.emotion_dataset import (
    DATASET,
    EMOTION_CLASSES,
    get_dataset,
    get_class_distribution,
)

__all__ = [
    "DATASET",
    "EMOTION_CLASSES",
    "get_dataset",
    "get_class_distribution",
]
