"""전체 설정 — 문서의 '다이얼'과 모델 슬롯을 한곳에서 관리.

- 다이얼 A/B/C: 08-realtime-detection.md
- 모델 슬롯: 아직 준비되지 않은 모델은 backend="stub"로 두고,
  준비되면 backend 값과 경로만 바꾸면 된다 (.env 로 덮어쓰기 가능).
"""
from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # ── 다이얼 3개 (08-realtime-detection.md §1) ──────────────────
    collect_interval_sec: int = 60      # A. 수집 주기
    analyze_interval_sec: int = 60      # B. 분석 실행 주기
    window_hours: int = 24              # C. 분석 대상 범위(시야)

    # ── 확산(버스트) 판정 (08 §4) ─────────────────────────────────
    burst_ratio: float = 3.0            # 평소의 몇 배면 급증인지
    min_recent: int = 5                 # 최근 창 최소 절대량 (오탐 방지)
    recent_window_min: int = 60         # 탐지 창(분)

    # ── 시간 감쇠 (08 §5) ─────────────────────────────────────────
    half_life_min: float = 30.0         # 반감기(분)

    # ── 쿨다운 (08 §6) ────────────────────────────────────────────
    cooldown_min: int = 60              # 같은 주제 재알림 금지(분)
    same_topic_sim: float = 0.85        # 같은 주제로 볼 코사인 유사도

    # ── 소강 알림 — 알림된 주제가 가라앉으면 종료 신호 발송 ─────────
    # heat(반감기 30분 감쇠합) 주도 + 글 수 보조: 완전 침묵 시 정점의 25%까지
    # 약 60분 → "종료 후 ~1시간 + 지속 확인 10분" 페이스로 소강이 나간다.
    calm_heat_ratio: float = 0.25       # 정점 대비 heat 이 비율 이하 (상대 기준)
    calm_streak_min: int = 10           # 조건 연속 충족 사이클(분) 수 (플래핑 방지)
    # 절대 기준은 min_recent 재사용: 최근 창 글 수 < min_recent (잔불 방지)

    # ── 수집 슬롯 (02-preprocessing.md) ──────────────────────────
    # dcinside: etl_dcinside 프로젝트의 dcinside.db(DB1)에서 증분 수집
    # mock: 데모용 가상 게시글 생성기
    collector_backend: str = "dcinside"  # dcinside | mock
    dcinside_db_path: str = "/Users/mcn1ne/etl_dcinside/dcinside.db"
    dcinside_backfill_hours: int = 72   # 최초 연결 시 몇 시간치까지 소급 수집할지

    # ── ① 임베딩 슬롯 (03-embedding.md) — 모델 미준비 ────────────
    # stub: 키워드 기반 가짜 벡터(데모용). 모델 준비 시:
    #   1) uv sync --extra ml
    #   2) EMBEDDING_BACKEND=sentence-transformers 로 변경
    embedding_backend: str = "stub"     # stub | sentence-transformers
    embedding_model: str = "google/embeddinggemma-300m"
    embedding_dim: int = 768

    # ── ② 클러스터링 슬롯 (04-clustering.md) — 미준비 ─────────────
    # stub: 코사인 유사도 탐욕 묶기(데모용). 준비 시:
    #   CLUSTERING_BACKEND=hdbscan (uv sync --extra ml 필요)
    clustering_backend: str = "stub"    # stub | hdbscan
    min_cluster_size: int = 5           # HDBSCAN 튜닝 포인트

    # ── 이미지 캡셔닝 슬롯 (제목+내용+이미지 임베딩용) ─────────────
    # stub: 이미지 무시. mlx-vlm: Gemma 4 멀티모달로 이미지를 한 문장 묘사
    # → 텍스트에 붙여서 EmbeddingGemma로 임베딩 (uv sync --extra vision 필요)
    captioner_backend: str = "stub"     # stub | mlx-vlm

    # ── 카테고리 분류 (하이브리드: 벡터 1차 + LLM 보조) ────────────
    # 새 글 → 기존 카테고리 중심벡터와 유사도 비교:
    #   ≥ category_assign_sim → 그 카테고리에 즉시 적재 (LLM 호출 없음)
    #   미달 → Gemma 4 기본형이 후보 k개 중 선택 or 신규 카테고리 명명
    categorizer_backend: str = "mlx"    # mlx | stub
    categorizer_model: str = "mlx-community/gemma-4-e4b-it-qat-4bit"
    # 벡터 빠른 경로 — "근사중복 지름길" 전용. ⚠️ 대분류 라우팅은 기하로 불가:
    # 실측(KURE, 2026-07-05) 글↔자기중심 0.661 vs 글↔남의중심 0.653 으로 안 갈림
    # (어떤 임계값에서도 통과율 ≈ 오배정률). 카테고리의 주 배정자는 LLM이고,
    # 이 값은 사실상 같은 글(도배 등)만 통과시켜 LLM 호출을 아끼는 용도.
    # 캘리브레이션은 반드시 글↔중심 분포로 할 것 (글↔글 최근접 통계 아님).
    category_assign_sim: float = 0.80
    category_candidates_k: int = 5      # LLM에게 보여줄 기존 카테고리 후보 수
    category_centroid_max_n: int = 50   # 중심벡터 갱신 상한 — 이후 동결 (표류로 인한 블랙홀 방지)

    # ── ③ 요약 슬롯 (05-summarization.md) ─────────────────────────
    # 요약 프롬프트에 넣는 글당 본문 길이. Gemma 4 컨텍스트는 128K 토큰이라
    # 여유가 크다 (전처리 단계에서 이미 2000자로 잘려 있음 — 임베딩 한도와 동일)
    summary_snippet_chars: int = 2000
    # stub: 대표 제목 나열(추출식). mlx: 로컬 Gemma 4B 프롬프트 요약.
    summarizer_backend: str = "stub"    # stub | mlx
    summarizer_model: str = "mlx-community/gemma-4-e4b-it-qat-4bit"

    # ── ④ 알림 판단 — 보유 파인튜닝 4B (06-alert-judgment.md) ─────
    # 현재: 베이스 + adapters3 LoRA 어댑터 (GreatestStep 프로젝트).
    # adapters3 머지(mlx_lm.fuse) 완료 후에는 judge_fused_path 에
    # 머지된 모델 경로만 넣으면 어댑터 없이 그 모델을 단독 로드한다.
    judge_backend: str = "mlx"          # mlx | stub
    judge_model: str = "mlx-community/gemma-4-e4b-it-qat-4bit"
    judge_adapter_path: str = "/Users/mcn1ne/GreatestStep/adapters3"
    judge_fused_path: str = ""          # 머지된 모델 경로 (설정 시 최우선)
    judge_system_prompt: str = (
        "너는 게임 운영팀의 알림 판단기다. 입력으로 주어진 클러스터 요약을 보고 "
        "운영팀에 즉시 알림을 발송해야 하면 'O', 아니면 'X'만 출력한다."
    )

    # ── 주제(topic) 묶기 — 미세·창발적 이슈 단위 (버스트·알림의 기준) ──
    # 코사인 연결 요소로 묶고(밀리초), Gemma는 새 주제 명명에만 사용.
    # 같은 주제로 연결할 유사도 — ⚠️ 반드시 실제 분석 창(24h)으로 캘리브레이션할 것
    # (KURE-v1, 24h 창 945건 실측: 0.70→주제 10개[48,27,24...], 0.75→1개로 과소)
    topic_link_sim: float = 0.70
    topic_min_size: int = 4             # 주제로 인정할 최소 글 수
    # 기존 주제와 같다고 볼 중심벡터 유사도. 연결 임계값보다 확실히 높아야
    # 별개 이슈가 기존 주제에 흡수되지 않는다 (런 간 같은 주제 드리프트는 ~0.98+)
    topic_match_sim: float = 0.85

    # ── 주제 드리프트 재명명 · 분열 병합 (실시간) ─────────────────────
    # 재명명: 현재 중심이 "이름을 지었던 시점의 중심"과 이 유사도 미만으로
    # 멀어지면 현재 멤버로 이름을 다시 짓는다 (실측: 「라드 필수성」 주제가
    # 루디 각성 얘기로 표류 — 이름이 낡으면 멤버 검증까지 오작동)
    topic_rename_drift_sim: float = 0.85
    # 자동 병합: 두 주제의 중심 유사도가 이 이상이면 LLM 없이 즉시 병합.
    # ⚠️ topic_match_sim(0.85)과 같게 두면 안 됨 — 실측에서 0.876~0.887 구간은
    # 잡탕 주제끼리 뭉치는 블랙홀 병합이 섞임 (확실한 쌍은 0.899~0.939)
    topic_auto_merge_sim: float = 0.90
    # 병합 후보 대역: [이 값, topic_auto_merge_sim) 쌍은 같은 사건이
    # 갈라졌을 가능성 → LLM이 판정 (사이클당 1쌍, 판정은 캐시)
    topic_merge_band_low: float = 0.75

    # ── 글 단위 분석 (큐 기반 워커) — 보유 파인튜닝 adapters4 ─────
    # GreatestStep에서 학습한 분류 LoRA: 글 단건 → "주제___대분류___감성" 한 줄.
    # 사고 채널 없이 즉시 출력하도록 학습됨 (기본형과 달리 토큰 상한 작게).
    gemma_analysis_enabled: bool = True
    gemma_batch_size: int = 5           # 사이클당 글 수 (단건 호출 × N)
    gemma_worker_interval_sec: int = 20 # 큐 소화 주기 (큐가 비면 대기만)
    analyzer_adapter_path: str = "/Users/mcn1ne/GreatestStep/adapters4"
    analyzer_max_tokens: int = 96       # 학습 출력이 한 줄 — 사고 채널 없음

    # ── LLM 생성 토큰 상한 (.env 로 제어) ─────────────────────────
    # 로컬 Gemma 4는 사고(thinking) 예산이 따로 없어 사고+답변이 이 상한을
    # 함께 쓴다. 상한(cap)일 뿐이라 크게 잡아도 모델이 일찍 끝내면 비용 없음.
    # 너무 작으면 사고만 하다 잘려 폴백(요약=제목 나열)으로 떨어진다.
    summarizer_max_tokens: int = 4000    # 요약: 사고 여유 + 3~5문장
    categorizer_max_tokens: int = 300    # 카테고리 명명
    reclassify_extra_tokens: int = 3000  # 배치 재분류: 건수×30 + 이 값
    judge_max_tokens: int = 8            # 판단: 파인튜닝이 사고 없이 O/X만 출력

    # ── 관제 타임머신 (스냅샷 재생 + 주제 시계열) ─────────────────
    # 매 분석 사이클의 대시보드 전체 상태를 gzip 블롭으로 저장해 임의 시각 재생.
    # 30일 · 1분 해상도 ≈ 스냅샷 ~200MB + topic_scores ~130MB (압축·인덱스 기준).
    snapshot_retention_days: int = 30

    # ── 저장소/서버 ───────────────────────────────────────────────
    db_path: str = str(BASE_DIR / "data" / "pipeline.db")
    judge_log_path: str = str(BASE_DIR / "data" / "judge_log.jsonl")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    model_config = {"env_file": str(BASE_DIR / ".env"), "extra": "ignore"}


settings = Settings()
