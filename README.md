# 🌽 Harvest Post

> `Bite-Knowledge` 프로젝트에서 크롤러가 `article_queue`에 적재한 raw HTML 아티클을
> LLM 기반으로 분류·전처리·임베딩 후 `article` 테이블로 승격시키는 배치 파이프라인.

## 아키텍처

**런타임**: self-hosted GPU 머신 (WoL 기반 on-demand)
- vLLM (`qwen2.5:7b`) → LLM classification + quality scoring
- vLLM pooling (`qwen3-embedding:0.6b`) → embedding 생성
- harvest-post 컨테이너 → 파이프라인 오케스트레이션

두 vLLM 인스턴스 모두 `vllm/vllm-openai` 이미지, OpenAI-compatible API로 통신.
전체 스택은 `docker-compose.gpu.yml`에 정의되어 `run.sh`가 관리한다.

## 파이프라인 흐름

```
article_queue
    ↓
[pre-LLM gate] 제목 없음 → article_rejected (empty_title)
[pre-LLM gate] 본문 + 영상 임베드 없음 → article_rejected (empty_content)
    ↓
vllm-chat: focusing + keywords + quality_score 추출
    ↓
[post-LLM gate] quality_score < 3 → article_rejected (low_quality_N)
[post-LLM gate] LLM parse 실패 → article_rejected (llm_extraction_failed)
    ↓
vllm-embed: 벡터 생성
    ↓
Qdrant upsert + article 테이블 INSERT
    ↓
bite → bite_dev 동기화
```

## 배포

GitHub push(prod) → CI validate → deploy-webhook(Mac) → WoL GPU → git pull + docker build → systemd harvest-post.service 자동 기동.

세부 흐름은 `.github/workflows/deploy-homeserver.yml` 및 infra 리포의
`scripts/deploy-gpu-harvest-post.sh` 참고.

## 운영 명령

```bash
# GPU에서 수동 기동 (디버그)
cd ~/harvest_post
docker compose -f docker-compose.gpu.yml up -d --build
docker compose -f docker-compose.gpu.yml run --rm harvest-post python3 -m main --continuous
docker compose -f docker-compose.gpu.yml down

# 모니터링
mysql -e "SELECT reject_reason, COUNT(*) FROM bite.article_rejected \
  WHERE rejected_at > NOW() - INTERVAL 1 DAY GROUP BY reject_reason;"

journalctl -u harvest-post.service -f  # GPU local
```

## 주요 파일

| 파일 | 역할 |
|------|------|
| `src/main.py` | 파이프라인 오케스트레이션 + pre/post LLM gate |
| `src/config.py` | LLM/Embedding 설정, reject threshold |
| `src/llm_pipeline/` | vLLM client + prompt generator + pydantic schemas |
| `src/embedder.py` | vLLM embedding client (OpenAI-compatible) |
| `src/preprocessing/` | HTML 파싱 + 텍스트 정제 |
| `docker-compose.gpu.yml` | 3-service GPU 스택 정의 |
| `run.sh` | systemd가 호출하는 run script (git pull → up → wait → sync → down → shutdown) |
| `scripts/sync_bite_to_dev.py` | bite → bite_dev MySQL + Qdrant 동기화 |
| `src/prompt.yml` | LLM prompt (focusing/keywords/quality_score task) |
