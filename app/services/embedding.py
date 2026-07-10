"""03-embedding.md — 임베딩 슬롯 (단계 ①). ⚠️ 실제 모델 미준비 상태.

- StubEmbedder: 데모용. 텍스트 해시 기반 가짜 벡터 (의미 없음, 파이프라인 배선 확인용)
- SentenceTransformerEmbedder: EmbeddingGemma 슬롯. 모델이 준비되면
  ① uv sync --extra ml
  ② Hugging Face 라이선스 동의 + hf auth login (01-setup.md §5)
  ③ .env 에 EMBEDDING_BACKEND=sentence-transformers
  만 하면 활성화된다.

실시간 운영 원칙: 새 글만 1회 임베딩 후 DB 저장, 재임베딩 금지 (03 문서).
"""
import hashlib
from typing import Protocol

import numpy as np

from app.config import settings


class Embedder(Protocol):
    ready: bool
    name: str

    def encode(self, texts: list[str]) -> np.ndarray:
        """texts → (N, dim) 정규화된 벡터"""
        ...


class StubEmbedder:
    """모델 슬롯이 비어 있을 때의 대체물.

    같은 단어를 공유하는 글이 가까운 벡터를 갖도록 단어 해시를 합산한다.
    데모/배선 확인용일 뿐, 의미 유사도를 잡지 못한다 — 실제 모델로 교체할 것.
    """

    ready = False  # "실제 모델 아님"을 상태 API에 노출
    name = "stub (모델 미준비 — 단어 해시 기반 데모 벡터)"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    def _word_vec(self, word: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.md5(word.encode()).digest()[:4], "little")
        rng = np.random.default_rng(seed)
        return rng.standard_normal(self.dim).astype(np.float32)

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = []
        for text in texts:
            words = text.split() or [""]
            v = np.sum([self._word_vec(w) for w in words], axis=0)
            norm = np.linalg.norm(v)
            vecs.append(v / norm if norm > 0 else v)
        return np.asarray(vecs, dtype=np.float32)


class SentenceTransformerEmbedder:
    """sentence-transformers 호환 임베딩 (KURE-v1 / EmbeddingGemma / BGE 등)."""

    ready = True

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # uv sync --extra ml

        # 모듈/인스턴스 수준에서 1회만 로딩 (요청마다 로딩 금지 — 07 문서)
        self._model = SentenceTransformer(settings.embedding_model)
        self.name = settings.embedding_model

    def encode(self, texts: list[str]) -> np.ndarray:
        # EmbeddingGemma 등 일부 모델만 용도별 프롬프트 지원 — 있을 때만 사용
        # (KURE-v1, BGE-M3 계열은 프롬프트 없이 인코딩)
        kwargs = {}
        if "Clustering" in (getattr(self._model, "prompts", None) or {}):
            kwargs["prompt_name"] = "Clustering"
        return self._model.encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,   # 클러스터링 전 정규화 권장
            **kwargs,
        )


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        if settings.embedding_backend == "sentence-transformers":
            _embedder = SentenceTransformerEmbedder()
        else:
            _embedder = StubEmbedder()
    return _embedder
