"""08-realtime-detection.md — 실시간 확산 탐지 (열기 점수·버스트 판정).

- 열기 점수: 지수 감쇠 가중치 합 (§5) — 카테고리별 "시간별 점수"로 사용
- 버스트 판정: 비율 조건 + 절대량 조건 동시 적용 (§4)
- 쿨다운: 카테고리가 영속(id 고정)이 되면서 08 문서의 중심벡터 재추적 방식이
  필요 없어짐 → categories.last_alerted_at 시각 비교로 단순화 (pipeline.py)
"""
from datetime import datetime, timezone

from app.config import settings


# ── 시간 감쇠 (§5) ────────────────────────────────────────────────

def decay_weight(age_minutes: float) -> float:
    return 0.5 ** (age_minutes / settings.half_life_min)


def post_age_minutes(created_at: str, now: datetime) -> float:
    dt = datetime.fromisoformat(created_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((now - dt).total_seconds() / 60.0, 0.0)


def cluster_heat(created_ats: list[str], now: datetime | None = None) -> float:
    """묶음에 속한 글들의 감쇠 가중치 합 = '지금 열기'"""
    now = now or datetime.now(timezone.utc)
    return sum(decay_weight(post_age_minutes(c, now)) for c in created_ats)


# ── 버스트 판정 (§4) ──────────────────────────────────────────────

def is_burst(recent_count: float, total_24h: int) -> bool:
    """recent_count: 최근 창 글 수(또는 열기 점수) / total_24h: 24h 전체 글 수.

    "평소 시간당 양의 BURST_RATIO 배 이상이 최근 창에 몰렸고,
     절대량도 MIN_RECENT 건 이상이면 확산"
    """
    expected = total_24h / 24
    expected = max(expected, 0.5)  # 신규 주제(기준선 0) 나눗셈 오류 방지
    return (
        recent_count >= settings.min_recent
        and recent_count / expected >= settings.burst_ratio
    )
