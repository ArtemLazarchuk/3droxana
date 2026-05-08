"""
assistant_core/ml_classifier.py
══════════════════════════════════════════════════════════════════════════════
Класифікатор емоцій на основі машинного навчання (sklearn).

ОБРАНИЙ АЛГОРИТМ
════════════════

    Ансамбль:
        TF-IDF (word n-grams + char n-grams)  →  Logistic Regression (multinomial)

    Чому саме така комбінація:
    ──────────────────────────
    1. TF-IDF (Term Frequency – Inverse Document Frequency)
       •  word-1,2-grams ловлять змістові послідовності («дуже радий», «не вдалося»)
       •  char-3,5-grams робочі для української мови з її флексією
          (один словник, але різні форми: «радий / рада / радіти»)
       •  L2-нормалізація → стійкість до довжини тексту
    2. Logistic Regression (multinomial, softmax)
       •  Інтерпретована модель — для диплому це плюс (можна показати ваги)
       •  predict_proba повертає розподіл по класах → інтегрується у softmax-агрегатор
       •  Швидко тренується на ~200 прикладах (≤1 с)
       •  Стійка до перенавчання при regularization C ≈ 1.0

ІНТЕРФЕЙС
═════════
    EmotionMLClassifier()
        .train(texts, labels)        — тренує модель і зберігає на диск
        .predict(text) -> dict       — повертає {emotion: probability, ...}
        .evaluate(...) -> dict       — повертає accuracy / precision / recall / f1
        .is_loaded -> bool           — чи завантажена модель
        .load(path) / .save(path)    — серіалізація через joblib

ІНТЕГРАЦІЯ
══════════
    Об'єкт використовується у `assistant_core/emotion_engine.py` як третій
    компонент пайплайна (поряд із лексиконом і регексами). Якщо модель не
    знайдена на диску — вона не блокує роботу системи: пайплайн працює як
    раніше (lex + pat).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Шлях за замовчуванням, куди зберігається натренована модель
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "emotion_model.joblib"


# ── Опціональні залежності (sklearn) ──────────────────────────────────────────
# Якщо sklearn не встановлено — клас все одно імпортується, але працює як stub.
try:
    import joblib  # type: ignore
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
    from sklearn.metrics import (  # type: ignore
        accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )
    from sklearn.model_selection import train_test_split  # type: ignore
    from sklearn.pipeline import FeatureUnion, Pipeline  # type: ignore

    _HAS_SKLEARN = True
except ImportError:  # pragma: no cover
    _HAS_SKLEARN = False
    logger.warning(
        "scikit-learn / joblib не встановлено — ML-класифікатор недоступний. "
        "Виконайте: pip install scikit-learn joblib"
    )


# ── Список класів (мусить збігатися з emotion_engine.EMOTION_LIST) ────────────
EMOTION_CLASSES: List[str] = [
    "neutral",
    "happy",
    "sad",
    "surprise",
    "thinking",
    "angry",
    "disgust",
]


# ══════════════════════════════════════════════════════════════════════════════
# Метрики (повертаються після тренування / валідації)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrainingMetrics:
    """Результати тренування / валідації моделі."""

    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    classification_report: str
    confusion_matrix: List[List[int]]
    classes: List[str]
    n_train: int
    n_test: int

    def to_dict(self) -> Dict:
        return {
            "accuracy": round(self.accuracy, 4),
            "precision_macro": round(self.precision_macro, 4),
            "recall_macro": round(self.recall_macro, 4),
            "f1_macro": round(self.f1_macro, 4),
            "classification_report": self.classification_report,
            "confusion_matrix": self.confusion_matrix,
            "classes": self.classes,
            "n_train": self.n_train,
            "n_test": self.n_test,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Головний класифікатор
# ══════════════════════════════════════════════════════════════════════════════

class EmotionMLClassifier:
    """
    Класифікатор емоцій на базі TF-IDF + Logistic Regression.

    Сценарій використання у проєкті:
    ────────────────────────────────
    1) Розробник запускає `python scripts/train_emotion_model.py`.
       Скрипт викликає .train() → модель зберігається у `models/emotion_model.joblib`.
    2) При старті FastAPI у `EmotionClassifier` створюється singleton, який
       намагається завантажити модель. Якщо файлу немає → `is_loaded = False`,
       і ML-вектор у пайплайні дорівнює нулю (працюють лише lex + pat).
    3) Під час кожного запиту викликається `predict(text)` → словник
       {emotion: probability}, що додається до загальних балів через ваги
       у `ScoreAggregator`.
    """

    def __init__(self, model_path: Optional[Path] = None) -> None:
        self._model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self._pipeline: Optional[object] = None  # sklearn Pipeline | None
        self._classes: List[str] = list(EMOTION_CLASSES)

    # ── Доступ до стану ──────────────────────────────────────────────────────
    @property
    def is_loaded(self) -> bool:
        """Чи готова модель до інференсу."""
        return self._pipeline is not None

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def classes(self) -> List[str]:
        return list(self._classes)

    # ── Побудова пайплайна (TF-IDF + LogReg) ─────────────────────────────────
    @staticmethod
    def _build_pipeline():
        """
        Конструює sklearn-пайплайн.

        Архітектура:
            ┌─────────────────────────────────────────────────────────┐
            │  FeatureUnion                                           │
            │    ├── TfidfVectorizer(word, 1-2 grams)                 │
            │    └── TfidfVectorizer(char_wb, 3-5 grams)              │
            └─────────────────────────────────────────────────────────┘
                              │
                              ▼
            ┌─────────────────────────────────────────────────────────┐
            │  LogisticRegression(multi_class='multinomial',           │
            │                      solver='lbfgs', C=1.0)             │
            └─────────────────────────────────────────────────────────┘
        """
        if not _HAS_SKLEARN:
            raise RuntimeError("scikit-learn не встановлено. Запустіть: pip install scikit-learn joblib")

        word_vec = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            min_df=1,
            max_df=0.95,
            sublinear_tf=True,
            lowercase=True,
        )
        char_vec = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            min_df=1,
            max_df=0.95,
            sublinear_tf=True,
            lowercase=True,
        )
        features = FeatureUnion(
            [
                ("word_tfidf", word_vec),
                ("char_tfidf", char_vec),
            ]
        )
        # `liblinear` — найстабільніший solver для маленьких текстових датасетів
        # на macOS (lbfgs у деяких збірках OpenBLAS падає на FeatureUnion з
        # char_wb-фічами). Для бінарного / one-vs-rest працює відмінно.
        clf = LogisticRegression(
            solver="liblinear",
            max_iter=2000,
            C=1.0,
            class_weight="balanced",
        )
        pipeline = Pipeline(
            [
                ("features", features),
                ("clf", clf),
            ]
        )
        return pipeline

    # ── Тренування ───────────────────────────────────────────────────────────
    def train(
        self,
        texts: List[str],
        labels: List[str],
        test_size: float = 0.2,
        random_state: int = 42,
    ) -> TrainingMetrics:
        """
        Тренує модель і повертає метрики на тестовій вибірці.

        Параметри:
            texts        — корпус документів (str)
            labels       — список міток (рядки з EMOTION_CLASSES)
            test_size    — частка тестової вибірки [0.1 … 0.5]
            random_state — seed для відтворюваності

        Повертає:
            TrainingMetrics(accuracy, precision, recall, f1, ...)

        Викликає:
            RuntimeError, якщо sklearn недоступний
            ValueError, якщо у вибірці < 2 класів
        """
        if not _HAS_SKLEARN:
            raise RuntimeError("scikit-learn не встановлено.")

        if len(set(labels)) < 2:
            raise ValueError("Для навчання потрібно щонайменше 2 класи.")

        X_train, X_test, y_train, y_test = train_test_split(
            texts, labels,
            test_size=test_size,
            random_state=random_state,
            stratify=labels,
        )

        pipeline = self._build_pipeline()
        pipeline.fit(X_train, y_train)
        self._pipeline = pipeline

        y_pred = pipeline.predict(X_test)
        labels_sorted = sorted(set(labels))

        metrics = TrainingMetrics(
            accuracy=accuracy_score(y_test, y_pred),
            precision_macro=precision_score(y_test, y_pred, average="macro", zero_division=0),
            recall_macro=recall_score(y_test, y_pred, average="macro", zero_division=0),
            f1_macro=f1_score(y_test, y_pred, average="macro", zero_division=0),
            classification_report=classification_report(
                y_test, y_pred, labels=labels_sorted, zero_division=0
            ),
            confusion_matrix=confusion_matrix(y_test, y_pred, labels=labels_sorted).tolist(),
            classes=labels_sorted,
            n_train=len(X_train),
            n_test=len(X_test),
        )
        return metrics

    # ── Збереження / завантаження ────────────────────────────────────────────
    def save(self, path: Optional[Path] = None) -> Path:
        """Зберігає натреновану модель у файл (joblib)."""
        if not _HAS_SKLEARN:
            raise RuntimeError("scikit-learn не встановлено.")
        if self._pipeline is None:
            raise RuntimeError("Модель ще не натренована. Викличте .train() спочатку.")
        target = Path(path) if path else self._model_path
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._pipeline, target)
        logger.info("Emotion ML model saved → %s", target)
        return target

    def load(self, path: Optional[Path] = None) -> bool:
        """
        Завантажує модель з файлу. Повертає True у разі успіху, False якщо файлу
        немає або sklearn недоступний (тоді працюємо без ML).
        """
        if not _HAS_SKLEARN:
            return False
        target = Path(path) if path else self._model_path
        if not target.exists():
            logger.info("ML модель не знайдена за шляхом %s — працюємо без ML", target)
            return False
        try:
            self._pipeline = joblib.load(target)
            logger.info("Emotion ML model loaded ← %s", target)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не вдалося завантажити ML-модель: %s", exc)
            self._pipeline = None
            return False

    # ── Інференс ─────────────────────────────────────────────────────────────
    def predict(self, text: str) -> Dict[str, float]:
        """
        Повертає словник {emotion: probability} для усіх класів EMOTION_CLASSES.

        Якщо модель не завантажена — повертає рівномірний розподіл для класу
        neutral (≈1.0) і нулі для інших, щоб ML-внесок не псував лексичні бали.

        Приклад:
            >>> clf.predict("Я так втомився сьогодні")
            {'neutral': 0.05, 'happy': 0.02, 'sad': 0.85, 'surprise': 0.03, 'thinking': 0.05}
        """
        if not text or not text.strip():
            return self._empty_distribution()

        if self._pipeline is None:
            return self._empty_distribution()

        try:
            probas = self._pipeline.predict_proba([text])[0]
        except Exception as exc:  # noqa: BLE001
            logger.exception("ML.predict() помилка: %s", exc)
            return self._empty_distribution()

        try:
            classes = list(self._pipeline.named_steps["clf"].classes_)
        except Exception:  # noqa: BLE001
            classes = list(self._classes)

        result = {cls: 0.0 for cls in EMOTION_CLASSES}
        for cls, p in zip(classes, probas):
            if cls in result:
                result[cls] = float(p)
        return result

    def predict_label(self, text: str) -> Tuple[str, float]:
        """Зручний шорткат: повертає (label, confidence)."""
        dist = self.predict(text)
        if not dist:
            return "neutral", 1.0
        label = max(dist, key=lambda k: dist[k])
        return label, dist[label]

    @staticmethod
    def _empty_distribution() -> Dict[str, float]:
        """Розподіл «модель неактивна» — увесь вес на neutral."""
        return {cls: (1.0 if cls == "neutral" else 0.0) for cls in EMOTION_CLASSES}


# ══════════════════════════════════════════════════════════════════════════════
# Singleton — одна модель на процес FastAPI
# ══════════════════════════════════════════════════════════════════════════════

_ml_classifier: Optional[EmotionMLClassifier] = None


def get_ml_classifier() -> EmotionMLClassifier:
    """
    Singleton-фабрика. При першому виклику пробує завантажити модель з диску;
    якщо її немає — повертає об'єкт-stub (`predict()` повертає neutral=1.0).
    """
    global _ml_classifier
    if _ml_classifier is None:
        clf = EmotionMLClassifier()
        clf.load()
        _ml_classifier = clf
    return _ml_classifier


def reload_ml_classifier() -> bool:
    """Перезавантажити модель з диску (наприклад, після перетренування)."""
    clf = get_ml_classifier()
    return clf.load()


def is_ml_available() -> bool:
    """Чи встановлено scikit-learn / joblib (імпорт успішний). Не вимагає файлу моделі."""
    return _HAS_SKLEARN
