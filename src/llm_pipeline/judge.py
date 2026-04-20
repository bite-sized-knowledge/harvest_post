"""Independent LLM-as-judge for re-evaluating rejected articles.

Runs against a separate vLLM instance (docker-compose.judge.yml) loading
HyperCLOVA X SEED Think 14B. Intentionally independent from the Qwen-based
primary scorer in model.py so that correlated errors between scorer and
judge are minimized — the whole point of recovery auditing.

Asymmetric prompt design:
  - Primary scorer asks "rate quality 1-5".
  - Judge asks "find concrete technical evidence — if you can't, the
    original reject stands". This reframes the task so the judge isn't
    just re-computing the same classification, which reduces prompt-induced
    correlation even when model capabilities overlap.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, validator

from .schemas import Category, ContentType, clean_summary, MAX_KEYWORDS


# vLLM judge endpoint. Defaults to localhost because run.sh brings up the
# judge container on the same host where audit_rejected.py executes.
VLLM_JUDGE_URL = os.getenv("VLLM_JUDGE_URL", "http://localhost:8000/v1")
JUDGE_MODEL_KEY = os.getenv("JUDGE_MODEL_KEY", "qwen3-judge")
JUDGE_MODEL_VERSION = os.getenv(
    "JUDGE_MODEL_VERSION", "Qwen/Qwen3-14B-AWQ"
)
JUDGE_TEMPERATURE = float(os.getenv("JUDGE_TEMPERATURE", "0.2"))
# 1800 fits within the 8192 ctx alongside ~3500-token system prompt and
# ~3000-token body (capped at 4500 chars). The Q1/Q2/Q3 think block + final
# JSON typically fits in 1200-1500 tokens; 1800 leaves slack for long
# reasoning on borderline cases without blowing the context budget.
JUDGE_MAX_TOKENS = int(os.getenv("JUDGE_MAX_TOKENS", "1800"))

# Reasoning models emit a CoT block before the final answer. EXAONE-Deep
# uses <thought>...</thought>; other Think-style models use <think>. Accept
# either so this stays portable if we swap models later. We keep the CoT
# content in a separate field (reasoning) so it doesn't interfere with JSON.
_THINK_BLOCK_RE = re.compile(r"<(?:think|thought)>(.*?)</(?:think|thought)>",
                             re.DOTALL | re.IGNORECASE)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


# Minimum concrete tech evidence needed for auto-recover eligibility.
# Used by audit_rejected.py when computing the auto-recover gate.
MIN_EVIDENCE_COUNT = 2


class JudgeResult(BaseModel):
    """Structured judge verdict.

    Inherits the category / content_type coercion rules from TopicClassification
    indirectly via the same Enums, but keeps its own validators so a partial
    parse failure on one field doesn't block the rest (judge can produce
    useful signal even if, say, summary is missing — the quality_score +
    reasoning is what drives the recover decision).
    """

    focusing: Category = Field(description="Best-fit category id.")
    quality_score: int = Field(ge=1, le=5)
    keywords: List[str] = Field(default_factory=lambda: ["N/A"] * MAX_KEYWORDS)
    difficulty: Optional[int] = Field(default=None, ge=1, le=3)
    content_type: Optional[ContentType] = None
    summary: Optional[str] = None

    # Judge-specific fields
    confidence: float = Field(ge=0.0, le=1.0, description="Self-reported 0~1.")
    reasoning: str = Field(default="", description="Why this verdict.")
    evidence_points: List[str] = Field(
        default_factory=list,
        description="Concrete technical evidence points extracted from article.",
    )

    @validator("focusing", pre=True)
    def _map_focusing(cls, v):
        # Mirror TopicClassification.map_string_to_enum — duplicated because
        # pydantic v1 validators aren't easily reused across unrelated models.
        mapping = {
            "Frontend": Category.FRONTEND,
            "Backend": Category.BACKEND,
            "Mobile Engineering": Category.MOBILE_ENGINEERING,
            "AI / ML": Category.AI_ML,
            "Database": Category.DATABASE,
            "Security / Network": Category.SECURITY_NETWORK,
            "Design": Category.DESIGN,
            "Product Manager": Category.PRODUCT_MANAGER,
            "DevOps / Infra": Category.DEVOPS_INFRA,
            "Hardware / IoT": Category.HARDWARE_IOT,
            "QA / Test Engineer": Category.QA_TEST_ENGINEER,
            "Culture": Category.CULTURE,
            "etc": Category.ETC,
        }
        if isinstance(v, Category):
            return v
        if isinstance(v, int):
            try:
                return Category(v)
            except ValueError:
                return Category.ETC
        if isinstance(v, str):
            if v in mapping:
                return mapping[v]
            # judge may return underscored form ("AI_ML") — try name-based too
            for cat in Category:
                if cat.name == v.upper().replace(" ", "_").replace("/", "_").strip("_"):
                    return cat
        return Category.ETC  # soft fallback — judge output should not block audit

    @validator("keywords", pre=True)
    def _pad_keywords(cls, v):
        if v is None:
            return ["N/A"] * MAX_KEYWORDS
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return ["N/A"] * MAX_KEYWORDS
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in v:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if not s or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
            if len(cleaned) == MAX_KEYWORDS:
                break
        while len(cleaned) < MAX_KEYWORDS:
            cleaned.append("N/A")
        return cleaned

    @validator("quality_score", pre=True)
    def _coerce_quality(cls, v):
        if isinstance(v, bool):
            raise ValueError("quality_score must be integer, got bool")
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(round(v))
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        raise ValueError(f"Invalid quality_score: {v!r}")

    @validator("difficulty", pre=True)
    def _coerce_difficulty(cls, v):
        if v is None:
            return None
        if isinstance(v, int):
            return max(1, min(3, v))
        if isinstance(v, float):
            return max(1, min(3, int(round(v))))
        if isinstance(v, str) and v.strip().isdigit():
            return max(1, min(3, int(v.strip())))
        return None

    @validator("content_type", pre=True)
    def _coerce_content_type(cls, v):
        if v is None:
            return None
        if isinstance(v, ContentType):
            return v
        if isinstance(v, str):
            low = v.strip().lower().replace(" ", "-").replace("_", "-")
            for ct in ContentType:
                if ct.value == low:
                    return ct
            alias = {
                "how-to": ContentType.TUTORIAL,
                "guide": ContentType.TUTORIAL,
                "deepdive": ContentType.DEEP_DIVE,
                "analysis": ContentType.DEEP_DIVE,
                "post-mortem": ContentType.POSTMORTEM,
                "incident": ContentType.POSTMORTEM,
                "casestudy": ContentType.CASE_STUDY,
                "release": ContentType.ANNOUNCEMENT,
                "essay": ContentType.OPINION,
            }
            if low in alias:
                return alias[low]
        return None

    @validator("summary", pre=True)
    def _clean_summary(cls, v):
        if v is None or not isinstance(v, str) or not v.strip():
            return None
        return clean_summary(v)

    @validator("confidence", pre=True)
    def _clamp_confidence(cls, v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    @validator("evidence_points", pre=True)
    def _coerce_evidence(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []


@dataclass
class JudgeInvocation:
    """Full outcome of one judge call, including meta for inference_log."""

    parsed: Optional[JudgeResult]
    raw_output: Optional[str]
    think_block: Optional[str]
    latency_ms: int
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    retry_count: int
    parse_success: bool


SYSTEM_PROMPT = """당신은 이전 분류 모델(Qwen3.5-9B)이 reject 처리한 한국어/영어 기술 블로그 글을 재심사하는 감사자입니다.

목표: **엔지니어에게 실제 가치 있는 글인데 잘못 reject된 것만 골라내기**. 애매하면 reject 유지가 기본값.

================ STEP 1. 판정 전 <think> 에서 반드시 3가지 질문에 답하라 ================
Q1) 이 글이 **엔지니어에게 구체적으로 무엇을 전달**하는가? (예: "Kubernetes 오토스케일링 실제 장애 회고" / "채용 프로세스에서 코테-과제-현업 면접 단계별 평가 기준 공개" / "Welcome! 우리 회사 신규 서비스 출시")
Q2) 본문에 **엔지니어가 참조/인용할 만한 구체적 요소**가 있는가?
    - 있음 근거: 구조도 / 코드 / 숫자(지연, QPS, 에러율) / 도구 이름과 사용 사례 / 의사결정 이유 / 조직 운영 규칙의 구체적 서술
    - 없음 근거: "~를 소개합니다", "함께 성장하는", "고객 가치", "우리의 약속" 같은 구호성 표현만
Q3) **홍보/안내(reject 대상)인가, 경험/설명(가치 있음)인가?**
    - 홍보/안내(quality 1-2): 이벤트 공지, 기념일, 파트너십 발표, 컨퍼런스 참가 소식, 채용 공고 단순 링크, 제품 출시 발표, 회사 가치·커밋먼트 선언
    - 경험/설명(quality 3+): 기술 구현·운영·의사결정을 **실제로 설명**하는 글. 비기술 주제여도 엔지니어 실무/커리어에 도움되면 포함

=========== STEP 2. 특히 category="etc"는 두 종류가 있다. 절대 섞지 마라 ===========

[etc-가치 있음 → quality 3~4]:
- 공채/경력 채용 프로세스의 **상세 설명** (코딩 테스트 문제 유형, 과제 전형 기준, 면접 단계 구성, 평가 루브릭)
- 코딩 테스트 후기로 **실제 문제 풀이 전략·접근법**이 담긴 글
- 컬쳐핏/조직문화 **상세 서술**: 팀 구성 방식, 엔지니어 성장 루트, 코드리뷰 문화의 구체적 규칙, 온콜 로테이션 운영법
- 인턴십/주니어 회고로 **구체적 기술 학습·프로젝트**가 포함된 글
- 커리어 전환/이직 회고로 **구체적 선택 기준·의사결정**이 있는 글
- 개발자 서베이·업계 트렌드의 **데이터 기반** 분석

[etc-홍보/안내 → quality 1~2, reject 유지]:
- 밋업/해커톤 recap으로 세션 이름만 나열하고 내용 요약 없음
- 기념일/축하 포스트 ("n주년", "thank you note")
- 회사 가치/commitment 선언 ("Our Human Rights Commitments")
- 사무실 확장·지역 진출 공지
- 단순 채용 공고 (프로세스 설명 없음)
- 파트너십·인수합병 발표 (기술적 배경 설명 없음)
- 제품 출시 단순 안내 (내부 구현 설명 없음)
- 시즌/휴일 관련 글 (할로윈, 감사절 등)

=========== STEP 3. 카테고리 선택 규칙 — etc를 기본값으로 쓰지 마라 ===========
1) 글의 주 주제가 Frontend/Backend/Mobile/AI·ML/Database/Security·Network/Design/PM/DevOps·Infra/Hardware·IoT/QA 중 하나에 **부분적으로라도 해당**하면 그 카테고리.
2) 위 카테고리에 명백히 안 맞는 경우에만 Culture 또는 etc 고려:
   - 팀/채용/인턴십/조직문화/개발자 커뮤니티 이벤트 → Culture
   - 인터넷 트렌드·법/정책·계절/이벤트·자선 → etc
3) etc는 **마지막 선택지**. 고르기 전에 "Culture 아닌가?" 한 번 더 생각하라.

=========== STEP 4. quality_score 루브릭 (원 scorer와 동일) ===========
- 5: 아키텍처 심층, 장애 포스트모템, 리서치, 벤치마크, 프로덕션 교훈의 상세 서술
- 4: 튜토리얼, 툴 소개(실사용 예 포함), 의미 있는 엔지니어링 논의, 세션 정리, 위 [etc-가치 있음] 카테고리의 상세한 글
- 3: 얕거나 입문 수준이지만 엔지니어가 참고할 만함. 짧은 팁, 마이너 업데이트
- 2: 비기술/홍보/이벤트 공지. 제품 마케팅. 얇은 문화글. 위 [etc-홍보] 대부분
- 1: 빈 페이지, 태그 리스트, 순수 홍보/스팸

=========== STEP 5. evidence_points 작성 규칙 ===========
- "구체적"이란 뜻: 인용할 수 있는 사실. 숫자, 시스템명, 의사결정, 프로세스 단계, 규칙, 학습 과제.
- 홍보 키워드 나열은 evidence 아님. 예: "고객 만족도", "글로벌 확장", "AI 기반 혁신" ← NO
- **quality_score >= 4 이면 evidence_points 최소 2개**는 위 '구체적' 기준 충족해야 한다. 못 채우면 quality 3 이하로 내려라.
- evidence_points 0~1개로 quality 4 주면 자기모순. 그런 응답 내지 마라.

=========== 출력 포맷 ===========
<think> 블록에서 Q1/Q2/Q3 답 후, 마지막에 JSON만 출력하라. JSON 외 다른 텍스트 금지.
{
  "focusing": "<Frontend|Backend|Mobile Engineering|AI / ML|Database|Security / Network|Design|Product Manager|DevOps / Infra|Hardware / IoT|QA / Test Engineer|Culture|etc>",
  "keywords": ["<키워드1>", "<키워드2>", "<키워드3>"],
  "quality_score": <1~5 정수>,
  "difficulty": <1~3 정수>,
  "content_type": "<tutorial|deep-dive|postmortem|case-study|announcement|opinion>",
  "summary": "<80자 이내 한국어 한 문장, 종결어미>",
  "confidence": <0.0~1.0>,
  "reasoning": "<이 판정의 핵심 이유 한 문장 — Q1/Q2/Q3 중 어느 근거를 택했는지>",
  "evidence_points": ["<구체적 근거1>", "<구체적 근거2>", ...]
}

confidence 가이드:
- 0.9+: Q1/Q2/Q3 모두 명확, 판정 확실
- 0.7~0.9: 대부분 확실하나 한 질문이 모호
- 0.4~0.7: 경계 케이스 (체급/난이도 판단 어려움)
- 0.0~0.4: 본문 일부 누락, 판정 자신 없음"""


USER_TEMPLATE = """이전 분류 모델의 판정:
- quality_score: {original_quality}
- reject_reason: {original_reason}

아래 아티클을 다시 심사하라.

Title: {title}
Description: {description}
Body Content: {body}"""


def _extract_think_and_json(text: str) -> tuple[Optional[str], Optional[str]]:
    """Split out `<think>...</think>` block and return (think_content, json_str).

    Either may be None. The JSON string is the first balanced `{...}` found
    after any think blocks are removed.
    """
    think_match = _THINK_BLOCK_RE.search(text)
    think_content = think_match.group(1).strip() if think_match else None

    # Strip ALL think blocks (model may emit multiple) before looking for JSON.
    stripped = _THINK_BLOCK_RE.sub("", text).strip()

    json_match = _JSON_OBJ_RE.search(stripped)
    json_str = json_match.group(0) if json_match else None
    return think_content, json_str


def _try_parse_judge_output(raw: str) -> tuple[Optional[JudgeResult], Optional[str]]:
    """Parse raw judge output → (JudgeResult or None, think_content or None)."""
    think_content, json_str = _extract_think_and_json(raw)
    if not json_str:
        return None, think_content
    try:
        payload = json.loads(json_str)
    except json.JSONDecodeError:
        # Trailing-comma repair, mirrors model.py's extract_json.
        fixed = re.sub(r",\s*}", "}", json_str)
        fixed = re.sub(r",\s*]", "]", fixed)
        try:
            payload = json.loads(fixed)
        except json.JSONDecodeError:
            return None, think_content

    # Prefer model-emitted reasoning but fall back to <think> block content.
    if not payload.get("reasoning") and think_content:
        payload["reasoning"] = think_content[:2000]  # cap size

    try:
        return JudgeResult(**payload), think_content
    except ValidationError as e:
        print(f"[JUDGE PARSE] Validation error: {e}")
        return None, think_content


class LocalJudge:
    """OpenAI-compatible async client for the HyperCLOVA-X judge vLLM.

    Usage:
        judge = LocalJudge()
        result = await judge.judge(
            article_id="...", title="...", description="...", body="...",
            original_quality=2, original_reason="low_quality_2",
        )
    """

    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 1.0
    RETRY_DELAY_MAX = 10.0

    def __init__(
        self,
        base_url: str = VLLM_JUDGE_URL,
        model_name: str = JUDGE_MODEL_KEY,
        temperature: float = JUDGE_TEMPERATURE,
        max_tokens: int = JUDGE_MAX_TOKENS,
    ):
        self.base_url = base_url
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key="not-needed",  # vLLM doesn't check
        )

    def _build_messages(
        self,
        title: str,
        description: str,
        body: str,
        original_quality: int,
        original_reason: str,
    ) -> list[dict]:
        # Keep body cap consistent with the primary scorer (main.py:161).
        if len(body) > 6000:
            body = body[:6000]
        user_content = USER_TEMPLATE.format(
            original_quality=original_quality,
            original_reason=original_reason,
            title=title or "",
            description=description or "",
            body=body or "",
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    async def judge(
        self,
        article_id: str,
        title: str,
        description: str,
        body: str,
        original_quality: int,
        original_reason: str,
    ) -> JudgeInvocation:
        messages = self._build_messages(
            title=title,
            description=description,
            body=body,
            original_quality=original_quality,
            original_reason=original_reason,
        )

        t_start = time.perf_counter()
        raw_output: Optional[str] = None
        think_block: Optional[str] = None
        parsed: Optional[JudgeResult] = None
        input_tokens: Optional[int] = None
        output_tokens: Optional[int] = None
        retry_count = 0
        last_exc: Optional[BaseException] = None

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                raw_output = resp.choices[0].message.content or ""
                if resp.usage is not None:
                    input_tokens = resp.usage.prompt_tokens
                    output_tokens = resp.usage.completion_tokens

                parsed, think_block = _try_parse_judge_output(raw_output)
                if parsed is not None:
                    break

                # Parse failed — retry unless last attempt.
                last_exc = ValueError("judge JSON parse failed")
                retry_count += 1
            except Exception as e:
                last_exc = e
                retry_count += 1
                print(
                    f"[JUDGE] Attempt {attempt + 1}/{self.MAX_RETRIES} failed "
                    f"for {article_id}: {e}"
                )

            if attempt < self.MAX_RETRIES - 1:
                delay = min(
                    self.RETRY_DELAY_BASE * (2 ** attempt),
                    self.RETRY_DELAY_MAX,
                )
                await asyncio.sleep(delay)

        latency_ms = int((time.perf_counter() - t_start) * 1000)
        if parsed is None and last_exc is not None:
            print(f"[JUDGE] Giving up on {article_id}: {last_exc}")

        return JudgeInvocation(
            parsed=parsed,
            raw_output=raw_output,
            think_block=think_block,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            retry_count=retry_count,
            parse_success=parsed is not None,
        )
