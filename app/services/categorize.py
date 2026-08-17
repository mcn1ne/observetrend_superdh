"""영속 카테고리 분류.

**현재(활성) 경로 — adapters4 규칙매핑**: `gemma_analyze.analyze_batch()`가 글당
adapters4 1회 호출로 얻은 major+topic_label을 `assign_from_analysis()`가 규칙
매핑(`MAJOR_CATEGORY_MAP`/`_BUILD_KEYWORDS`)해 즉시 확정한다. 이 경로는 추가
LLM 호출이 없다.

**이전 경로 — 벡터 1차 매칭 + LLM 보조 명명 (`assign_posts`/`MLXCategorizer`,
아래 보존, 현재 파이프라인에서는 호출되지 않음)**:
    1) 임베딩 ↔ 기존 카테고리 중심벡터 코사인 유사도 계산 (VectorDB 역할)
    2) 최고 유사도 ≥ category_assign_sim → 즉시 그 카테고리에 적재 (LLM 호출 없음)
    3) 미달 → Gemma 4 기본형에게 상위 후보 k개를 보여주고
       "후보 중 선택 or 신규 카테고리 명명"을 요청
    4) 신규면 카테고리 생성 (이 글의 벡터가 초기 중심벡터)

카테고리 중심벡터는 (두 경로 공통) 글이 배정될 때마다 이동 평균으로 갱신 후
재정규화하며, `category_centroid_max_n`에서 동결한다.
"""
import re
from typing import Protocol

import numpy as np

from app import db
from app.config import settings

_SYSTEM = (
    "너는 게임 게시판 관제 시스템의 카테고리 분류기다. 게시글을 보고 "
    "기존 카테고리 중 하나에 속하면 그 카테고리 이름을 정확히 그대로 출력한다. "
    "어디에도 속하지 않을 때만 새 카테고리 이름을 만든다.\n"
    "새 카테고리 이름 규칙:\n"
    "- 게시글 제목을 복사하지 말고, 같은 부류의 글이 계속 모일 수 있는 "
    "일반화된 주제 명사구로 짓는다 (한국어 2~4어절)\n"
    "- 좋은 예: '덱 구성 질문', '뽑기 결과 공유', '캐릭터 밸런스 불만', "
    "'버그·오류 제보', '결제·환불 문의', '스토리 감상', '유머·잡담'\n"
    "- 나쁜 예: 특정 캐릭터명·수치·말줄임이 든 글 제목 그대로\n"
    "설명 없이 카테고리 이름만 한 줄로 출력한다."
)

# ── 운영 카테고리: adapters4 1회 분석 결과로 결정 ──────────────────
# major가 명확한 5개 영역은 그대로 큰 서랍으로 매핑한다. '일반'만 topic_label의
# 세부 의미를 이용해 육성/세팅 질문과 나머지 잡담을 나눈다. 자유형 label 문자열을
# 카테고리 이름으로 직접 쓰지 않아 카테고리 폭증을 막는다.
MAJOR_CATEGORY_MAP = {
    "콘텐츠": "콘텐츠·공략",
    "운영": "운영·이벤트",
    "밸런스": "밸런스",
    "과금": "과금",
    "버그": "버그·오류",
}
_BUILD_KEYWORDS = (
    "캐릭터", "헌터", "육성", "장비", "세팅", "셋팅", "덱", "스킬",
    "무기", "아티", "아티팩트", "돌파", "초월", "강화", "조합",
)


def category_name_from_analysis(major: str | None, topic_label: str | None) -> str:
    """adapters4의 major+topic_label을 영속 대분류 이름으로 변환한다."""
    if major in MAJOR_CATEGORY_MAP:
        return MAJOR_CATEGORY_MAP[major]
    label = topic_label or ""
    if any(k in label for k in _BUILD_KEYWORDS):
        return "캐릭터·장비·덱"
    return "일반·잡담"


def assign_from_analysis(post: dict, analysis: dict, game_id: str | None = None) -> int:
    """adapters4 결과 1건으로 카테고리를 배정하고 중심벡터를 갱신한다.

    기본형 LLM은 호출하지 않는다. 같은 분석 결과를 post_analysis 저장과 카테고리
    배정에 함께 사용하므로 글당 Gemma 호출은 adapters4 한 번뿐이다.
    """
    game_id = game_id or post.get("game_id") or settings.default_game_id
    name = category_name_from_analysis(analysis.get("major"), analysis.get("topic_label"))
    vec = post.get("embedding")
    if vec is None:
        raise ValueError("카테고리 배정에는 게시글 임베딩이 필요합니다")

    category = db.find_category_by_name(name, game_id)
    if category is None:
        category_id = db.create_category(name, vec, game_id)
        category = db.find_category_by_name(name, game_id)
    else:
        category_id = category["id"]

    db.assign_category(post["id"], category_id, game_id)
    n = category["post_count"]
    centroid = category["centroid"]
    if n < settings.category_centroid_max_n:
        centroid = (centroid * n + vec) / (n + 1)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
    db.update_category_centroid(category_id, centroid, n + 1)
    return category_id


def rebuild_categories_from_analyses(game_id: str | None = None) -> dict:
    """저장된 adapters4 결과 전체로 한 게임의 카테고리를 멱등 재구성한다.

    기존 자유형 카테고리 행은 점수 이력 참조 보존을 위해 삭제하지 않고, 현재 글이
    하나도 없으면 post_count=0으로 남긴다. 반복 실행해도 실제 posts 배정에서 다시
    집계하므로 카운트가 부풀지 않는다.
    """
    game_id = game_id or settings.default_game_id
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.embedding, a.major, a.topic_label "
            "FROM posts p JOIN post_analysis a "
            "ON a.game_id=p.game_id AND a.post_id=p.id "
            "WHERE p.game_id=? AND p.embedding IS NOT NULL",
            (game_id,),
        ).fetchall()

        category_ids: dict[str, int] = {}
        for r in rows:
            name = category_name_from_analysis(r["major"], r["topic_label"])
            if name not in category_ids:
                found = conn.execute(
                    "SELECT id FROM categories WHERE game_id=? AND name=?",
                    (game_id, name),
                ).fetchone()
                if found:
                    category_ids[name] = found["id"]
                else:
                    vec = np.frombuffer(r["embedding"], dtype=np.float32)
                    now = db._now()
                    cur = conn.execute(
                        "INSERT INTO categories (game_id,name,centroid,post_count,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (game_id, name, vec.tobytes(), 0, now, now),
                    )
                    category_ids[name] = cur.lastrowid
            conn.execute(
                "UPDATE posts SET category_id=? WHERE game_id=? AND id=?",
                (category_ids[name], game_id, r["id"]),
            )

        # 모든 카테고리의 count/centroid를 현재 실제 배정에서 다시 계산한다.
        categories = conn.execute(
            "SELECT id FROM categories WHERE game_id=?", (game_id,)
        ).fetchall()
        now = db._now()
        for c in categories:
            vectors = conn.execute(
                "SELECT embedding FROM posts WHERE game_id=? AND category_id=? "
                "AND embedding IS NOT NULL ORDER BY created_at LIMIT ?",
                (game_id, c["id"], settings.category_centroid_max_n),
            ).fetchall()
            actual = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE game_id=? AND category_id=?",
                (game_id, c["id"]),
            ).fetchone()[0]
            if vectors:
                centroid = np.mean(
                    np.stack([np.frombuffer(v["embedding"], dtype=np.float32) for v in vectors]),
                    axis=0,
                )
                norm = np.linalg.norm(centroid)
                if norm > 0:
                    centroid = centroid / norm
                conn.execute(
                    "UPDATE categories SET centroid=?,post_count=?,updated_at=? WHERE id=?",
                    (centroid.astype(np.float32).tobytes(), actual, now, c["id"]),
                )
            else:
                conn.execute(
                    "UPDATE categories SET post_count=0,updated_at=? WHERE id=?",
                    (now, c["id"]),
                )

    return {"game_id": game_id, "reassigned": len(rows),
            "active_categories": len(category_ids), "categories": sorted(category_ids)}


class Categorizer(Protocol):
    ready: bool
    name: str

    def pick_or_create_name(self, post: dict, candidates: list[dict]) -> str:
        """post: {title, text} / candidates: [{name, sim}] → 카테고리 이름"""
        ...


class StubCategorizer:
    """LLM 없이 동작: 최고 후보가 하한선을 넘으면 그 이름, 아니면 제목으로 신규."""

    ready = False
    name = "stub (제목 기반 명명 — LLM 미사용)"
    FALLBACK_SIM = 0.70

    def pick_or_create_name(self, post: dict, candidates: list[dict]) -> str:
        if candidates and candidates[0]["sim"] >= self.FALLBACK_SIM:
            return candidates[0]["name"]
        return post["title"][:30].strip()


class MLXCategorizer:
    """Gemma 4 기본형(공유 로딩)이 카테고리를 뽑는다."""

    ready = True

    def __init__(self) -> None:
        self.name = f"mlx ({settings.categorizer_model})"

    def pick_or_create_name(self, post: dict, candidates: list[dict]) -> str:
        from app.services.mlx_runtime import generate_text

        cand_text = (
            "\n".join(f"- {c['name']}" for c in candidates)
            if candidates else "(아직 카테고리 없음)"
        )
        user = (
            f"[기존 카테고리 후보]\n{cand_text}\n\n"
            f"[게시글]\n제목: {post['title']}\n내용: {(post.get('text') or '')[:300]}"
        )
        name = ""
        for _ in range(2):   # 사고 채널로 새서 빈 답이면 1회 재시도
            out = generate_text(
                settings.categorizer_model,
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                max_tokens=settings.categorizer_max_tokens,
            )
            name = _sanitize(out)
            if name:
                break
        if not name:
            # 재시도까지 실패: 최근접 후보로 라우팅하면 무관한 전문 카테고리가
            # 오염되므로(실측), 가장 글이 많은 범용 카테고리(보통 잡담)로 보낸다.
            # Gemma 워커의 글별 확정 배정이 이후에 바로잡는다.
            cats = db.load_categories()
            if cats:
                return max(cats, key=lambda c: c["post_count"])["name"]
            return post["title"][:30].strip()
        # 모델이 후보를 살짝 바꿔 말해도 후보와 일치시키기 (증식 억제)
        for c in candidates:
            if name == c["name"] or name in c["name"] or c["name"] in name:
                return c["name"]
        return name[:40]


def _sanitize(text: str) -> str:
    # 채널/특수 토큰 정리 (사고 채널만 있고 최종답이 없으면 "" = 폴백 신호)
    from app.services.mlx_runtime import extract_final_channel

    text = extract_final_channel(text)
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    line = re.sub(r'^[-*\d.\)\s"\'`]+|["\'`]+$', "", line).strip()
    # 카테고리 이름으로 부적합한 잔여물 거부 (토큰 파편, 채널명, 너무 짧음)
    if "<" in line or "|" in line or len(line) < 2:
        return ""
    if line.lower() in {"thought", "final", "analysis", "message"}:
        return ""
    return line


# ── 배정 엔진 ────────────────────────────────────────────────────────

def _candidates(vec: np.ndarray, categories: list[dict], k: int) -> list[dict]:
    scored = [
        {"id": c["id"], "name": c["name"], "sim": float(np.dot(vec, c["centroid"]))}
        for c in categories
    ]
    scored.sort(key=lambda x: -x["sim"])
    return scored[:k]


def assign_posts(posts: list[dict], game_id: str | None = None) -> dict:
    """카테고리 미배정 글들을 배정. posts: [{id,title,text,embedding[,game_id]}]

    카테고리는 game_id 파티션이라 그 게임의 카테고리 안에서만 배정/생성한다.
    반환: {assigned_fast, assigned_llm, created} 카운트
    """
    game_id = game_id or settings.default_game_id
    categorizer = get_categorizer()
    categories = db.load_categories(game_id)
    stats = {"assigned_fast": 0, "assigned_llm": 0, "created": 0}

    for post in posts:
        vec = post["embedding"]
        cands = _candidates(vec, categories, settings.category_candidates_k)

        if cands and cands[0]["sim"] >= settings.category_assign_sim:
            target_id = cands[0]["id"]                      # 1차: 벡터 즉시 배정
            stats["assigned_fast"] += 1
        else:
            name = categorizer.pick_or_create_name(post, cands)  # 2차: LLM 보조
            existing = next((c for c in categories if c["name"] == name), None) \
                or db.find_category_by_name(name, game_id)
            if existing:
                target_id = existing["id"]
                stats["assigned_llm"] += 1
            else:
                target_id = db.create_category(name, vec, game_id)  # 신규 카테고리
                categories = db.load_categories(game_id)            # 캐시 갱신
                stats["created"] += 1

        db.assign_category(post["id"], target_id, game_id)

        # 중심벡터 이동 평균 갱신 + 재정규화.
        # ⚠️ post_count 가 상한을 넘으면 동결 — 계속 갱신하면 중심이 "게시판
        # 평균 글" 방향으로 표류해 잡다한 글을 다 흡수하는 블랙홀이 된다.
        cat = next(c for c in categories if c["id"] == target_id)
        n = cat["post_count"]
        if n < settings.category_centroid_max_n:
            c_new = (cat["centroid"] * n + vec) / (n + 1)
            norm = np.linalg.norm(c_new)
            if norm > 0:
                c_new = c_new / norm
            cat["centroid"] = c_new.astype(np.float32)
        cat["post_count"] = n + 1
        db.update_category_centroid(target_id, cat["centroid"], cat["post_count"])

    return stats


# ── 전체 재분류 (유지보수) ──────────────────────────────────────────

# ⚠️ 사고 채널을 막지 말 것: "즉시 출력" 지시는 단어 기반 얕은 판정을 유발한다
# (실측 2026-07-06/07: '환불' 단어→결제 문의, '방덱' 욕설→덱 논쟁 오배정 다수).
# 도메인 컨텍스트(반어·밈)와 단문→잡담 규칙도 분석 워커와 동일하게 필수.
_RECLASSIFY_SYSTEM_TMPL = (
    "너는 게임 게시판 글 분류기다.\n{domain}\n\n"
    "각 글을 주어진 카테고리 중 정확히 하나로 분류한다 (충분히 생각한 뒤 답하라).\n"
    "- 표면 단어가 아니라 글이 실제로 말하는 것으로 판단하라 "
    "(예: '환불각' 같은 상투적 푸념은 결제·환불 문의가 아니다)\n"
    "- 내용이 불분명한 한두 단어 글·밈·드립·인사·근황은 유머·잡담 계열로\n"
    "- 글마다 '글번호: 카테고리번호' 형식으로 한 줄씩 출력한다\n"
    "[출력 예시]\n1: 3\n2: 1\n3: 12"
)


def reclassify_window(hours: int = 72, batch_size: int = 10,
                      max_sim: float | None = None) -> dict:
    """고정된 현재 카테고리 체계로 창 내 글을 LLM 배치 재분류.

    콜드스타트(카테고리가 몇 개 없던 시기)에 잘못 배정된 글들을 교정한다.
    max_sim 지정 시 자기 카테고리 중심과 유사도가 그 미만인 '의심 글'만 대상
    (전체 재분류 없이 오배정 교정 패스만 돌릴 때 — 분포 하위 ~20%가 0.62 부근).
    끝나면 각 카테고리 중심벡터를 교정된 구성원 기준으로 재계산한다.
    """
    from app.services.gemma_analyze import DOMAIN_CONTEXT
    from app.services.mlx_runtime import generate_text

    cats = db.load_categories()
    if not cats:
        return {"error": "카테고리 없음"}
    cat_list = "\n".join(f"{i+1}. {c['name']}" for i, c in enumerate(cats))
    idx_to_id = {i + 1: c["id"] for i, c in enumerate(cats)}
    system = _RECLASSIFY_SYSTEM_TMPL.format(domain=DOMAIN_CONTEXT)

    posts = [p for p in db.load_recent_posts(hours) if p.get("category_id") is not None]
    if max_sim is not None:
        cen_by_id = {c["id"]: c["centroid"] for c in cats}
        posts = [
            p for p in posts
            if p.get("embedding") is not None and p["category_id"] in cen_by_id
            and float(np.dot(p["embedding"], cen_by_id[p["category_id"]])) < max_sim
        ]
    moved, kept, failed = 0, 0, 0

    for start in range(0, len(posts), batch_size):
        chunk = posts[start:start + batch_size]
        listing = "\n".join(
            f"{n}. {p['title']} — {(p.get('text') or '')[:400]}"
            for n, p in enumerate(chunk, 1)
        )
        user = f"[카테고리]\n{cat_list}\n\n[글 목록]\n{listing}"
        from app.services.mlx_runtime import extract_final_channel

        answers: dict[int, int] = {}
        for attempt in range(2):   # 사고 채널로 새서 빈 답이 오면 1회 재시도
            out = generate_text(
                settings.categorizer_model,
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                # 사고 채널이 토큰을 먼저 소비하므로 여유를 크게 (결과 자체는 짧음)
                max_tokens=60 * len(chunk) + settings.reclassify_extra_tokens,
            )
            for line in extract_final_channel(out).splitlines():
                m = re.match(r"\s*(\d+)\s*[:.\-]\s*(\d+)", line)
                if m:
                    answers[int(m.group(1))] = int(m.group(2))
            if answers:
                break

        for n, p in enumerate(chunk, 1):
            cat_idx = answers.get(n)
            if cat_idx not in idx_to_id:
                failed += 1                 # 파싱 실패 → 기존 배정 유지
                continue
            new_id = idx_to_id[cat_idx]
            if new_id != p["category_id"]:
                db.assign_category(p["id"], new_id)
                moved += 1
            else:
                kept += 1

    _rebuild_centroids(hours)
    return {"total": len(posts), "moved": moved, "kept": kept, "parse_failed": failed}


def _rebuild_centroids(hours: int) -> None:
    """교정된 배정 기준으로 카테고리 중심벡터·글 수를 재계산."""
    posts = [p for p in db.load_recent_posts(hours)
             if p.get("category_id") is not None and p.get("embedding") is not None]
    by_cat: dict[int, list[np.ndarray]] = {}
    for p in posts:
        by_cat.setdefault(p["category_id"], []).append(p["embedding"])
    for c in db.load_categories():
        vecs = by_cat.get(c["id"])
        if not vecs:
            continue
        centroid = np.mean(vecs[-settings.category_centroid_max_n:], axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        db.update_category_centroid(c["id"], centroid.astype(np.float32), len(vecs))


_categorizer: Categorizer | None = None


def get_categorizer() -> Categorizer:
    global _categorizer
    if _categorizer is None:
        if settings.categorizer_backend == "mlx":
            _categorizer = MLXCategorizer()
        else:
            _categorizer = StubCategorizer()
    return _categorizer
