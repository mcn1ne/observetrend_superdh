"""05-summarization.md — 요약 슬롯 (단계 ③).

출력 형식은 ④단계 판단 모델(adapters3)의 학습 입력 형식에 맞춘다:
    "{주제 라벨}___{대표 제목}___{요약}"
이를 위해 요약 결과를 {label, title, summary} 구조로 반환한다.

- StubSummarizer: 추출식 (중심에 가까운 대표 제목 나열). 생성 모델 없이 동작.
- MLXSummarizer: 보유 4B(Gemma 4 E4B)에 프롬프트로 요약 요청 (문서 권장 A안).
  .env 에 SUMMARIZER_BACKEND=mlx 로 활성화.
"""
import re
from typing import Protocol

import numpy as np

from app.config import settings

MAX_POSTS_IN_PROMPT = 15  # 묶음이 크면 대표 글만 샘플링 (길이 제한/비용 관리)


def _representative_order(posts: list[dict], embeddings: np.ndarray | None) -> list[int]:
    """중심 벡터에 가까운 순서로 글 인덱스 정렬 (대표 글 샘플링용)."""
    if embeddings is None or len(embeddings) != len(posts):
        return list(range(len(posts)))
    c = np.mean(embeddings, axis=0)
    norm = np.linalg.norm(c)
    if norm > 0:
        c = c / norm
    sims = embeddings @ c
    return list(np.argsort(-sims))


def _body_snippet(post: dict, limit: int | None = None) -> str:
    """요약 프롬프트용 본문 조각 — text는 '제목+본문' 합본이므로 제목 중복을 제거.

    기존 저장분에 남은 '첨부' 플레이스홀더도 표시 단계에서 걷어낸다
    (신규 글은 preprocess 에서 원천 제거됨).
    """
    from app.services.preprocess import clean_text

    text = post.get("text") or ""
    ct = clean_text(post.get("title") or "")
    if ct and text.startswith(ct):
        text = text[len(ct):].strip()
    text = re.sub(r"(?:^|\s)첨부(?=\s|$)", " ", text).strip()
    return text[: limit or settings.summary_snippet_chars]


class Summarizer(Protocol):
    ready: bool
    name: str

    def summarize(self, posts: list[dict], embeddings: np.ndarray | None = None) -> dict:
        """→ {label, title, summary}"""
        ...


class StubSummarizer:
    """생성 모델 없이 동작하는 추출식 요약 — 대표 제목 기반."""

    ready = False
    name = "stub (추출식 — 대표 제목 나열)"

    def summarize(self, posts: list[dict], embeddings: np.ndarray | None = None) -> dict:
        order = _representative_order(posts, embeddings)
        rep_title = posts[order[0]]["title"]
        top_titles = []
        for i in order:
            t = posts[i]["title"]
            if t not in top_titles:
                top_titles.append(t)
            if len(top_titles) >= 3:
                break
        return {
            "label": rep_title,
            "title": rep_title,
            "summary": f"관련 글 {len(posts)}건. 주요 글: " + " / ".join(top_titles),
        }


class MLXSummarizer:
    """보유 4B 프롬프트 요약 (05 문서 A안). 베이스 모델은 분류기와 공유 로딩."""

    ready = True

    def __init__(self) -> None:
        self.name = f"mlx ({settings.summarizer_model})"

    def summarize(self, posts: list[dict], embeddings: np.ndarray | None = None) -> dict:
        from app.services.mlx_runtime import generate_text

        order = _representative_order(posts, embeddings)[:MAX_POSTS_IN_PROMPT]
        listing = "\n".join(
            f"{n}. {posts[i]['title']}" + (f" — {snippet}" if (snippet := _body_snippet(posts[i])) else "")
            for n, i in enumerate(order, 1)
        )
        from app.services.gemma_analyze import DOMAIN_CONTEXT

        prompt_text = (
            DOMAIN_CONTEXT + "\n\n"
            "아래는 같은 주제로 묶인 이 갤러리의 글 모음이다.\n"
            "이 묶음이 무슨 주제이고 사람들이 어떤 반응/불만/요청을 하는지 요약하라.\n"
            "반어·밈·상투적 푸념은 실제 사건·피해 제보와 구분해서 서술하라 "
            "(예: '환불 언급이 있으나 냉소적 푸념 수준' vs '실제 미지급 제보 다수').\n"
            "반드시 아래 3줄 형식으로만 답하라.\n"
            "주제: (10자 내외 주제 라벨)\n"
            "대표제목: (가장 대표적인 글 제목 1개)\n"
            "요약: (3~5문장 요약)\n\n"
            f"[게시글들]\n{listing}"
        )
        out = generate_text(
            settings.summarizer_model,
            [{"role": "user", "content": prompt_text}],
            max_tokens=settings.summarizer_max_tokens,
        )
        # 사고 채널/특수 토큰 정리 — 오염된 원문이 ④판단 입력에 그대로
        # 들어가면 안 된다. 최종답이 없으면 추출식으로 폴백.
        from app.services.mlx_runtime import extract_final_channel

        out = extract_final_channel(out)
        if not out.strip():
            return StubSummarizer().summarize(posts, embeddings)

        rep_title = posts[order[0]]["title"]
        result = {"label": rep_title, "title": rep_title, "summary": out.strip()}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("주제:"):
                result["label"] = line[3:].strip()
            elif line.startswith("대표제목:"):
                result["title"] = line[5:].strip()
            elif line.startswith("요약:"):
                result["summary"] = line[3:].strip()
        return result


_summarizer: Summarizer | None = None


def get_summarizer() -> Summarizer:
    global _summarizer
    if _summarizer is None:
        if settings.summarizer_backend == "mlx":
            _summarizer = MLXSummarizer()
        else:
            _summarizer = StubSummarizer()
    return _summarizer
