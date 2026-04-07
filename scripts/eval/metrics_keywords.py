"""키워드 추출 평가 메트릭."""


def compute_keyword_metrics(
    human_keywords_list: list[list[str]],
    model_keywords_list: list[list[str]],
) -> dict:
    """키워드 Jaccard overlap 메트릭 계산.

    Args:
        human_keywords_list: 정답 키워드 리스트의 리스트 (각 아티클당 3개)
        model_keywords_list: 모델 예측 키워드 리스트의 리스트
    """
    assert len(human_keywords_list) == len(model_keywords_list)
    n = len(human_keywords_list)
    if n == 0:
        return {}

    jaccard_scores = []
    overlap_2_count = 0  # 2개 이상 겹치는 아티클 수

    for human_kw, model_kw in zip(human_keywords_list, model_keywords_list):
        # N/A 제외, 소문자 정규화
        h_set = {k.strip().lower() for k in human_kw if k.strip().lower() != "n/a"}
        m_set = {k.strip().lower() for k in model_kw if k.strip().lower() != "n/a"}

        if not h_set and not m_set:
            jaccard_scores.append(1.0)
            overlap_2_count += 1
            continue

        if not h_set or not m_set:
            jaccard_scores.append(0.0)
            continue

        intersection = h_set & m_set
        union = h_set | m_set
        jaccard = len(intersection) / len(union) if union else 0.0
        jaccard_scores.append(jaccard)

        if len(intersection) >= 2:
            overlap_2_count += 1

    avg_jaccard = sum(jaccard_scores) / n
    overlap_2_rate = overlap_2_count / n

    return {
        "avg_jaccard": round(avg_jaccard, 4),
        "overlap_2_rate": round(overlap_2_rate, 4),
        "per_article_jaccard": [round(s, 4) for s in jaccard_scores],
    }
