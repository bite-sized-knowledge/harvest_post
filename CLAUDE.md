# Harvest Post Lambda - Project Memory

## Overview
블로그 포스트 수집 후 LLM 기반 텍스트 분류/구조화 및 벡터 임베딩 생성하는 AWS Lambda 프로젝트

## Tech Stack
- Python 3.11, AWS Lambda, LangChain
- LLM: OpenAI GPT-5-nano (temperature=1 only, timeout=60s)
- Embedding: AWS Bedrock Titan Embed Text v2
- Vector DB: Qdrant
- Database: MySQL (SQLAlchemy)

## Key Files
| 파일 | 역할 |
|------|------|
| `src/main.py` | Lambda 핸들러, 오케스트레이션 |
| `src/config.py` | 설정 (LLMConfig, EmbeddingConfig) |
| `src/errors.py` | 에러 분류 체계 |
| `src/logger.py` | 구조화 JSON 로깅 |
| `src/llm_pipeline/model.py` | LLM 호출 (재시도 로직 포함) |
| `src/llm_pipeline/schemas.py` | Pydantic 출력 스키마 |
| `src/embedder.py` | AWS Bedrock 임베딩 |
| `src/qdrant_config.py` | Qdrant 벡터 DB |
| `src/preprocessing/base.py` | 텍스트 정제 (개선된 라인 필터링) |
| `src/preprocessing/parsing.py` | HTML 파싱 |

## Completed Improvements (2025-01-10 BITE-107)

### Commits
1. `4241e82` - clarify queue retention logic for failed embeddings
2. `bc942e3` - add LLM retry logic with exponential backoff
3. `343db09` - fix SQL injection vulnerability with parameterized queries
4. `b035d35` - introduce error classification system
5. `0004c9c` - improve preprocessing to preserve short but important lines
6. `e7912fa` - add structured JSON logging for CloudWatch
7. `1eebc33` - consolidate LLM settings with dataclass config

### Key Changes
- **재시도 로직**: Exponential backoff (3회, 최대 10초 대기)
- **에러 분류**: ProcessingError with category/severity
- **Temperature**: GPT-5-nano는 temperature=1만 지원
- **SQL Injection 방어**: Parameterized queries
- **전처리 개선**: 짧은 라인 중 중요 패턴 보존
- **구조화 로깅**: JSON format for CloudWatch
- **설정 통합**: LLMConfig/EmbeddingConfig dataclass

### Strategy
- 폴백 메커니즘 제외 (GPT-5/Bedrock 성능 차이 큼)
- 실패 시 queue에 유지 → 다음 Lambda에서 자동 재처리

## Local Docs
분석 결과는 `docs/` 폴더에 마크다운으로 저장됨 (gitignore됨):
- `docs/01-architecture-analysis.md` - 아키텍처 분석
- `docs/02-issues-found.md` - 발견된 문제점
- `docs/03-improvement-roadmap.md` - 개선 로드맵

## Commands
```bash
# 로컬 실행
./local_run.sh

# Docker 빌드
docker build -t harvest-post .
```
