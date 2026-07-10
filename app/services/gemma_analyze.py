"""글 단위 분석 워커 — 보유 파인튜닝 adapters4 (큐 기반, 단건 호출).

역할: 모든 글을 GreatestStep에서 학습한 분류 LoRA(adapters4)가 읽고
  주제문구('[대분류] 자유형 요약') · 대분류(6종) · 감성(7종)을 저장한다.

- 주제문구는 알림 판단 모델(adapters3)의 학습 입력 라벨과 같은 어휘다 →
  버스트 주제의 judge 입력 라벨로 다수결 사용 (학습/서빙 분포 일치)
- 카테고리 배정은 건드리지 않는다 (벡터 빠른 경로 + 실시간 categorizer 담당)
- 긴급도(0~3)는 2026-07-09 폐기 — adapters4가 출력하지 않음 (구버전 행에만 존재)
- ⚠️ 시스템 프롬프트·입력 형식(제목\\n본문, 2000자)은 학습(data4)과 동일해야 한다.
  파인튜닝이 사고 채널 없이 한 줄을 즉시 출력하므로 max_tokens 을 작게 잡는다.
- 파싱 실패분은 attempts 3회까지 자동 재시도 후 실패 처리
"""
from app import db
from app.config import settings

# 디씨 갤러리 특성 — 재분류·요약 등 기본형 프롬프트가 공유하는 도메인 컨텍스트.
# ⚠️ 이게 없으면 기본형이 반어·밈을 문자 그대로 읽어 '환불/오류' 단어에만 반응한다 (실측).
# (adapters4 자체는 학습으로 도메인을 익혔으므로 이 컨텍스트를 쓰지 않는다)
DOMAIN_CONTEXT = (
    "대상은 모바일 게임 '세븐나이츠 리버스'의 디시인사이드 갤러리다.\n"
    "이 커뮤니티는 반어·과장·욕설·밈이 기본 화법이다: '망겜', '접는다', '환불각', "
    "'섭종해라' 같은 표현은 대부분 상투적 푸념이지 실제 사건 제보가 아니다. "
    "'뒷삭'은 갤러리 글 삭제를 뜻하는 갤 내부 용어다(게임 계정과 무관).\n"
    "단어가 아니라 글 전체 맥락으로 판단하라: 화자가 '실제로 겪은 구체적 사실'을 "
    "서술하는지, 아니면 감정·냉소·농담을 표출하는지 구분하는 것이 핵심이다."
)

MAJORS = {"일반", "콘텐츠", "운영", "밸런스", "과금", "버그"}
SENTIMENTS = {
    "neutral", "general_negative", "operation_criticism", "balance_complaint",
    "positive", "bug_complaint", "monetization_complaint",
}

# adapters4 학습(data4) 시스템 프롬프트 — 한 글자도 바꾸지 말 것
_SYSTEM = (
    "너는 게임 커뮤니티 게시글을 분석하는 분류기다. 게시글을 읽고 다음 형식으로 "
    "정확히 한 줄만 출력한다: 주제___대분류___감성\n"
    "- 주제: '[대분류] 간결한 주제 문구' 형태의 자유형 요약 "
    "(예: [일반] 커뮤니티 잡담 및 타 게임 관련 언급)\n"
    "- 대분류: 일반, 콘텐츠, 운영, 밸런스, 과금, 버그 중 하나\n"
    "- 감성: neutral, general_negative, operation_criticism, balance_complaint, "
    "positive, bug_complaint, monetization_complaint 중 하나\n"
    "다른 설명 없이 위 형식 한 줄만 출력한다."
)

_MAX_INPUT_CHARS = 2000   # 학습 데이터와 동일한 절단 길이


def _parse(out: str) -> dict | None:
    """'주제___대분류___감성' 한 줄 → 필드 dict. 형식·허용값 위반 시 None."""
    from app.services.mlx_runtime import extract_final_channel

    text = extract_final_channel(out).strip()
    for line in text.splitlines():
        parts = [p.strip() for p in line.strip().split("___")]
        if len(parts) != 3:
            continue
        topic_label, major, sentiment = parts
        if major in MAJORS and sentiment in SENTIMENTS and len(topic_label) >= 2:
            return {"topic_label": topic_label[:120], "major": major,
                    "sentiment": sentiment}
    return None


def analyze_batch() -> dict:
    """큐에서 글 N건을 꺼내 단건씩 분석. 반환: {taken, saved, failed_parse}"""
    from app.services.mlx_runtime import generate_text

    chunk = db.load_analysis_queue(limit=settings.gemma_batch_size)
    if not chunk:
        return {"taken": 0, "saved": 0, "failed_parse": 0}

    saved_rows, missed = [], []
    for p in chunk:
        body = f"{p['title']}\n{p.get('text') or ''}".strip()[:_MAX_INPUT_CHARS]
        out = generate_text(
            settings.categorizer_model,
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": body}],
            max_tokens=settings.analyzer_max_tokens,
            adapter_path=settings.analyzer_adapter_path,
        )
        parsed = _parse(out)
        if parsed is None:
            missed.append(p["id"])
            continue
        saved_rows.append({"post_id": p["id"], **parsed})

    db.save_analyses(saved_rows)
    db.bump_analysis_attempts(missed)   # 다음 라운드에 재시도 (3회 초과 시 실패 처리)
    return {"taken": len(chunk), "saved": len(saved_rows),
            "failed_parse": len(missed)}
