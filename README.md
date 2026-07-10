# 게임 게시판 실시간 관제 시스템

`game-board-pipeline_기획/` 문서 세트 기반으로 만들고, **영속 카테고리 방식의
실시간 관제**로 확장한 시스템. 게시판 글을 1분 단위로 수집해 **카테고리에 적재하고
→ 시간별 점수를 매기고 → 확산을 탐지해 → 알림 여부를 판단**한다.

- 백엔드: **FastAPI + uv** / 프론트: **Vue 3 + Vite (MVVM)**
- 수집: **etl_dcinside/dcinside.db 연동** ✅ (DB1 → post_no 증분, 읽기 전용)
- ① 임베딩: **EmbeddingGemma** ✅ · ② 분류: **Gemma 4 하이브리드** ✅ · ④ 판단: **파인튜닝 4B (adapters3)** ✅
- 슬롯(미준비): 이미지 캡셔닝(stub) · 요약(stub 추출식)

> **Qdrant(etl_dcinside/qdrant_storage)를 안 쓰는 이유**: 그 벡터는 Gemini API
> (`gemini-embedding-2`) 공간이라 본 시스템의 EmbeddingGemma(로컬) 공간과 호환되지
> 않고, 커버리지도 일부이며 새 글마다 API 키·과금이 필요하다. 전량 로컬 재임베딩이
> 더 싸고 단순하다 (1,900건 ≈ 1분).

---

## 아키텍처 (영속 카테고리 하이브리드)

```
[DB1/수집원] ──매 1분──▶ 전처리(02) ─▶ DB2(SQLite) 적재
                              │
                              ▼
                  (이미지 있으면 캡셔닝 → 텍스트에 포함)
                              │
                              ▼
                  EmbeddingGemma 임베딩(03) — 새 글만 1회, 벡터 DB 저장
                              │
                              ▼
              ┌─ 카테고리 배정 (하이브리드) ──────────────────┐
              │ 1차: 기존 카테고리 중심벡터 유사도 ≥ 0.82     │
              │      → 즉시 적재 (LLM 호출 없음, 대부분의 글) │
              │ 2차: 미달 → Gemma 4 기본형이 후보 중 선택     │
              │      or 신규 카테고리 명명·생성               │
              └───────────────────────────────────────────────┘
                              │
                              ▼  (매 1분 분석)
        카테고리별 열기 점수(감쇠 가중합, 08) + 점수 이력 저장
                              │
                              ▼
        버스트 판정(08) → 급증 카테고리만 요약(05) → 파인튜닝 4B 판단(06)
                              │
                              ▼
        'O' 판정 → 카테고리별 쿨다운 확인 → 알림 발송 + 이력 기록
                              (UI에서 알림 카테고리는 상단 고정)

  * HDBSCAN(04)은 안전망: 재클러스터링으로 카테고리 중복(병합 후보)·누락 점검
```

**분류의 역할 분담 (실측으로 확정)**: **카테고리(대분류)의 주 배정자는 LLM**이다.
대분류 소속 판정은 의미 판단이라 벡터 기하로는 불가능함이 실측됨 — 글↔자기
카테고리 중심(0.661)과 글↔남의 중심(0.653)이 안 갈려서, 어떤 임계값에서도
통과율 ≈ 오배정률. 벡터 빠른 경로(`CATEGORY_ASSIGN_SIM=0.80`)는 사실상 같은
글(도배 등)만 통과시키는 지름길이다. 반면 **주제(topic)는 벡터 담당** — 조밀한
묶음은 기하로 정확히 잡힌다 (KURE-v1 기준 NN 순도 81%).
Gemma 분류 워커(`services/gemma_analyze.py`, 큐 기반 배치)가 모든 글을 읽고
카테고리 배정 + 감정·긴급도·요지를 기록한다. 폭증 시 큐에 쌓였다가 전부
소화되므로 유실이 없다.

**카테고리는 영속 자산이다.** 클러스터 번호(매 실행마다 바뀜)와 달리 id·이름·
중심벡터가 유지되므로, 쿨다운·점수 이력·기준선을 카테고리 단위로 안정적으로
추적한다. 중심벡터는 글이 배정될 때마다 이동 평균으로 갱신된다.

## 실행

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8007   # http://localhost:8007
cd frontend && npm run dev                                 # 프론트 개발 서버 (5173)
cd frontend && npm run build                               # 빌드 → FastAPI가 dist/ 서빙
```

API 문서: http://localhost:8007/docs

수집 원본 DB(dcinside.db)는 저장소에 없다. 별도로 준비해 `DCINSIDE_DB_PATH` 로
연결하며, 구조만 `schema/dcinside_source.sql` 에 남겨 두었다. 원본 없이 띄우려면
빈 DB를 만들어 물리거나 `COLLECTOR_BACKEND=mock` 을 쓴다.

```bash
sqlite3 dcinside.db < schema/dcinside_source.sql
```

파이프라인 자체 DB(`data/pipeline.db`)는 첫 실행 때 자동 생성된다.

## 화면 (Vue 3 · MVVM)

| 경로 | 내용 |
|---|---|
| `/` 실시간 관제 | **알림 카테고리 상단 고정** · 점수 보드(스파크라인) · 슬롯/다이얼 · 안전망 점검 버튼 |
| `/categories` | 카테고리 전체 (정렬: 알림 → 확산 → 점수) |
| `/alerts` | 발송 판정(O) + 발송 이력 |
| `/judge` | 파인튜닝 4B 단독 테스트 (오탐/미탐 점검) |

## 단계 ↔ 코드 ↔ 상태

| 단계 | 코드 | 상태 |
|---|---|---|
| 수집 | `services/collect.py` | ✅ dcinside.db 증분 (`DCINSIDE_BACKFILL_HOURS=72` 소급) / mock 전환 가능 |
| 전처리 (02) | `services/preprocess.py` | ✅ |
| 이미지 캡셔닝 | `services/caption.py` | 🔶 **슬롯** — `uv sync --extra vision` + `CAPTIONER_BACKEND=mlx-vlm` |
| ① 임베딩 (03) | `services/embedding.py` | ✅ EmbeddingGemma |
| ② 카테고리 분류 | `services/categorize.py` | ✅ 하이브리드 (벡터 + Gemma 4 명명) |
| ③ 요약 (05) | `services/summarize.py` | 🔶 stub 추출식 / `SUMMARIZER_BACKEND=mlx` |
| ④ 알림 판단 (06) | `services/judge.py` | ✅ 파인튜닝 4B (adapters3) |
| 점수·버스트 (08) | `services/detection.py` | ✅ 감쇠 반감기 30분 |
| 안전망 (04) | `services/clustering.py` + `recluster_check` | ✅ HDBSCAN — 병합 후보 제안 |
| 통합·루프 (07) | `services/pipeline.py`, `main.py` | ✅ |

## 주요 설정 (`.env` — 전체 키는 `app/config.py`)

| 키 | 기본값 | 의미 |
|---|---|---|
| `CATEGORY_ASSIGN_SIM` | 0.85 | 벡터 즉시 배정 유사도. **낮으면 과병합·높으면 LLM 호출↑** (핵심 튜닝) |
| `CATEGORY_CANDIDATES_K` | 5 | LLM에게 보여줄 기존 카테고리 후보 수 |
| `CATEGORY_CENTROID_MAX_N` | 50 | 중심벡터 갱신 상한(동결). 계속 갱신하면 "게시판 평균" 방향으로 표류해 블랙홀 카테고리가 됨 |
| `COOLDOWN_MIN` | 60 | 같은 카테고리 재알림 금지(분) |
| `HALF_LIFE_MIN` | 30 | 점수 감쇠 반감기 |
| `BURST_RATIO` / `MIN_RECENT` | 3.0 / 5 | 급증 판정 (비율+절대량) |
| `JUDGE_FUSED_PATH` | (빈값) | adapters3 머지 후 모델 경로 — 설정 시 어댑터 대신 사용 |

## 운영 메모

- **판정 로그**: `data/judge_log.jsonl` (오탐/미탐 모니터링, 문서 06)
- **점수 이력**: `category_scores` 테이블 (7일 보관, 스파크라인 데이터)
- **안전망 점검**: 관제 화면 버튼 or `POST /api/maintenance/recluster-check` — 하루 1회 권장.
  "병합 검토" 제안이 반복되면 `CATEGORY_ASSIGN_SIM`을 낮추거나 카테고리를 수동 정리
- **Gemma 4 사고 채널**: 기본형 모델이 `<|channel|>thought`를 출력할 수 있어
  `categorize._sanitize`가 final 채널만 취한다 (잘림 시 폴백)
- **MLX 스레드 규칙**: 모든 MLX 추론은 `services/mlx_runtime.py`의 전용 단일 스레드로.
  torch(임베딩)와 공존 시 다른 스레드에서 부르면 `no Stream(gpu, N)` 오류
- **실제 크롤링 전환 시**: 요청 간격·robots.txt·이용약관 주의 (문서 08 §8)

## MVVM 구조 (frontend/src)

```
models/api.js          # M — API 클라이언트
viewmodels/use*.js     # VM — 상태+로직 (usePolling 공통 폴링)
views/*.vue            # V — 관제/카테고리/알림/판단테스트
components/*.vue       # V — CategoryCard, Sparkline, HeatBar, SlotBadge, StatCard
```
