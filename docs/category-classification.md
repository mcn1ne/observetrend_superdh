# 카테고리 분류 방식 변경 기록

## 현재 방식 — adapters4 1회 호출

새 글은 임베딩 저장 후 adapters4 워커가 한 번 읽는다.

```text
제목 + 본문
  → Gemma 4 E4B + adapters4
  → topic_label___major___sentiment
  → major + topic_label 규칙 매핑
  → 영속 카테고리 배정
```

| adapters4 major | 카테고리 |
|---|---|
| 콘텐츠 | 콘텐츠·공략 |
| 운영 | 운영·이벤트 |
| 밸런스 | 밸런스 |
| 과금 | 과금 |
| 버그 | 버그·오류 |

`major=일반`은 `topic_label`에 캐릭터·헌터·육성·장비·세팅·덱·스킬·무기·
아티팩트·돌파·초월·강화·조합 관련 표현이 있으면 `캐릭터·장비·덱`, 아니면
`일반·잡담`으로 배정한다.

장점은 글당 Gemma 호출이 adapters4 한 번뿐이고, 모든 게임에서 카테고리의
추상화 수준이 일정하며, 자유형 이름 증가와 `유머·잡담` 블랙홀을 막는다는 점이다.
세부 이슈명은 카테고리가 아니라 adapters4의 `topic_label`과 벡터 주제가 담당한다.

## 이전 방식 — 벡터 빠른 경로 + Gemma 4 기본형

2026-07-11 이전 라이브 분류 방식이다. 구현은 유지보수·비교 실험을 위해
`app/services/categorize.py`의 `assign_posts()`와 `MLXCategorizer`에 남겨 둔다.

```text
새 글 임베딩
  → 기존 카테고리 중심과 코사인 유사도 비교
  → 0.80 이상: 최근접 카테고리 즉시 배정
  → 0.80 미만: Gemma 4 기본형 호출
       ├─ 기존 후보 최대 5개 중 하나 선택
       └─ 맞는 후보가 없으면 자유형 신규 카테고리 생성
```

카테고리 중심은 배정 글의 이동 평균으로 갱신하고 50건에서 동결했다. 벡터 빠른
경로는 실측상 대분류 판단용이 아니라 근사 중복·도배 글의 지름길이었다.

이전 방식의 장점은 데이터에서 새로운 카테고리 이름을 자유롭게 만들 수 있다는
점이다. 단점은 기본형과 adapters4가 글당 각각 호출될 수 있고, 콜드스타트에서
`유머·잡담` 같은 넓은 초기 카테고리가 후속 글을 흡수하며, `길드스킬 질문`처럼
지나치게 좁은 이름과 큰 분류가 같은 계층에 섞일 수 있다는 점이다.

## 이전 방식으로 복원하는 위치

`app/services/pipeline.py`의 `collect_step()`에서 임베딩 후 게임별
`load_posts_without_category()` → `assign_posts()` 호출을 되살리고,
`app/services/gemma_analyze.py`의 `assign_from_analysis()` 호출을 제거하면 된다.
설정의 `categorizer_backend`, `categorizer_model`, `category_assign_sim`,
`category_candidates_k`, `category_centroid_max_n`은 비교 실험과 복원용으로 유지한다.

## 배선 위치

adapters4 분석·카테고리 배정은 `app/main.py`의 `gemma_worker_loop()`에서 돈다.
이 루프는 수집·임베딩을 담당하는 `pipeline_loop()`와 **독립적**이며, 게임별로
라운드로빈하며 큐가 남아있는 동안 쉬지 않는다. `settings.gemma_analysis_enabled`
(기본 `True`)를 `False`로 두면 이 루프 자체가 시작하지 않아 분석·카테고리 배정이
전부 멈춘다(수집·임베딩은 계속됨).

## 유지보수 도구

- **`POST /api/maintenance/reclassify`** (`categorize.reclassify_window`) — 현재
  카테고리 체계를 고정해두고 창 내 글을 기본형 Gemma로 배치 재분류한다. 콜드스타트
  오배정 교정용이며, `max_sim` 지정 시 자기 카테고리 중심과 유사도가 낮은 의심 글만
  대상으로 한다(경험상 하위 ~20%가 0.62 부근). adapters4 경로와 독립적으로 지금도
  쓰인다 — 규칙매핑 자체가 틀렸을 때가 아니라 개별 글의 오배정을 잡을 때 사용.
- **`scripts/rebuild_categories_from_adapters4.py`** — `MAJOR_CATEGORY_MAP`/키워드
  규칙을 바꾼 뒤, Gemma를 다시 부르지 않고 이미 저장된 `post_analysis`(adapters4
  결과)만으로 게임별 카테고리를 멱등 재구성한다. `uv run python
  scripts/rebuild_categories_from_adapters4.py`로 실행.

## 운영 주의

- adapters4의 시스템 프롬프트와 `제목\n본문` 입력 형식은 학습 시 형식을 유지한다.
- adapters4 파싱에 실패한 글은 최대 3회 재시도하며, 성공하기 전에는 카테고리가 없다.
- 키워드 규칙 변경 시 기존 글은 자동 재분류되지 않으므로 `rebuild_categories_from_adapters4.py`로 별도 재분류가 필요하다.
- 모델을 바꾸면 대표 표본으로 회귀 검증한다.
