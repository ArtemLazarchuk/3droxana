"""
scripts/train_emotion_model.py
══════════════════════════════════════════════════════════════════════════════
CLI-скрипт для тренування ML-моделі класифікації емоцій.

ЯК ЗАПУСТИТИ
════════════
    # У корені проєкту, у активованому venv:
    python -m scripts.train_emotion_model

    # Або:
    python scripts/train_emotion_model.py

    # З ключами (опціонально):
    python -m scripts.train_emotion_model --test-size 0.25 --output models/my_model.joblib

ЩО РОБИТЬ
═════════
1. Завантажує датасет з `assistant_core/data/emotion_dataset.py` (≈200 прикладів)
2. Розбиває на train/test (стратифіковано за класами)
3. Будує пайплайн TF-IDF (word + char n-grams) + LogisticRegression
4. Тренує модель, виводить метрики:
        accuracy, precision, recall, F1 (macro)
        per-class classification report
        confusion matrix
5. Зберігає натреновану модель у `models/emotion_model.joblib`
6. Виконує приклади передбачень для візуальної перевірки

ВИХІДНИЙ КОД
════════════
    0 — успіх
    1 — помилка (немає sklearn / помилка тренування)
    2 — accuracy < поріг (для CI можна додавати --strict)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Tuple

# Додаємо корінь проєкту до sys.path, щоб запускалось і як `python scripts/train_emotion_model.py`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant_core.data import get_class_distribution, get_dataset  # noqa: E402
from assistant_core.ml_classifier import EmotionMLClassifier  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("train")


# Тестові приклади для смокового перегляду після тренування
SMOKE_EXAMPLES: List[str] = [
    "Дякую дуже, ти найкращий помічник!",
    "Я провалив екзамен, мені дуже сумно",
    "Ого, як це могло статися?!",
    "Цікаво, як це працює насправді?",
    "Розклад занять опубліковано на сайті",
    "Не дуже подобається ця дисципліна",
    "Я не можу повірити, ти серйозно це зробив?",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Тренування ML-моделі емоцій (TF-IDF + LogReg).")
    p.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Частка тестової вибірки (0.1–0.5). За замовчуванням 0.2.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed для відтворюваності. За замовчуванням 42.",
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Шлях до файлу збереження моделі (joblib). За замовчуванням — models/emotion_model.joblib.",
    )
    p.add_argument(
        "--min-accuracy",
        type=float,
        default=0.0,
        help="Мінімально допустима accuracy на тесті. Якщо менше — exit code 2.",
    )
    p.add_argument(
        "--no-save",
        action="store_true",
        help="Не зберігати модель на диск (лише вивести метрики).",
    )
    return p.parse_args()


def print_class_distribution() -> None:
    dist = get_class_distribution()
    total = sum(dist.values()) or 1
    log.info("Розподіл класів у датасеті (всього %d прикладів):", total)
    for cls, n in dist.items():
        bar = "█" * int(40 * n / total)
        log.info("  %-9s  %3d  (%5.1f%%) %s", cls, n, 100 * n / total, bar)


def print_metrics(metrics) -> None:
    log.info("─" * 70)
    log.info("МЕТРИКИ НА ТЕСТОВІЙ ВИБІРЦІ")
    log.info("─" * 70)
    log.info("  train: %3d  |  test: %3d", metrics.n_train, metrics.n_test)
    log.info("  accuracy        : %.4f", metrics.accuracy)
    log.info("  precision (mac) : %.4f", metrics.precision_macro)
    log.info("  recall    (mac) : %.4f", metrics.recall_macro)
    log.info("  f1        (mac) : %.4f", metrics.f1_macro)
    log.info("─" * 70)
    log.info("Classification report:\n%s", metrics.classification_report)
    log.info("Confusion matrix (rows = true, cols = pred):")
    log.info("  classes: %s", metrics.classes)
    for row in metrics.confusion_matrix:
        log.info("    %s", row)
    log.info("─" * 70)


def smoke_test(clf: EmotionMLClassifier) -> None:
    log.info("СМОКОВИЙ ТЕСТ — приклади передбачень:")
    log.info("─" * 70)
    for txt in SMOKE_EXAMPLES:
        label, conf = clf.predict_label(txt)
        dist = clf.predict(txt)
        top3 = sorted(dist.items(), key=lambda kv: -kv[1])[:3]
        top3_str = ", ".join(f"{k}={v:.2f}" for k, v in top3)
        log.info("  [%-9s | %.2f] %s", label, conf, txt)
        log.info("     top-3: %s", top3_str)
    log.info("─" * 70)


def main() -> int:
    args = parse_args()

    log.info("Тренування моделі емоцій (TF-IDF + Logistic Regression)")

    try:
        texts, labels = get_dataset()
    except Exception as exc:  # noqa: BLE001
        log.error("Не вдалося завантажити датасет: %s", exc)
        return 1

    if not texts:
        log.error("Датасет порожній.")
        return 1

    print_class_distribution()

    clf = EmotionMLClassifier(model_path=Path(args.output) if args.output else None)

    log.info("Тренування…  (test_size=%.2f, seed=%d)", args.test_size, args.seed)
    try:
        metrics = clf.train(
            texts=texts,
            labels=labels,
            test_size=args.test_size,
            random_state=args.seed,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Помилка тренування: %s", exc)
        return 1

    print_metrics(metrics)

    if not args.no_save:
        path = clf.save()
        log.info("Модель збережено → %s", path)
    else:
        log.info("--no-save → модель не збережено на диск.")

    smoke_test(clf)

    if metrics.accuracy < args.min_accuracy:
        log.error(
            "Accuracy %.4f < min-accuracy %.4f — повертаю код 2.",
            metrics.accuracy, args.min_accuracy,
        )
        return 2

    log.info("Готово ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
