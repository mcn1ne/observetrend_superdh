# TODO — 주제 묶기 행렬화 (3만 글/일 대비 선제 최적화)

> 상태: **보류 (2026-07-11 문서화만)** — 하루 유입이 수천 건대를 넘보기 시작하면 착수.
> 결과(묶음 구성)는 완전히 동일해야 하는 순수 성능 개선. 파일 1개, 함수 1개 교체.
>
> ⚠️ **2026-07-12 갱신**: `_greedy_groups`가 average-link(비정규화 평균 내적)로
> 바뀌었다 — 눈덩이 블롭 사고 수정 (구현 이력은 함수 docstring 참고). 행렬화에는
> 오히려 유리: 아래 설계 2번의 "누적합 후 정규화" 단계에서 **정규화가 아예 필요
> 없어져** `C[j] = S[j] / len(members[j])` 나눗셈만 남는다 (동치 증명도 단순해짐).
> norm=0 폴백도 불필요. 벤치 스니펫의 문턱은 0.70이 아니라 현행 0.60으로 돌릴 것.

## 왜 필요한가 (실측 근거)

주제 묶기 `_greedy_groups`(`app/services/topics.py`)는 매분 24h 창 전체를 다시 묶는데,
안쪽이 "글 1개 ↔ 묶음 중심 1개" 파이썬 반복문이라 규모에 제곱적으로 느려진다.

실측 (2026-07-11, 실제 함수 + 합성 군집 데이터, 최악 조건):

| 창 내 글 수 | 소요 |
|---|---|
| 1,500 (현재 수준) | 0.28초 ✅ |
| 5,000 | 3.2초 |
| 15,000 | 33초 ⚠️ |
| 30,000 | **134초** ❌ (60초 분석 주기 붕괴) |

참고: 실데이터는 묶임이 더 잘 일어나 이보다 빠르지만 증가 추세는 동일.
동반 부하: 매분 3만 행 topic_id 갱신(set_topic_assignments), 3만×1024 float32(≈123MB) 스택.

## 설계 — 동일 의미론을 유지하는 부분 행렬화

`_greedy_groups`만 교체 (시그니처·호출부·반환부 불변):

1. **안쪽 루프 제거**: 묶음 중심을 (G × 1024) numpy 행렬 `C`로 유지,
   글마다 `sims = C[:g] @ v` 행렬-벡터 곱 1번으로 전 묶음 동시 비교.
   - 의미론 동치 근거: 기존 코드는 `sim > best_sim`(엄격 초과)이라 "먼저 나온 최대"가
     이긴다 → `np.argmax`(첫 최대 반환) + `sims[j] > threshold` 판정이 정확히 동일.
2. **중심 갱신을 누적합으로**: 기존은 편입마다 `np.mean(embeddings[members])` 재합산
   (묶음 클수록 제곱 비용). 누적합 `S[j] += v` 후 정규화로 교체.
   - 동치 근거: 정규화(단위벡터화)하면 평균과 합은 같은 방향 → 동일 중심.
     norm=0 엣지케이스만 기존과 같게 `S/len(members)` 폴백.
3. **용량 두 배 증식**: C·S는 cap 초과 시 2배 재할당 (리스트 append 제거).

예상 효과: 30k에서 134초 → 수 초 (BLAS/멀티코어). 추가 여지: 창 축소(24h→6~12h),
topic_min_size 4→8 상향도 병행 가능한 독립 레버.

## 착수 시 검증 절차 (그대로 따라 하면 됨)

1. **백업**: git 없음 → `cp app/services/topics.py app/services/topics.py.bak-행렬화전`
2. **동등성**: 기존 구현을 검증 스크립트에 legacy로 복사해 두고,
   - 합성 데이터(군집 구조, 시드 3개 × 1.5k/5k)에서 그룹 구성 완전 일치 assert
   - 실데이터(DB 24h 창 임베딩, read-only)에서 old vs new 그룹 구성 일치 assert
   - 부동소수 미세 차이로 경계 글 1~2개가 갈리면: 차이 건수를 보고하고 수용 여부 판단
3. **벤치마크**: 1.5k/5k/15k/30k 재측정 — 목표 30k 수 초 이내
4. **회귀**: 서버 재기동 → analyze 사이클 정상, 주제 수·이름 연속성 유지,
   스냅샷 stats.duration_sec 이전과 비슷하거나 개선 확인

## 벤치마크 재현 스니펫

```bash
cd ~/TrendSys/game-board-pipeline && uv run python - <<'PY'
import time, numpy as np
from app.services.topics import _greedy_groups
def synth(n, nc, dim=1024, seed=0):
    rng = np.random.default_rng(seed)
    c = rng.normal(size=(nc, dim)).astype(np.float32); c /= np.linalg.norm(c, axis=1, keepdims=True)
    v = c[rng.integers(0, nc, n)] + rng.normal(scale=0.25, size=(n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)
for n, nc in [(1500,40),(5000,120),(15000,350),(30000,700)]:
    v = synth(n, nc); t0=time.time(); g=_greedy_groups(v, 0.70)
    print(f"{n:>6}글 → 묶음{len(g):>4} {time.time()-t0:6.2f}s")
PY
```
