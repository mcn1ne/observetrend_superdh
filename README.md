# 게임 게시판 실시간 관제 파이프라인

게임 게시판 글을 주기적으로 수집해 전처리·임베딩하고, 큰 분류인 **카테고리**와 구체적인 사건 단위인 **주제**를 각각 추적한 뒤 급증한 주제만 요약·판정하는 관제 시스템입니다. 백엔드는 FastAPI와 SQLite, 프론트엔드는 Vue 3와 Vite로 구성되어 있으며, 로컬 Gemma/MLX 모델을 분류·주제 관리·요약·알림 판단에 사용합니다.

현재 외부 알림 채널은 연결되어 있지 않습니다. `O` 판정과 소강 이벤트는 SQLite에 기록되고 `send_alert()`가 서버 표준 출력에 메시지를 남깁니다.

## 핵심 개념

카테고리와 주제는 목적과 생성 방식이 다릅니다.

| 구분 | 카테고리 | 주제 |
|---|---|---|
| 의미 | 게시판의 큰 분류 | 현재 함께 언급되는 구체적인 사건·이슈 |
| 생성 | adapters4의 `major + topic_label` 규칙 매핑 | 임베딩 벡터의 average-link 탐욕 묶기 |
| 지속성 | 게임별 영속 ID·이름·중심벡터 | 게임별 영속 ID·이름·중심벡터 |
| 용도 | 전체 흐름과 heat 추이 표시 | 버스트 탐지, 요약, O/X 판단, 알림과 소강 감시 |
| 관계 | 한 카테고리에 여러 주제가 있을 수 있음 | 한 주제에 여러 카테고리의 글이 섞일 수 있음 |

활성 카테고리 배정 규칙이 만들 수 있는 이름은 다음 7개입니다. 기존 DB의 레거시 카테고리 행은 별도로 남아 있을 수 있습니다.

| adapters4 `major` | 영속 카테고리 |
|---|---|
| `콘텐츠` | `콘텐츠·공략` |
| `운영` | `운영·이벤트` |
| `밸런스` | `밸런스` |
| `과금` | `과금` |
| `버그` | `버그·오류` |
| `일반` + 육성·장비·덱 관련 `topic_label` | `캐릭터·장비·덱` |
| 그 밖의 `일반` | `일반·잡담` |

`일반` 분기 키워드의 정확한 목록은 `app/services/categorize.py`의 `_BUILD_KEYWORDS`에 있습니다.

## 전체 처리 흐름

```text
dcinside 원본 SQLite(DB1, read-only) 또는 mock
  │
  ├─ 게임별 post_no 증분 수집
  ├─ 제목+본문 정제, 짧은 글·현재 배치의 완전 중복 제거
  ├─ 첨부 이미지 URL 추출
  ▼
파이프라인 SQLite(DB2, data/pipeline.db)
  │
  ├─ 임베딩이 없는 글을 최대 500건씩 인코딩해 BLOB 저장
  │
  ├─ Gemma adapters4 워커 (게임별 최신 글 우선)
  │    ├─ topic_label___major___sentiment 분석
  │    └─ 규칙 매핑으로 영속 카테고리 배정
  │
  └─ 이미지 캡션 워커 (mlx-vlm 사용 시, 게임별 최신 글 우선)
       ├─ 첨부 최대 3장 다운로드
       ├─ 관련 정보 이미지 / 인증 화면 / 무관 이미지 판정
       └─ 관련 묘사만 텍스트에 붙여 해당 글을 재임베딩

매 분석 사이클, 게임별 최근 24시간 창
  │
  ├─ 카테고리별 heat·최근 글 수·버스트와 점수 이력 계산
  │
  └─ 글 임베딩을 주제로 average-link 묶기
       ├─ 기존 영속 주제와 1:1 매칭
       ├─ 새 주제 명명, 멤버 O/X 검증, 드리프트 재명명
       ├─ 자동 또는 LLM 판정 병합
       └─ 버스트 주제만 멤버 재검증 → 요약 → adapters3 O/X 판단
            ├─ O: 쿨다운 통과 시 이력 저장 + send_alert()
            ├─ X: 쿨다운 간격으로 판단 이력 저장
            └─ O 이후 충분히 식으면 소강 이력 저장 + send_alert()

분석 결과
  ├─ 인메모리 최신 상태: API와 실시간 화면
  ├─ gzip 스냅샷: 타임머신 재생
  ├─ category_scores / topic_scores: 시계열 집계
  └─ alerts / judge_log.jsonl: 판단·피드백·판정 원문 이력
```

### 수집과 전처리

- `dcinside` 백엔드는 원본 DB를 SQLite URI `mode=ro`로 엽니다.
- 게임마다 `source_game`을 정확히 일치시키고, 설정된 경우 `source_gallery`까지 일치시킵니다.
- 최초 연결은 기본 72시간만 소급하고, 이후에는 게임별 `last_post_no` 커서보다 큰 글만 읽습니다.
- 원본 `post_time`은 KST의 `YYYY-MM-DD HH:MM:SS`로 해석해 UTC ISO-8601로 저장합니다.
- 전처리는 제목과 본문을 합치고 HTML·URL·특수문자·디시 앱 푸터를 제거하며, 일부 은어를 정규화합니다. 정제 결과가 10자 미만이면 버리고 2,000자로 자릅니다.
- `mock` 백엔드는 기본 게임 하나에 최초 6시간 분량의 가상 글을 채운 뒤 평상시 유입과 확률적 10분 버스트를 생성합니다.

### 임베딩과 이미지

- 임베딩은 `embedding IS NULL`인 글만 계산합니다. 한 수집 사이클에 최대 500건을 처리합니다.
- 예외적으로 이미지 캡션이 끝난 글은 캡션을 반영하기 위해 재임베딩합니다.
- 캡션 워커는 최신 글부터 기본 3건씩 처리하며, 디시 이미지 요청에 `Referer`와 `User-Agent`를 사용합니다.
- 이미지 매직 바이트가 JPEG·PNG·GIF·WebP가 아니면 건너뜁니다.
- 결과·점수·순위·뽑기 화면은 `인증`, 글과 무관한 반응 이미지 등은 `무관`으로 표시해 임베딩 텍스트에 넣지 않습니다. 관련 정보 이미지의 묘사만 최대 280자로 반영합니다.
- 캡션은 주제 묶기용 임베딩에만 반영되며, 학습 입력 형식을 유지하기 위해 adapters4 분석 입력에는 포함하지 않습니다.

### 주제 추적과 품질 관리

- 주제 묶기는 시간순으로 글을 훑으며 새 글과 기존 그룹 **멤버 전체의 평균 코사인 유사도**가 `TOPIC_LINK_SIM` 이상인 최적 그룹에 편입합니다. 기본값은 `0.60`, 최소 주제 크기는 6건입니다.
- 그룹 중심과 저장된 주제 중심이 `TOPIC_MATCH_SIM=0.85` 이상이면 같은 실행에서 하나의 그룹만 그 주제 ID를 승계합니다.
- 새 그룹은 Gemma 기본 모델이 이름을 만들며, 유사한 기존 주제가 있으면 명명 단계에서 기존 사건으로 편입할 수 있습니다.
- 주제 중심이 이름을 만들던 시점의 중심에서 `0.85` 미만으로 멀어지면 한 사이클에 최대 한 주제를 재명명합니다.
- 활성 주제 두 개의 중심 유사도가 `0.92` 이상이면 한 사이클에 최대 5쌍을 자동 병합합니다. `0.75` 이상 `0.92` 미만인 최상위 한 쌍은 Gemma가 같은 사건인지 판정합니다.
- 멤버 검증과 병합 판정은 각각 `data/topic_verify_cache.json`, `data/topic_merge_cache.json`에 캐시합니다. 평시 멤버 검증 예산은 분석 사이클당 1배치이며, 버스트 길목에서는 최대 2배치를 우선 사용합니다.

### heat, 버스트, 알림과 소강

각 글의 시간 가중치는 다음과 같습니다.

```text
weight = 0.5 ** (글의 경과 분 / HALF_LIFE_MIN)
heat   = 그룹에 속한 글 weight의 합
```

기본 반감기는 30분입니다. 기본 24시간 창에서는 `전체 글 수 / 24`를 시간당 기준선으로 삼고, 최근 60분의 글 수 또는 heat가 다음 두 조건을 모두 만족하면 버스트로 봅니다.

```text
최근 값 >= 5
최근 값 / max(시간당 기준선, 0.5) >= 3.0
```

카테고리 버스트는 화면 표시용입니다. 실제 요약과 알림 판단은 주제 버스트에만 실행됩니다. 판단 입력은 adapters3의 학습 형식과 같은 다음 한 줄입니다.

```text
{멤버 adapters4 topic_label 다수결 또는 주제명}___{대표 제목}___{요약}
```

판단 모델의 `O`는 발송 후보, `X`는 미발송입니다. `O`는 주제별 기본 60분 쿨다운을 통과해야 `alerts`에 발송 이력으로 저장됩니다. `X`도 오탐·미탐 피드백을 받을 수 있도록 같은 주제명 기준 쿨다운 간격으로 이력에 남습니다.

`O`가 기록된 주제는 이후에도 감시합니다. 현재 heat가 관측 정점의 25% 이하이고 최근 글 수가 5건 미만인 상태가 10개 분석 사이클 연속 유지되면 `소강` 이벤트를 한 번 기록합니다. 기본 주기에서는 약 10분의 연속 확인에 해당합니다.

## 기술 구성

- 백엔드: Python 3.10+, FastAPI, Uvicorn, Pydantic Settings
- 저장소: SQLite, NumPy `float32` 임베딩 BLOB, gzip JSON 스냅샷
- 로컬 LLM: `mlx-lm`, 선택적 `mlx-vlm`
- 임베딩·클러스터링 선택 의존성: `sentence-transformers`, scikit-learn HDBSCAN
- 프론트엔드: Vue 3, Vue Router, Vite
- 패키지 관리: `uv`, npm

모든 MLX 추론은 `app/services/mlx_runtime.py`의 단일 전용 스레드에서 직렬 실행됩니다. 모델 로딩과 추론 스레드를 일치시키고 동시 생성에 따른 메모리 경합을 피하기 위한 구조입니다.

## 실행 전 준비

백엔드에는 Python 3.10 이상과 `uv`가 필요하고, 프론트엔드를 설치·개발·빌드하려면 Node.js/npm이 추가로 필요합니다. 실제 MLX 백엔드를 사용할 환경에는 `mlx-lm`/`mlx-vlm`이 실행 가능해야 하며, 설정된 Hugging Face 모델과 로컬 LoRA 어댑터에도 접근할 수 있어야 합니다.

### 1. 의존성 설치

기본 Python 의존성만 설치하려면:

```bash
uv sync
```

실제 sentence-transformers 임베딩, HDBSCAN, 이미지 캡셔닝까지 사용하려면:

```bash
uv sync --extra ml --extra vision
```

프론트엔드 의존성은 잠금 파일 기준으로 설치합니다.

```bash
cd frontend
npm ci
cd ..
```

### 2. 환경 설정

```bash
cp .env.example .env
```

`.env`의 대문자 키는 `app/config.py`의 `Settings` 필드를 덮어씁니다. 다만 활성 멀티게임 수집 경로는 `settings.games` 각 항목의 `source_db_path`, `source_game`, `source_gallery`를 사용합니다. `DCINSIDE_DB_PATH`는 하위호환 및 상태 표시용 필드이며, 이것만 바꿔서는 `collect_step()`이 사용하는 게임별 소스가 바뀌지 않습니다. 게임 등록과 원본 경로는 `app/config.py`의 `games` 목록을 환경에 맞게 구성해야 합니다.

기본 등록 게임은 다음 두 개입니다.

| `game_id` | 표시 이름 | 원본 `game` | 원본 `gallery` |
|---|---|---|---|
| `snr` | 세븐나이츠 리버스 | `세븐나이츠 리버스` | `sevennightsrebirth` |
| `solo` | 나혼자만레벨업어라이즈 | `나혼자만레벨업어라이즈` | `sololeveling` |

대상 DB에서 `post_no`는 갤러리 안에서만 유일하므로, 한 게임에 여러 갤러리가 들어 있다면 `source_gallery`를 지정하는 것이 안전합니다. 파이프라인의 게시글 키는 `(game_id, post_no)`입니다.

원본 DB가 없다면 제공된 구조로 빈 DB를 만들 수 있습니다.

```bash
sqlite3 /path/to/dcinside.db < schema/dcinside_source.sql
```

실제 수집기가 읽는 필드는 `gallery`, `post_no`, `game`, `title`, `content`, `post_time`, `url`입니다. 파이프라인 DB `data/pipeline.db`와 필요한 테이블·마이그레이션은 서버 시작 시 자동 생성됩니다.

### 3. 모델 설정 확인

코드 기본값과 역할은 다음과 같습니다. 이 표의 기본값은 `.env.example`의 운영 예시가 아니라 `app/config.py`의 실제 기본값입니다.

| 역할 | 코드 기본값 | 실제 동작 |
|---|---|---|
| 수집 | `COLLECTOR_BACKEND=dcinside` | `settings.games`의 원본 DB들을 읽음 |
| 임베딩 | `EMBEDDING_BACKEND=stub` | 768차원 단어 해시 데모 벡터; 상태상 미준비 |
| 임베딩 모델명 | `google/embeddinggemma-300m` | `sentence-transformers` 백엔드로 바꿀 때 사용 |
| 이미지 캡션 | `CAPTIONER_BACKEND=mlx-vlm` | `categorizer_model`을 멀티모달로 로드; `vision` extra 필요 |
| 글 분석·카테고리 | `GEMMA_ANALYSIS_ENABLED=true` | Gemma 4 기본 모델 + `ANALYZER_ADAPTER_PATH`의 adapters4 |
| 주제 이름·검증·병합 | Gemma 4 기본 모델 | `CATEGORIZER_MODEL`을 직접 사용하며 별도 stub 경로 없음 |
| HDBSCAN 안전망 | `CLUSTERING_BACKEND=stub` | 코사인 탐욕 데모 클러스터러; 상태상 미준비 |
| 요약 | `SUMMARIZER_BACKEND=stub` | 대표 제목 최대 3개를 나열하는 추출식 요약 |
| 알림 판단 | `JUDGE_BACKEND=mlx` | Gemma 4 + `JUDGE_ADAPTER_PATH`의 adapters3, 또는 fused 모델 |

기본 모델·어댑터 경로는 다음과 같습니다.

```text
CATEGORIZER_MODEL=mlx-community/gemma-4-e4b-it-qat-4bit
ANALYZER_ADAPTER_PATH=/Users/mcn1ne/GreatestStep/adapters4
JUDGE_MODEL=mlx-community/gemma-4-e4b-it-qat-4bit
JUDGE_ADAPTER_PATH=/Users/mcn1ne/GreatestStep/adapters3
```

다른 환경에서는 반드시 로컬 어댑터 경로를 수정해야 합니다. adapters3를 모델에 합친 경우 `JUDGE_FUSED_PATH`를 지정하면 `JUDGE_MODEL + JUDGE_ADAPTER_PATH` 대신 fused 모델 하나를 로드합니다.

저장소의 운영 예시인 KURE-v1/HDBSCAN/Gemma 요약을 쓰려면 다음 값을 사용할 수 있습니다.

```dotenv
EMBEDDING_BACKEND=sentence-transformers
EMBEDDING_MODEL=nlpai-lab/KURE-v1
CLUSTERING_BACKEND=hdbscan
CAPTIONER_BACKEND=mlx-vlm
SUMMARIZER_BACKEND=mlx
JUDGE_BACKEND=mlx
```

임베딩 모델을 바꾸면 기존 BLOB과 차원이 섞이지 않게 전체 데이터를 다시 임베딩하고 주제 유사도 임계값을 재보정해야 합니다.

`COLLECTOR_BACKEND=mock`은 원본 DB만 대체합니다. 주제 명명·검증·병합은 여전히 Gemma 기본 모델을 사용합니다. 또한 `GEMMA_ANALYSIS_ENABLED=false`로 adapters4 워커를 끄면 수집과 임베딩은 계속되지만 신규 글의 분석과 카테고리 배정은 멈춥니다. 현재 코드에는 완전한 무모델 주제 분석 모드가 없습니다.

### 4. 개발 서버 실행

터미널 1에서 백엔드를 실행합니다.

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8007
```

터미널 2에서 Vite 개발 서버를 실행합니다.

```bash
cd frontend
npm run dev
```

- 프론트엔드: <http://localhost:5173>
- OpenAPI 문서: <http://localhost:8007/docs>
- Vite는 `/api` 요청을 `http://localhost:8007`로 프록시합니다.

### 5. 단일 서버로 프론트엔드 제공

`frontend/dist`가 **백엔드 시작 전에** 존재하면 FastAPI가 정적 파일과 Vue Router SPA fallback을 함께 제공합니다.

```bash
cd frontend
npm run build
cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8007
```

이 경우 UI와 API를 모두 <http://localhost:8007>에서 사용할 수 있습니다. `dist` 존재 여부는 `app.main` 모듈을 불러올 때 확인하므로, 백엔드를 먼저 시작한 뒤 빌드했다면 서버를 재시작해야 합니다.

## 서버에서 동시에 도는 루프

FastAPI lifespan이 시작될 때 DB를 초기화하고 다음 작업을 기동합니다.

| 작업 | 기본 주기·배치 | 설명 |
|---|---|---|
| 수집·임베딩 루프 | 60초 | 모든 등록 게임 수집, 전 게임 임베딩 대기열 최대 500건 처리 |
| 게임별 분석 | 60초 | 최근 24시간 창의 카테고리·주제·알림 상태 계산과 스냅샷 저장 |
| adapters4 워커 | 게임별 5건, 대기 시 20초 | 분석 결과가 없는 최신 글부터 처리하고 카테고리 배정 |
| 이미지 캡션 워커 | 게임별 3건, 대기 시 20초 | 캡션이 없는 최신 이미지 글부터 처리하고 재임베딩 |

큐는 별도 브로커가 아니라 SQLite의 NULL/미존재 상태로 표현되므로 프로세스 재시작 후에도 남습니다. adapters4 출력 파싱 실패와 캡션 처리 실패는 각 게시글의 시도 횟수에 기록되며 기본 최대 3회까지 다시 시도합니다.

CPU를 오래 사용하는 동기 작업은 `asyncio.to_thread()`로 API 이벤트 루프 밖에서 실행됩니다. MLX 호출은 그 안에서 다시 전용 단일 스레드로 모입니다.

## 주요 설정

전체 설정과 기본값은 `app/config.py`, 복사용 예시는 `.env.example`이 기준입니다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| `COLLECT_INTERVAL_SEC` | `60` | 수집 루프 대기 시간 |
| `ANALYZE_INTERVAL_SEC` | `60` | 분석 실행 최소 간격 |
| `WINDOW_HOURS` | `24` | 라이브 분석 창 |
| `DCINSIDE_BACKFILL_HOURS` | `72` | 게임별 최초 원본 연결 소급 범위 |
| `EMBEDDING_BACKEND` | `stub` | `stub` 또는 `sentence-transformers` |
| `EMBEDDING_DIM` | `768` | stub 벡터 차원 |
| `CAPTION_MAX_IMAGES` | `3` | 글당 캡션 대상 이미지 상한 |
| `CAPTION_BATCH_SIZE` | `3` | 게임별 캡션 워커 배치 |
| `GEMMA_BATCH_SIZE` | `5` | 게임별 adapters4 워커 배치 |
| `CATEGORY_CENTROID_MAX_N` | `50` | 카테고리 중심 갱신 후 동결 기준 |
| `TOPIC_LINK_SIM` | `0.60` | average-link 그룹 편입 기준 |
| `TOPIC_MIN_SIZE` | `6` | 주제로 인정할 최소 글 수 |
| `TOPIC_MATCH_SIM` | `0.85` | 기존 주제 ID 승계 기준 |
| `TOPIC_RENAME_DRIFT_SIM` | `0.85` | 주제 재명명 드리프트 기준 |
| `TOPIC_AUTO_MERGE_SIM` | `0.92` | 자동 병합 기준 |
| `TOPIC_MERGE_BAND_LOW` | `0.75` | LLM 병합 검토 대역 하한 |
| `RECENT_WINDOW_MIN` | `60` | 최근 글 수를 세는 범위 |
| `HALF_LIFE_MIN` | `30` | heat 반감기 |
| `BURST_RATIO` | `3.0` | 시간당 기준선 대비 급증 배수 |
| `MIN_RECENT` | `5` | 버스트 최소 최근 값 및 소강 보조 기준 |
| `COOLDOWN_MIN` | `60` | 같은 주제 O 재발송과 X 이력 중복 제한 |
| `CALM_HEAT_RATIO` | `0.25` | 소강으로 볼 정점 대비 heat 비율 |
| `CALM_STREAK_MIN` | `10` | 소강 조건 연속 분석 사이클 수 |
| `SNAPSHOT_RETENTION_DAYS` | `30` | 스냅샷·주제 점수 보관 일수 |
| `DB_PATH` | `data/pipeline.db` | 파이프라인 SQLite 경로 |
| `JUDGE_LOG_PATH` | `data/judge_log.jsonl` | 판단 원문 JSONL 로그 경로 |

`CATEGORY_ASSIGN_SIM`, `CATEGORY_CANDIDATES_K`, `CATEGORIZER_BACKEND`는 현재 활성 규칙 매핑이 아니라 `assign_posts()`로 보존된 이전 벡터+기본형 Gemma 분류 경로와 재분류 도구에 쓰입니다.

## API

카테고리·주제·알림·스냅샷 같은 게임별 보드 조회 API는 선택적 `game` 쿼리를 받으며, 생략하면 `default_game_id`인 `snr`를 사용합니다. 전역 ID로 조회하는 상세 API와 아래에 따로 적은 유지보수 API는 `game`을 받지 않습니다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/status` | 루프 상태, 슬롯, 다이얼, 전체 게시글 수 |
| `GET` | `/api/games` | 등록 게임과 기본 게임 |
| `GET` | `/api/categories` | 선택 게임의 최신 카테고리 보드와 6시간 score history |
| `GET` | `/api/categories/{id}/posts` | 카테고리의 최근 글 |
| `GET` | `/api/topics` | 선택 게임의 최신 주제 보드 |
| `GET` | `/api/topics/{id}/posts` | 주제 글 검색·정렬·페이지네이션; `hours=0`은 전체 이력 |
| `GET` | `/api/topics/{id}/posts-at?at=...` | 지정 시각 이하 최신 스냅샷의 주제 멤버 복원 |
| `GET` | `/api/alerts` | 현재 `O` 주제와 최근 O/X/소강 이력 100건 |
| `POST` | `/api/alerts/{id}/feedback` | `O`, `X`, `null` 피드백 저장 |
| `GET` | `/api/alerts/export` | 평가된 판단을 UTF-8 BOM CSV로 내보내기 |
| `GET` | `/api/posts/recent` | 최근 수집 글과 임베딩 완료 여부 |
| `GET` | `/api/snapshots` | 타임머신 스냅샷 메타 목록 |
| `GET` | `/api/snapshot` | 지정 시각 이하 최신 대시보드 스냅샷 복원 |
| `GET` | `/api/history/range` | 구간 커버리지·알림·상위 주제·카테고리 집계 |
| `POST` | `/api/run` | 수집과 분석을 1회 수동 실행 |
| `GET` | `/api/maintenance/cosine-cluster` | 연결 요소 방식 코사인 임계값 실험 |
| `POST` | `/api/maintenance/reclassify` | 기존 카테고리 체계로 창 내 글을 기본형 Gemma 재분류 |
| `POST` | `/api/maintenance/recluster-check` | HDBSCAN/대체 클러스터러 안전망 점검 |
| `GET` | `/api/gemma/analyses` | adapters4 큐 통계와 최근 분석 결과 |
| `POST` | `/api/judge/test` | 판단 모델 단독 O/X 테스트 |

`/api/run`, cosine 실험, 재분류, HDBSCAN 점검은 현재 함수 구현상 기본 게임을 대상으로 합니다. 프론트 API 클라이언트가 `game`을 붙여도 이 엔드포인트들은 이를 매개변수로 받지 않습니다. `/api/alerts/export`도 게임 필터 없이 평가된 전체 알림을 내보냅니다.

## 프론트엔드 화면

프론트엔드는 API 클라이언트(Model), 상태·폴링 composable(ViewModel), Vue 화면(View)을 나눈 MVVM 구조입니다. 폴링 화면은 10초 또는 15초마다 갱신되고, 게임을 바꾸면 즉시 다시 조회합니다. 선택한 게임과 데스크톱 사이드바 접힘 상태는 `localStorage`에 저장됩니다.

| 경로 | 화면 |
|---|---|
| `/` | 라이브 보드, 슬롯·다이얼, 수동 실행, HDBSCAN 요약 점검, 최근 글 |
| `/topics` | 알림 → 버스트 → heat 순의 활동 주제 |
| `/topics/:id` | 현재 창/전체 이력/스냅샷 당시 구성, 검색·정렬·페이지네이션 |
| `/categories` | 영속 카테고리와 heat 스파크라인 |
| `/categories/:id` | 카테고리의 최근 24시간 글 |
| `/alerts` | 현재 O 주제, O/X/소강 이력, 피드백, 학습 CSV 다운로드 |
| `/timemachine` | 스냅샷 스크럽·자동 재생·다음 알림 이동·구간 집계 |
| `/judge` | `주제___제목___요약` 형식 판단 모델 테스트 |
| `/hdbscan` | 안전망 클러스터와 현 카테고리 구성 비교 |
| `/cosine` | 0.50~0.95 코사인 연결 요소 임계값 실험 |
| `/gemma` | adapters4 대기·완료·실패 통계와 감성/대분류 필터 |

`/clusters`는 `/categories`로 리다이렉트됩니다. 레이아웃은 768px 이하에서 모바일 드로어로 전환됩니다.

## 데이터 저장과 보관

`data/`, `.env`, 로그, 프론트 빌드와 설치 결과는 `.gitignore` 대상입니다.

| 테이블·파일 | 내용 | 자동 보관 정책 |
|---|---|---|
| `games` | 게임 등록 정보와 증분 수집 커서 | 삭제 없음 |
| `posts` | 원문, 정제 텍스트, 이미지 메타, 캡션, 임베딩, 카테고리·주제 ID | 삭제 없음 |
| `post_analysis` | adapters4 `topic_label`, `major`, `sentiment` | 삭제 없음 |
| `categories` | 게임별 영속 카테고리와 중심·누적 수 | 삭제 없음 |
| `category_scores` | 카테고리 heat와 최근 글 수 | 7일 |
| `topics` | 게임별 영속 주제, 중심, 명명 기준점, 알림·소강 감시 상태 | 기간 만료 삭제 없음; 병합에서 흡수된 주제는 삭제 |
| `topic_scores` | 주제 heat·크기·버스트·판정 시계열 | 기본 30일 |
| `snapshots` | 매 분석 사이클의 gzip 대시보드 상태와 당시 멤버 ID | 기본 30일 |
| `alerts` | O, X, 소강 이벤트, 판단 입력, 담당자 피드백 | 삭제 없음 |
| `data/judge_log.jsonl` | 판단 시각·입력·결정·모델 원출력 | 삭제 없음 |
| `data/topic_verify_cache.json` | 주제별 글 소속 O/X 캐시 | 삭제 없음 |
| `data/topic_merge_cache.json` | 별개 주제 쌍 판정 캐시 | 삭제 없음 |

구형 DB는 `init_db()`가 필요한 컬럼과 `game_id` 파티션을 멱등 마이그레이션하고, `settings.games`를 `games` 테이블에 upsert합니다.

## 유지보수·실험 스크립트

스크립트는 저장소 루트에서 실행합니다.

| 스크립트 | 용도 |
|---|---|
| `scripts/backfill_image_urls.py [hours]` | 최근 글 본문에서 첨부 URL을 다시 찾아 캡션 큐에 넣음 |
| `scripts/rebuild_categories_from_adapters4.py` | 저장된 adapters4 결과만으로 모든 등록 게임의 규칙 카테고리를 멱등 재구성 |
| `scripts/build_gold_dataset.py` | 읽기 전용 DB에서 HDBSCAN→LLM 검증→문장 단위 구출로 오프라인 멀티라벨 정답셋 생성 |
| `scripts/exp_label_embedding.py` | 원문, `topic_label`, 원문+라벨 임베딩의 주제 묶기 품질을 읽기 전용으로 비교 |
| `scripts/seed_categories_from_gold.py` | 레거시 정답셋 기반 카테고리 초기화·시드; DB를 직접 재작성하므로 서버 중지와 백업 필수 |
| `scripts/build_system_overview_ppt.py` | `docs/TrendSys_전체작동방식.pptx` 생성 |
| `scripts/build_user_flow_ppt.py` | `docs/TrendSys_사용자용_분류와주제_전체플로우.pptx` 생성 |

정답셋 스크립트의 주요 예시는 다음과 같습니다.

```bash
uv run python scripts/build_gold_dataset.py
uv run python scripts/build_gold_dataset.py --hours 48
uv run python scripts/build_gold_dataset.py --resume
uv run python scripts/build_gold_dataset.py --no-llm
```

PPT 생성 스크립트가 import하는 `python-pptx`는 현재 `pyproject.toml` 의존성에 선언되어 있지 않습니다. 해당 스크립트를 실행하려면 별도로 준비해야 합니다.

## 프로젝트 구조

```text
app/
  main.py                  FastAPI lifespan, 백그라운드 루프, SPA 서빙
  config.py                전체 설정과 게임 레지스트리
  db.py                    SQLite 스키마·마이그레이션·쿼리
  store.py                 게임별 최신 분석 결과 인메모리 저장소
  routers/analysis.py      /api 라우터
  services/
    collect.py             dcinside/mock 수집
    preprocess.py          텍스트 정제·첨부 URL 추출
    embedding.py           stub/sentence-transformers 임베딩
    caption.py             비동기 이미지 다운로드·판정·캡션
    gemma_analyze.py       adapters4 글 분석 큐
    categorize.py          활성 규칙 카테고리와 레거시 분류 경로
    topics.py              주제 묶기·추적·검증·재명명·병합
    detection.py           heat와 버스트 공식
    summarize.py           추출식/MLX 요약
    judge.py               adapters3/fused/stub O/X 판단과 JSONL 로그
    mlx_runtime.py         MLX 단일 스레드와 모델 캐시
    pipeline.py            전체 수집·분석 연결과 소강 감시
frontend/
  src/models/              API와 전역 게임 상태
  src/viewmodels/          폴링·상태·화면 로직 composable
  src/views/               라우트 화면
  src/components/          보드·카드·그래프·상태 표시 컴포넌트
schema/dcinside_source.sql 원본 DB 최소 스키마
scripts/                    유지보수·정답셋·실험·PPT 생성 도구
docs/                       최적화 TODO와 로컬 설명 자료
```

## 운영 시 주의사항

- 외부 Slack·메일·웹훅 발송은 구현되어 있지 않습니다. 연동 지점은 `app/services/pipeline.py`의 `send_alert()`입니다.
- 인증·인가가 구현되어 있지 않으며, 수동 실행·유지보수·판단 테스트·피드백 API도 보호되지 않습니다. 현재 상태로 공용 인터넷에 노출하면 안 됩니다.
- 최신 대시보드는 프로세스 메모리에 있고 모든 lifespan 프로세스가 자체 수집·분석 루프를 시작합니다. 별도 조정 장치를 추가하지 않는 한 Uvicorn 다중 worker 구성은 중복 작업과 서로 다른 인메모리 상태를 만들 수 있습니다.
- `/api/run`은 백그라운드 루프와 별도로 기본 게임의 수집·분석을 실행하므로 장시간 모델 작업 중 중복 실행에 주의해야 합니다.
- 실제 원본 DB는 읽기 전용이지만 파이프라인 DB와 캐시·로그에는 지속적으로 씁니다. 운영 전 `data/` 백업 정책을 별도로 마련해야 합니다.
- 유사도 임계값은 현재 임베딩 공간과 데이터 분포에 맞춘 값입니다. 모델이나 게임을 바꾸면 재보정해야 합니다.
- 저장소에는 자동화된 테스트, lint 설정, Docker 구성이 없습니다. 프론트엔드 배선 검증은 `npm run build`, API 동작 검증은 개발 서버의 `/docs`와 각 상태·조회 엔드포인트를 기준으로 수행해야 합니다.
- 크롤링 원본을 운영할 때는 대상 서비스의 요청 제한, robots.txt, 이용약관을 확인해야 합니다.

주제 묶기의 향후 행렬화 계획은 `docs/TODO-주제묶기-행렬화.md`를 참고하세요.
