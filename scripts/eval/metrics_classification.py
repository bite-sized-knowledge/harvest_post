"""카테고리 분류 평가 메트릭."""
from collections import Counter


def compute_classification_metrics(
    human_categories: list[int],
    model_categories: list[int],
    num_classes: int = 13,
) -> dict:
    """Accuracy, per-class precision/recall, macro F1, confusion matrix 계산.

    Args:
        human_categories: 정답 카테고리 ID 리스트 (1-indexed)
        model_categories: 모델 예측 카테고리 ID 리스트
        num_classes: 전체 카테고리 수
    """
    assert len(human_categories) == len(model_categories)
    n = len(human_categories)

    # Accuracy
    correct = sum(h == m for h, m in zip(human_categories, model_categories))
    accuracy = correct / n if n > 0 else 0.0

    # Confusion matrix (1-indexed → 0-indexed internally)
    confusion = [[0] * num_classes for _ in range(num_classes)]
    for h, m in zip(human_categories, model_categories):
        confusion[h - 1][m - 1] += 1

    # Per-class precision, recall, F1
    per_class = {}
    f1_scores = []
    for c in range(num_classes):
        tp = confusion[c][c]
        fp = sum(confusion[r][c] for r in range(num_classes)) - tp
        fn = sum(confusion[c][p] for p in range(num_classes)) - tp

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        per_class[c + 1] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": sum(confusion[c]),
        }
        if sum(confusion[c]) > 0:
            f1_scores.append(f1)

    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def format_confusion_matrix(confusion: list[list[int]], labels: list[str] = None) -> str:
    """Confusion matrix를 보기 좋은 텍스트 테이블로 포맷."""
    n = len(confusion)
    if labels is None:
        labels = [str(i + 1) for i in range(n)]

    # 각 컬럼 폭 계산
    max_label = max(len(l) for l in labels)
    col_width = max(3, max_label)

    header = " " * (max_label + 2) + "  ".join(f"{l:>{col_width}}" for l in labels)
    lines = [header]
    for i, row in enumerate(confusion):
        row_str = f"{labels[i]:>{max_label}}  " + "  ".join(f"{v:>{col_width}}" for v in row)
        lines.append(row_str)

    return "\n".join(lines)
