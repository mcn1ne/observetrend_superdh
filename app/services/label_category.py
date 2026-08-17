"""'일반' 대분류를 두 카테고리로 가르는 라벨 판정 (LLM + 영속 캐시).

adapters4 의 major 6종 중 5종은 카테고리로 직행하지만, `일반`(전체 글의 62%)만은
한 번 더 갈라야 한다 — `캐릭터·장비·덱` 인가 `일반·잡담` 인가.

**이전 방식(키워드 부분일치)의 한계**: `categorize._BUILD_KEYWORDS` 는 세븐나이츠·
나혼렙 어휘로 손수 채운 전역 튜플이라 두 문제가 있었다.
  1. 게임이 늘 때마다 사람이 어휘를 알아야 한다 (쿠키런의 '토핑'·'젤리'는 없다)
  2. 부분 문자열 매칭이라 오탐이 난다 ('강화' 가 "서버 안정성 강화 요청" 에 걸림)

**현재 방식**: 주제문구(topic_label) 자체를 LLM 이 한 번 보고 O/X 를 정한 뒤
그 판정을 라벨 문자열로 캐시한다. 판정 단위가 글이 아니라 **라벨**이라 비용이 묶인다
(실측 2026-08-17: '일반' 글 4,048건의 고유 라벨은 560개, 상위 10개가 글의 65%·
상위 50개가 83%를 덮는다. 신규 라벨은 하루 26~105개).

라벨에는 그 게임 어휘가 이미 들어 있으므로 게임별 분기가 필요 없다 —
`[일반] 토핑 조합 질문` 이든 `[일반] 헌터 육성 질문` 이든 같은 프롬프트로 판정된다.
그래서 캐시 키도 game_id 없이 라벨 문자열 하나다.

**판정 전 폴백**: 아직 안 물어본 라벨은 기존 키워드 규칙을 그대로 쓴다. 그래서
이 기능이 꺼져 있거나 큐가 밀려도 분류 품질이 이전보다 나빠지지 않는다.
쌓인 판정을 과거 글에 소급 적용하려면 `scripts/rebuild_categories_from_adapters4.py`
를 돌린다 (배정·중심벡터·건수를 저장된 분석 결과로 멱등 재구성한다).
"""
import json
import re
from pathlib import Path

from app import db
from app.config import settings

# 캐시는 topic_verify_cache.json / topic_merge_cache.json 과 같은 관용구를 따른다.
# LLM 판정 수백 건짜리 자산이라 메모리에만 두면 재시작마다 다시 사야 한다.
_CACHE_PATH = Path(settings.db_path).parent / "label_category_cache.json"


def _load_cache() -> dict[str, bool]:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {}


_cache: dict[str, bool] = _load_cache()

_SYSTEM_TMPL = (
    "너는 게임 커뮤니티 게시글의 주제 문구를 분류한다.\n{domain}\n\n"
    "아래 각 주제 문구가 '캐릭터 육성 · 장비 세팅 · 덱 편성' 에 관한 것인지 판정한다.\n"
    "- O: 캐릭터·유닛의 성능/육성/강화, 장비·아이템 세팅, 덱·조합·파티 편성 이야기\n"
    "- X: 그 외 전부 — 잡담·밈·인사·근황, 공지·이벤트 안내, 사건 제보, "
    "콘텐츠 진행 질문처럼 육성/세팅과 직접 상관없는 것\n"
    "표면 단어가 아니라 문구가 실제로 가리키는 내용으로 판단하라 "
    "(예: '서버 안정성 강화 요청' 은 '강화' 가 들어가지만 X다).\n"
    "문구마다 '번호: O' 또는 '번호: X' 형식으로 한 줄씩 출력한다.\n"
    "[출력 예시]\n1: O\n2: X\n3: O"
)

_LINE = re.compile(r"\s*(\d+)\s*[:.]\s*([OX])")


def verdict(topic_label: str | None) -> bool | None:
    """캐시된 판정. None = 아직 안 물어봄 (호출부가 키워드 규칙으로 폴백)."""
    if not topic_label:
        return None
    return _cache.get(topic_label)


def cache_stats() -> dict:
    judged = len(_cache)
    build = sum(1 for v in _cache.values() if v)
    return {"judged": judged, "build": build, "chatter": judged - build}


def classify_batch(game_id: str | None = None, batch_size: int | None = None) -> dict:
    """미판정 라벨을 빈도 높은 순으로 한 배치 판정해 캐시에 넣는다.

    빈도순인 이유: 상위 10개 라벨이 '일반' 글의 65%를 덮으므로, 앞쪽 몇 배치만
    돌아도 대부분의 글이 규칙이 아닌 실제 판정으로 분류된다.
    """
    from app.services.gemma_analyze import domain_context
    from app.services.mlx_runtime import extract_final_channel, generate_text

    game_id = game_id or settings.default_game_id
    batch_size = batch_size or settings.label_classify_batch_size

    labels = [l for l, _ in db.load_general_labels(game_id) if l not in _cache][:batch_size]
    if not labels:
        return {"taken": 0, "judged": 0}

    listing = "\n".join(f"{n}. {l}" for n, l in enumerate(labels, 1))
    out = generate_text(
        settings.categorizer_model,
        [{"role": "system", "content": _SYSTEM_TMPL.format(domain=domain_context(game_id))},
         {"role": "user", "content": f"[주제 문구]\n{listing}"}],
        # 사고 채널이 토큰을 먼저 소비하므로 여유를 크게 (결과 자체는 짧다)
        max_tokens=40 * len(labels) + settings.reclassify_extra_tokens,
    )

    judged = 0
    for line in extract_final_channel(out).splitlines():
        m = _LINE.match(line)
        if m and 1 <= int(m.group(1)) <= len(labels):
            _cache[labels[int(m.group(1)) - 1]] = m.group(2) == "O"
            judged += 1
    if judged:
        try:
            _CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False))
        except OSError:
            pass                      # 캐시 저장 실패는 치명적이지 않다 (다음에 다시 물음)
    return {"taken": len(labels), "judged": judged}


def pending_count(game_id: str | None = None) -> int:
    game_id = game_id or settings.default_game_id
    return sum(1 for l, _ in db.load_general_labels(game_id) if l not in _cache)
