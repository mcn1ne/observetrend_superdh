"""주제(topic) 탐지 — 미세·창발적 이슈 단위의 묶음.

카테고리(대분류, Gemma 관리)와 별개로, "지금 게시판에서 돌고 있는 구체적인
이야깃거리"를 벡터로 묶는다. 버스트·요약·알림 판단은 이 주제 단위로 돈다.

- 묶기: 중심벡터 탐욕(greedy centroid) — 수 밀리초, 유입량과 무관 ★
  (연결 요소 방식은 A~B, B~C 사슬 병합으로 잡탕 묶음이 생겨 교체함.
   그룹 중심과 직접 유사해야만 편입되므로 묶음이 조밀 = 이름이 구체적)
- 영속 추적: 묶음 중심벡터 ↔ topics 테이블 중심벡터 매칭 (topic_match_sim)
  → 클러스터 번호가 매번 바뀌어도 "같은 주제"는 같은 id 유지 (08 문서 원칙)
- 명명: 새 주제일 때만 Gemma 4 호출 (드묾 — 병목 없음)
"""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from app import db
from app.config import settings

# 이런 단어가 든 이름은 "구체적 이슈"가 아니므로 실패로 간주하고 재시도
_VAGUE_WORDS = (
    "게임 플레이", "격렬한", "자유로운", "전반적", "관련 이야기", "다양한",
    "의견 교환", "여러 가지", "일반적", "기타", "종합",
)

_NAME_SYSTEM = (
    "너는 게임 게시판 관제 시스템이다. 같은 이야깃거리로 묶인 글들을 보고 "
    "이 주제를 '〈구체적 대상〉+〈사건/행위〉' 형태의 한국어 명사구(2~6어절)로 명명한다.\n"
    "- 좋은 예: '구글 플레이스토어 결제 오류 제보', '길드전 매칭 방식 불만', "
    "'신규 캐릭터 성능 논란', '서버 접속 장애 제보', '총력전 보상 개선 요구'\n"
    "- 나쁜 예(금지): '게임 플레이에 대한 격렬한 반응', '자유로운 의견 교환', "
    "'전반적인 불만', '관련 이야기' — 무엇에 대한 것인지 드러나지 않는 일반어 금지\n"
    "- 글 제목을 그대로 복사하지 말 것\n"
    "이름만 한 줄 출력한다."
)

# 신규 묶음 명명 시, 근접 대역의 기존 주제와 같은 사건이면 번호로 편입
# (분열은 여기서 태어난다 — 생성 순간에 잡으면 추가 LLM 호출 없이 예방)
_NAME_ATTACH_SUFFIX = (
    "\n\n단, 이 글 묶음이 [기존 주제 후보] 중 하나와 같은 사건·이야깃거리라면 "
    "이름 대신 그 후보의 번호 하나만 출력한다. 비슷한 소재라도 다른 사건이면 새 이름을 짓는다."
)

_MERGE_SYSTEM = (
    "너는 게임 게시판 관제 시스템이다.\n{domain}\n\n"
    "아래 주제 A와 B가 '같은 사건·같은 이야깃거리'가 갈라진 것인지 판정한다 "
    "(충분히 생각한 뒤 답하라). 비슷한 소재(같은 게임 요소)라도 별개 사건이면 X다.\n"
    "마지막 줄에 O(같은 사건) 또는 X(별개)만 출력한다."
)


def _greedy_groups(embeddings: np.ndarray, threshold: float) -> list[list[int]]:
    """중심벡터 탐욕 묶기 — 그룹 중심과 유사도 ≥ threshold 여야만 편입.

    연결 요소와 달리 사슬 병합이 없어 그룹이 조밀하다. 시간순(입력순) 순회.
    """
    centroids: list[np.ndarray] = []
    members: list[list[int]] = []
    for i, v in enumerate(embeddings):
        best, best_sim = -1, threshold
        for g, c in enumerate(centroids):
            sim = float(np.dot(v, c))
            if sim > best_sim:
                best, best_sim = g, sim
        if best == -1:
            centroids.append(v.copy())
            members.append([i])
        else:
            members[best].append(i)
            c = np.mean(embeddings[members[best]], axis=0)
            norm = np.linalg.norm(c)
            centroids[best] = c / norm if norm > 0 else c
    return [idxs for idxs in members if len(idxs) >= settings.topic_min_size]


def _name_topic(group_posts: list[dict],
                attach_candidates: list[dict] | None = None) -> str | int:
    """새 주제 명명 — Gemma 4 (모호한 이름은 재시도, 실패 시 대표 제목 폴백).

    attach_candidates 가 있으면 "같은 사건이면 후보 번호로 편입"을 함께 판정하고,
    편입 시 해당 주제의 id(int)를 반환한다 (분열의 생성 시점 예방).
    """
    from app.services.mlx_runtime import extract_final_channel, generate_text

    listing = "\n".join(
        f"- {p['title']} — {(p.get('text') or '')[:80]}"
        for p in group_posts[:8]
    )
    system = _NAME_SYSTEM
    if attach_candidates:
        cand = "\n".join(f"{i+1}. {t['name']}" for i, t in enumerate(attach_candidates))
        system = _NAME_SYSTEM + _NAME_ATTACH_SUFFIX
        listing = f"[기존 주제 후보]\n{cand}\n\n[글 목록]\n{listing}"
    try:
        for attempt in range(2):
            user = listing if attempt == 0 else (
                listing + "\n\n(주의: 방금 답이 너무 모호했다. 글들이 공통으로 다루는 "
                "구체적인 대상을 반드시 이름에 넣어라.)"
            )
            out = generate_text(
                settings.categorizer_model,
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                # 이름은 짧지만 사고 채널이 토큰을 먼저 소비하므로 여유 필수
                max_tokens=settings.summarizer_max_tokens,
            )
            cleaned = extract_final_channel(out).strip()
            name = cleaned.splitlines()[0].strip() if cleaned else ""
            name = re.sub(r'^[-*\d.\)\s"\'`]+|["\'`]+$', "", name).strip()
            if attach_candidates:
                m = re.fullmatch(r"(\d+)\s*[번.]?", cleaned.splitlines()[0].strip() if cleaned else "")
                if m and 1 <= int(m.group(1)) <= len(attach_candidates):
                    return attach_candidates[int(m.group(1)) - 1]["id"]   # 기존 주제로 편입
            if not (2 <= len(name) <= 40) or "<" in name or "|" in name:
                continue
            if any(w in name for w in _VAGUE_WORDS):
                continue                    # 모호한 이름 → 재시도
            return name
    except Exception as e:
        print(f"[주제 명명 실패] {e}")
    return group_posts[0]["title"][:30]


# ── 주제 멤버 검증 (관제 화면·요약·알림 판단 정화) ───────────────────
# 주제 묶기는 순수 벡터(0.70)라 인접 용어(파쇄/감쇄 등)가 섞인다. 매분 전체
# LLM 검증은 불가능하므로 점진 처리: 판정을 (주제, 글) 단위로 캐시하고,
# 평시에는 사이클당 소량 예산으로, 버스트(알림 길목)는 우선적으로 검증한다.

# topic_id → {post_id: 소속 여부}. 파일로 영속 — 재기동해도 정화 진행분 유지
# (LLM 판정 수백 건짜리 자산이라 메모리에만 두면 재시작마다 다시 사야 한다)
_CACHE_PATH = Path(settings.db_path).parent / "topic_verify_cache.json"


def _load_verify_cache() -> dict[int, dict[str, bool]]:
    try:
        return {int(k): v for k, v in json.loads(_CACHE_PATH.read_text()).items()}
    except (OSError, ValueError):
        return {}


_verify_cache: dict[int, dict[str, bool]] = _load_verify_cache()

_VERIFY_SYSTEM_TMPL = (
    "너는 게임 게시판 관제 시스템이다.\n{domain}\n\n"
    "주제: 「{name}」\n"
    "아래 각 글이 이 주제에 속하는지 판정한다 (충분히 생각한 뒤 답하라).\n"
    "표면 단어가 아니라 글이 실제로 다루는 내용으로 판단하고, 인접하지만 다른 "
    "대상(예: 다른 스탯·다른 캐릭터·다른 콘텐츠)을 말하는 글은 X다.\n"
    "글마다 '글번호: O' 또는 '글번호: X' 형식으로 한 줄씩 출력한다.\n"
    "[출력 예시]\n1: O\n2: X\n3: O"
)

_VERIFY_LINE = re.compile(r"\s*(\d+)\s*[:.]\s*([OX])")


def verify_topic_members(topic_id: int, name: str, group: list[dict],
                         batch_limit: int = 1, batch_size: int = 5) -> tuple[list[dict], dict]:
    """주제 멤버를 LLM으로 점진 검증. 반환: (X 제외한 멤버, 통계).

    판정은 (topic_id, post_id) 단위로 캐시 — 같은 글을 두 번 묻지 않는다.
    batch_limit=0 이면 캐시 적용만 (LLM 호출 없음 — 평시 주제의 상시 필터).
    미검증분은 실행당 batch_limit 배치만 처리해 analyze 루프(60초 주기)를
    오래 막지 않는다. 미검증 글은 일단 포함 (몇 분에 걸쳐 점진적으로 정화).
    """
    from app.services.gemma_analyze import DOMAIN_CONTEXT
    from app.services.mlx_runtime import extract_final_channel, generate_text

    cache = _verify_cache.setdefault(topic_id, {})
    todo = [p for p in group if p["id"] not in cache][: batch_limit * batch_size]
    system = _VERIFY_SYSTEM_TMPL.format(domain=DOMAIN_CONTEXT, name=name)
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        listing = "\n".join(
            f"{n}. {p['title']} — {(p.get('text') or '')[:200]}"
            for n, p in enumerate(chunk, 1)
        )
        out = generate_text(
            settings.categorizer_model,
            [{"role": "system", "content": system},
             {"role": "user", "content": f"[글 목록]\n{listing}"}],
            max_tokens=60 * len(chunk) + settings.reclassify_extra_tokens,  # 사고 여유
        )
        for line in extract_final_channel(out).splitlines():
            m = _VERIFY_LINE.match(line)
            if m and 1 <= int(m.group(1)) <= len(chunk):
                cache[chunk[int(m.group(1)) - 1]["id"]] = m.group(2) == "O"
    if todo:
        try:
            _CACHE_PATH.write_text(json.dumps(_verify_cache))
        except OSError:
            pass                          # 캐시 저장 실패는 치명적이지 않음

    kept = [p for p in group if cache.get(p["id"], True)]   # X 확정만 제외
    stats = {
        "checked": sum(1 for p in group if p["id"] in cache),
        "evicted": sum(1 for p in group if cache.get(p["id"]) is False),
        "pending": sum(1 for p in group if p["id"] not in cache),
        "spent": -(-len(todo) // batch_size) if todo else 0,   # 이번에 쓴 LLM 배치 수
    }
    return kept, stats


# ── 분열 병합 (실시간) ───────────────────────────────────────────────
# 같은 사건이 두 주제로 갈라진 쌍(중심 유사도가 병합 대역인 쌍)을 사이클당
# 1쌍씩 LLM으로 판정해 병합. "별개" 판정은 캐시(파일 영속)해 다시 묻지 않는다.

_MERGE_CACHE_PATH = Path(settings.db_path).parent / "topic_merge_cache.json"


def _load_merge_cache() -> dict[str, bool]:
    try:
        return json.loads(_MERGE_CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {}


_merge_cache: dict[str, bool] = _load_merge_cache()


def _drop_verify_cache(topic_id: int) -> None:
    """이름이 바뀌면 그 이름 기준의 O/X 판정은 무효 — 다시 점진 검증한다."""
    if _verify_cache.pop(topic_id, None) is not None:
        try:
            _CACHE_PATH.write_text(json.dumps(_verify_cache))
        except OSError:
            pass


def _do_merge(known: list[dict], a: dict, b: dict, sim: float, how: str) -> None:
    keep, drop = (a, b) if a["id"] < b["id"] else (b, a)
    c = keep["centroid"] + drop["centroid"]
    norm = np.linalg.norm(c)
    c = (c / norm if norm > 0 else c).astype(np.float32)
    db.merge_topics(keep["id"], drop["id"], c)
    keep["centroid"] = c
    # 병합으로 주제의 실체가 넓어졌으므로 이름을 재유도: 명명 기준점을 영벡터로
    # 두면 다음 매칭 사이클의 드리프트 검사(sim=0)가 재명명을 자연히 발화시킨다
    zero = np.zeros_like(c)
    db.rename_topic(keep["id"], keep["name"], zero)
    keep["named_centroid"] = zero
    known.remove(drop)
    _verify_cache.setdefault(keep["id"], {}).update(_verify_cache.pop(drop["id"], {}))
    print(f"[주제 병합·{how}] 「{drop['name']}」({drop['id']}) → 「{keep['name']}」({keep['id']}) sim={sim:.3f}")


def _merge_sweep(known: list[dict], window_iso: str) -> None:
    """분열 회수 — 매 사이클 호출 (관제 요구: 분 단위 회수, 일 배치는 무의미).

    1) 중심 유사도 ≥ topic_match_sim 쌍: 시스템 정의상 '같은 주제' → 즉시 병합
       (LLM 불필요 — 실측: 정기 사태 분열 쌍들이 0.89~0.94로 공존하고 있었음)
    2) 병합 대역 [merge_band_low, match_sim) 쌍: 사이클당 1쌍 LLM 판정,
       '별개' 판정은 캐시(파일 영속)해 다시 묻지 않는다.
    known 리스트는 병합 결과를 반영해 제자리 수정된다.
    """
    from app.services.gemma_analyze import DOMAIN_CONTEXT
    from app.services.mlx_runtime import extract_final_channel, generate_text

    active = [t for t in known if t["last_seen_at"] >= window_iso]
    # 1) 확실한 같은 주제 (≥ auto_merge_sim) — 자동 병합, 사이클당 최대 5쌍
    for _ in range(5):
        pair = None
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                sim = float(np.dot(active[i]["centroid"], active[j]["centroid"]))
                if sim >= settings.topic_auto_merge_sim and (pair is None or sim > pair[0]):
                    pair = (sim, active[i], active[j])
        if pair is None:
            break
        _do_merge(known, pair[1], pair[2], pair[0], "자동")
        active = [t for t in active if t in known]

    # 2) 병합 대역 — LLM 판정 (사이클당 1쌍)
    best = None
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            key = f"{min(a['id'], b['id'])}-{max(a['id'], b['id'])}"
            if key in _merge_cache:
                continue
            sim = float(np.dot(a["centroid"], b["centroid"]))
            if settings.topic_merge_band_low <= sim < settings.topic_auto_merge_sim:
                if best is None or sim > best[0]:
                    best = (sim, a, b, key)
    if best is None:
        return
    sim, a, b, key = best
    lines_a = "\n".join(f"- {t}" for t in db.load_topic_titles(a["id"]))
    lines_b = "\n".join(f"- {t}" for t in db.load_topic_titles(b["id"]))
    out = generate_text(
        settings.categorizer_model,
        [{"role": "system", "content": _MERGE_SYSTEM.format(domain=DOMAIN_CONTEXT)},
         {"role": "user", "content": f"[주제 A] {a['name']}\n{lines_a}\n\n[주제 B] {b['name']}\n{lines_b}"}],
        max_tokens=settings.summarizer_max_tokens,   # 사고 채널 여유
    )
    answer = extract_final_channel(out).strip().splitlines()
    verdict = answer[-1].strip().upper() if answer else ""
    if verdict.startswith("O"):
        _do_merge(known, a, b, sim, "LLM")
    else:
        _merge_cache[key] = False            # 별개 사건 — 다시 묻지 않음
        try:
            _MERGE_CACHE_PATH.write_text(json.dumps(_merge_cache))
        except OSError:
            pass


def detect_topics(posts: list[dict], embeddings: np.ndarray,
                  now: datetime, since_iso: str) -> list[dict]:
    """창 내 글을 주제로 묶고 영속 추적. 반환: 주제별 {topic_id, name, idxs, centroid}."""
    groups = _greedy_groups(embeddings, settings.topic_link_sim)
    known = db.load_topics()
    _merge_sweep(known, since_iso)           # 분열 회수 — 사이클당 최대 1쌍 판정

    results: list[dict] = []
    by_topic_id: dict[int, dict] = {}
    assignments: dict[str, int | None] = {p["id"]: None for p in posts}
    rename_budget = 1                        # 드리프트 재명명: 실행당 1회 (폭주 방지)
    for idxs in sorted(groups, key=len, reverse=True):
        c = np.mean(embeddings[idxs], axis=0)
        norm = np.linalg.norm(c)
        if norm > 0:
            c = c / norm
        c = c.astype(np.float32)

        # 기존 주제와 중심벡터 매칭 → 같은 주제면 id 유지 (번호 재추적 문제 해결)
        best, best_sim = None, settings.topic_match_sim
        for t in known:
            sim = float(np.dot(c, t["centroid"]))
            if sim >= best_sim:
                best, best_sim = t, sim

        if best is not None:
            topic_id, name = best["id"], best["name"]
            db.touch_topic(topic_id, c)          # 중심·관측시각 갱신
            # 드리프트 재명명: 대화가 표류해 현재 중심이 "이름 지은 시점"에서
            # 멀어지면 현재 멤버로 이름을 다시 짓는다 (낡은 이름은 관제 오독
            # + 멤버 검증 오작동을 유발 — 실측: 「라드 필수성」에 루디 각성 98건)
            named_c = best.get("named_centroid")
            if named_c is None:
                db.rename_topic(topic_id, name, c)      # 구버전 행 — 기준점만 설정
                best["named_centroid"] = c
            elif (float(np.dot(c, named_c)) < settings.topic_rename_drift_sim
                  and rename_budget > 0):
                rename_budget -= 1
                new_name = _name_topic([posts[i] for i in idxs])
                if isinstance(new_name, str) and new_name != name:
                    print(f"[주제 재명명] 「{name}」 → 「{new_name}」 (id {topic_id})")
                    name = new_name
                    best["name"] = new_name
                    _drop_verify_cache(topic_id)  # 이름 기준이 바뀜 → 판정 재수집
                db.rename_topic(topic_id, name, c)      # 기준점 갱신 (이름 유지여도)
                best["named_centroid"] = c
        else:
            # 신규 묶음 — 병합 대역의 기존 주제가 있으면 명명과 동시에
            # "같은 사건이면 편입"을 판정 (분열의 생성 시점 예방, 추가 호출 없음)
            near = sorted(
                (t for t in known
                 if settings.topic_merge_band_low
                 <= float(np.dot(c, t["centroid"])) < settings.topic_match_sim),
                key=lambda t: -float(np.dot(c, t["centroid"])),
            )[:3]
            named = _name_topic([posts[i] for i in idxs], attach_candidates=near or None)
            if isinstance(named, int):
                topic_id = named                        # 같은 사건 → 기존 주제로 편입
                name = next(t["name"] for t in known if t["id"] == topic_id)
                db.touch_topic(topic_id, c)
            else:
                name = named
                # 같은 이름의 주제가 이미 있으면 그리로 편입 — 같은 주제가 매칭
                # 문턱 바로 아래에서 갈라지면 각각 생성되며 이름이 겹친다
                dup = next((t for t in known if t["name"] == name), None)
                if dup is not None:
                    topic_id = dup["id"]
                    db.touch_topic(topic_id, c)
                else:
                    topic_id = db.create_topic(name, c)
                    known.append({"id": topic_id, "name": name, "centroid": c,
                                  "named_centroid": c, "last_alerted_at": None})

        for i in idxs:
            # 검증에서 X 판정된 글은 주제 배정도 하지 않음 (주제 글 목록 정화)
            if _verify_cache.get(topic_id, {}).get(posts[i]["id"]) is False:
                continue
            assignments[posts[i]["id"]] = topic_id

        if topic_id in by_topic_id:
            # 서로 다른 연결 요소가 같은 영속 주제에 매칭됨 → 병합
            by_topic_id[topic_id]["idxs"] = list(by_topic_id[topic_id]["idxs"]) + list(idxs)
        else:
            item = {
                "topic_id": topic_id, "name": name, "idxs": list(idxs), "centroid": c,
                "last_alerted_at": next(
                    (t.get("last_alerted_at") for t in known if t["id"] == topic_id), None),
            }
            by_topic_id[topic_id] = item
            results.append(item)

    db.set_topic_assignments(assignments, since_iso)
    return results
