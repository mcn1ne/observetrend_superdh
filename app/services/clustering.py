"""04-clustering.md — 클러스터링 슬롯 (단계 ②). ⚠️ 실제 백엔드 미준비 상태.

- StubClusterer: 데모용. 코사인 유사도 탐욕 묶기 (외부 의존성 없음)
- HDBSCANClusterer: 준비되면 .env 에 CLUSTERING_BACKEND=hdbscan
  (uv sync --extra ml 로 scikit-learn 설치 필요. 개수 자동, 애매한 글은 -1 노이즈)

반환 규약(공통): labels — 글 i의 묶음 번호 배열, -1 은 노이즈.
⚠️ 묶음 번호는 실행마다 새로 매겨진다. 번호로 주제를 추적하지 말 것 (08 §6).
"""
from collections import defaultdict
from typing import Protocol

import numpy as np

from app.config import settings


class Clusterer(Protocol):
    ready: bool
    name: str

    def fit_predict(self, embeddings: np.ndarray) -> np.ndarray: ...


class StubClusterer:
    """모델 슬롯 대체물: 중심 벡터와의 코사인 유사도로 탐욕적으로 묶는다.

    HDBSCAN 처럼 묶음 개수를 미리 정하지 않고, min_cluster_size 미만 묶음과
    어디에도 안 붙는 글은 -1(노이즈)로 뺀다. 배선 확인용 — HDBSCAN으로 교체할 것.
    """

    ready = False
    name = "stub (미준비 — 코사인 탐욕 묶기)"
    SIM_THRESHOLD = 0.60  # 이 유사도 이상이면 같은 묶음으로 (데모용 고정값)

    def fit_predict(self, embeddings: np.ndarray) -> np.ndarray:
        n = len(embeddings)
        labels = np.full(n, -1, dtype=int)
        centroids: list[np.ndarray] = []
        members: dict[int, list[int]] = defaultdict(list)

        for i, v in enumerate(embeddings):
            best, best_sim = -1, self.SIM_THRESHOLD
            for c_idx, c in enumerate(centroids):
                sim = float(np.dot(v, c))
                if sim > best_sim:
                    best, best_sim = c_idx, sim
            if best == -1:
                centroids.append(v.copy())
                best = len(centroids) - 1
            members[best].append(i)
            # 중심 갱신 + 정규화
            c = np.mean(embeddings[members[best]], axis=0)
            norm = np.linalg.norm(c)
            centroids[best] = c / norm if norm > 0 else c

        for c_idx, idxs in members.items():
            if len(idxs) >= settings.min_cluster_size:
                for i in idxs:
                    labels[i] = c_idx
        return labels


class HDBSCANClusterer:
    """HDBSCAN — 준비되면 이 클래스가 활성화 (scikit-learn 1.3+ 내장판 사용)."""

    ready = True

    def __init__(self) -> None:
        from sklearn.cluster import HDBSCAN  # uv sync --extra ml

        self._cls = HDBSCAN
        self.name = f"HDBSCAN (min_cluster_size={settings.min_cluster_size})"

    def fit_predict(self, embeddings: np.ndarray) -> np.ndarray:
        clusterer = self._cls(
            min_cluster_size=settings.min_cluster_size,  # 튜닝 포인트 (3/5/10 비교)
            metric="euclidean",  # 정규화된 벡터(03)라 유클리드로 충분
        )
        return clusterer.fit_predict(embeddings)


def group_by_label(labels: np.ndarray) -> dict[int, list[int]]:
    """묶음별 글 인덱스 모으기 (-1 노이즈 제외)."""
    clusters: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        if label != -1:
            clusters[int(label)].append(idx)
    return dict(clusters)


_clusterer: Clusterer | None = None


def get_clusterer() -> Clusterer:
    global _clusterer
    if _clusterer is None:
        if settings.clustering_backend == "hdbscan":
            _clusterer = HDBSCANClusterer()
        else:
            _clusterer = StubClusterer()
    return _clusterer
