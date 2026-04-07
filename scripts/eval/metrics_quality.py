"""품질 점수 평가 메트릭."""
from __future__ import annotations

from collections import Counter


def compute_quality_metrics(
    human_scores: list[int],
    model_scores: list[int],
) -> dict:
    """MAE, Spearman 상관계수, exact match, within-1 비율, 분포 계산.

    Args:
        human_scores: 정답 품질 점수 리스트 (1-5)
        model_scores: 모델 예측 품질 점수 리스트 (1-5)
    """
    assert len(human_scores) == len(model_scores)
    n = len(human_scores)
    if n == 0:
        return {}

    # MAE
    errors = [abs(h - m) for h, m in zip(human_scores, model_scores)]
    mae = sum(errors) / n

    # Exact match rate
    exact_match = sum(h == m for h, m in zip(human_scores, model_scores)) / n

    # Within-1 rate
    within_1 = sum(abs(h - m) <= 1 for h, m in zip(human_scores, model_scores)) / n

    # Spearman rank correlation
    spearman = _spearman_correlation(human_scores, model_scores)

    # Score distributions
    human_dist = Counter(human_scores)
    model_dist = Counter(model_scores)
    distribution = {
        "human": {s: human_dist.get(s, 0) for s in range(1, 6)},
        "model": {s: model_dist.get(s, 0) for s in range(1, 6)},
    }

    # 5x5 confusion matrix (quality scores 1-5)
    confusion = [[0] * 5 for _ in range(5)]
    for h, m in zip(human_scores, model_scores):
        confusion[h - 1][m - 1] += 1

    return {
        "mae": round(mae, 4),
        "spearman": round(spearman, 4) if spearman is not None else None,
        "exact_match": round(exact_match, 4),
        "within_1": round(within_1, 4),
        "distribution": distribution,
        "confusion_matrix": confusion,
    }


def _spearman_correlation(x: list, y: list) -> float | None:
    """Spearman rank correlation (scipy 없이 직접 구현)."""
    n = len(x)
    if n < 3:
        return None

    def _rank(values):
        sorted_indices = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and values[sorted_indices[j]] == values[sorted_indices[j + 1]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[sorted_indices[k]] = avg_rank
            i = j + 1
        return ranks

    rank_x = _rank(x)
    rank_y = _rank(y)

    d_sq = sum((rx - ry) ** 2 for rx, ry in zip(rank_x, rank_y))

    # Pearson on ranks
    mean_x = sum(rank_x) / n
    mean_y = sum(rank_y) / n
    cov = sum((rx - mean_x) * (ry - mean_y) for rx, ry in zip(rank_x, rank_y))
    std_x = (sum((rx - mean_x) ** 2 for rx in rank_x)) ** 0.5
    std_y = (sum((ry - mean_y) ** 2 for ry in rank_y)) ** 0.5

    if std_x == 0 or std_y == 0:
        return None

    return cov / (std_x * std_y)
