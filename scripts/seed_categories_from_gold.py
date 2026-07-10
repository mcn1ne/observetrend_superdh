"""카테고리 초기화 + 하이브리드 시드 — 정답셋(동향) + 상시 대분류.

⚠️ 서버를 내린 상태에서 실행할 것 (DB 직접 쓰기 + 라이브 배정과 경합 방지).
⚠️ 실행 전 pipeline.db 백업 필수.

절차:
  1. 기존 카테고리에서 상시 대분류 시드의 중심벡터를 스냅샷 (삭제 전에)
  2. data/gold/ 에서 동향(nature=동향) 클러스터의 검증 멤버로 중심벡터 계산
  3. categories·category_scores 삭제, posts.category_id 초기화
  4. 시드 12개 생성 + 전체 글을 '유머·잡담'에 임시 배정
     → 이후 서버 띄우고 POST /api/maintenance/reclassify 로 전량 재분류
       (reclassify는 category_id IS NULL 글을 건너뛰므로 임시 배정이 필수)

alerts(피드백 학습 데이터)와 post_analysis(감정·긴급도)는 보존한다.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

GOLD_DIR = Path(settings.db_path).parent / "gold"

# 상시 대분류 — 기존 카테고리의 검증된 중심벡터를 이름으로 찾아 재사용.
# (07-05 reclassify 교정을 거친 체계라 상위 카테고리 중심은 신뢰 가능)
ARCHETYPES = [
    "유머·잡담",
    "덱 구성 질문",
    "캐릭터 밸런스 불만",
    "버그·오류 제보",
    "스토리 감상",
    "결제·환불 문의",
    "길드 운영 관련 문의",
    "유저 비매너 신고",
]
# 병합 시드: 새 이름 ← 기존 카테고리 여러 개의 중심 가중 평균
MERGED = {"뽑기·캐릭터 수급": ["뽑기 결과 공유", "캐릭터 수급 고민"]}

CATCH_ALL = "유머·잡담"   # 임시 배정 대상 (reclassify 파싱 실패 시에도 여기 잔류)


def normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return (v / n if n > 0 else v).astype(np.float32)


def main() -> None:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row

    # 1) 삭제 전에 필요한 중심벡터 전부 스냅샷 ------------------------
    existing = {
        r["name"]: (np.frombuffer(r["centroid"], dtype=np.float32),
                    (conn.execute("SELECT COUNT(*) FROM posts WHERE category_id=?",
                                  (r["id"],)).fetchone()[0]))
        for r in conn.execute("SELECT id, name, centroid FROM categories")
    }
    seeds: dict[str, np.ndarray] = {}
    for name in ARCHETYPES:
        if name not in existing:
            sys.exit(f"상시 대분류 '{name}' 가 기존 카테고리에 없음 — 이름 확인 필요")
        seeds[name] = existing[name][0]
    for new_name, parts in MERGED.items():
        vecs, weights = [], []
        for p in parts:
            if p not in existing:
                sys.exit(f"병합 대상 '{p}' 가 기존 카테고리에 없음")
            vecs.append(existing[p][0])
            weights.append(max(existing[p][1], 1))
        seeds[new_name] = normalize(np.average(vecs, axis=0, weights=weights))

    # 2) 정답셋 동향 클러스터 → 검증 멤버 임베딩 평균 ------------------
    clusters = [json.loads(l) for l in (GOLD_DIR / "clusters.jsonl").read_text().splitlines()]
    labels = [json.loads(l) for l in (GOLD_DIR / "labels.jsonl").read_text().splitlines()]
    members: dict[int, list[str]] = {}
    for r in labels:
        for lb in r["labels"]:
            if lb["source"] == "hdbscan+llm":        # 구출분 제외 — 검증 핵만으로 중심
                members.setdefault(lb["cluster"], []).append(r["post_id"])
    n_trends = 0
    for c in clusters:
        if c["nature"] != "동향":
            continue
        ids = members.get(c["cluster"], [])
        rows = conn.execute(
            f"SELECT embedding FROM posts WHERE id IN ({','.join('?' * len(ids))}) "
            "AND embedding IS NOT NULL", ids).fetchall()
        if len(rows) < 2:
            print(f"⚠️ 동향 「{c['name']}」 멤버 임베딩 부족({len(rows)}) — 시드 제외")
            continue
        vecs = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        if c["name"] in seeds:
            print(f"⚠️ 동향 「{c['name']}」 이 상시 대분류와 이름 충돌 — 동향 중심으로 대체")
        seeds[c["name"]] = normalize(np.mean(vecs, axis=0))
        n_trends += 1

    # 3) 초기화 + 4) 시드 생성·임시 배정 (한 트랜잭션) ------------------
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("DELETE FROM category_scores")
        conn.execute("DELETE FROM categories")
        conn.execute("UPDATE posts SET category_id = NULL")
        catch_all_id = None
        for name, centroid in seeds.items():
            cur = conn.execute(
                "INSERT INTO categories (name, centroid, post_count, created_at, updated_at) "
                "VALUES (?, ?, 0, ?, ?)", (name, centroid.tobytes(), now, now))
            if name == CATCH_ALL:
                catch_all_id = cur.lastrowid
        n_posts = conn.execute(
            "UPDATE posts SET category_id = ?", (catch_all_id,)).rowcount

    print(f"초기화 완료: 기존 카테고리 {len(existing)}개 삭제")
    print(f"시드 {len(seeds)}개 생성 (상시 {len(seeds) - n_trends} + 동향 {n_trends}):")
    for name in seeds:
        print(f"  - {name}")
    print(f"글 {n_posts}건 → '{CATCH_ALL}' 임시 배정. 다음 단계:")
    print("  서버 기동 후 curl -X POST 'http://localhost:8007/api/maintenance/reclassify?hours=336'")


if __name__ == "__main__":
    main()
