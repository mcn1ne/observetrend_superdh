"""adapters4 라벨을 임베딩 입력으로 쓰면 주제 묶기가 나아지는가 — 오프라인 비교 실험.

세 가지 임베딩 입력을 같은 글 집합·같은 묶기 알고리즘(_greedy_groups)으로 비교한다.

  A (대조군)  posts.text                     ← 현재 라이브 방식. 저장된 BLOB 재사용
  B           topic_label 단독               ← "정리된 내용으로 임베딩" 제안안
  C           posts.text + "\\n" + label     ← 결합안

⚠️ 단일 임계값 비교는 불공정하다. 세 안은 유사도 분포의 스케일이 다르므로
   (이 프로젝트의 기존 교훈: "캘리브레이션은 용도에 맞는 분포로") 임계값을 스윕해
   "주제 수가 같아지는 지점"과 "최대 주제 크기가 같아지는 지점"에서 품질을 비교한다.

⚠️ 응집도는 반드시 A 공간(원문 임베딩)에서 잰다. B를 B 공간에서 재면 같은 문자열이
   유사도 1.0이라 자기 공간에서만 유리하게 나온다.

사용:
  uv run python scripts/exp_label_embedding.py                      # 기본 3개 창
  uv run python scripts/exp_label_embedding.py --window 2026-07-10  # 단일 창
  uv run python scripts/exp_label_embedding.py --no-cache           # 임베딩 캐시 무시

⚠️ pipeline.db는 read-only(mode=ro)로 연다 — 서버가 떠 있어도 잠금 충돌이 없고,
   이 스크립트는 DB에 아무것도 쓰지 않는다. 산출물은 --out 디렉터리 아래에만 생긴다.
⚠️ 서버 가동 중 실행하면 KURE-v1이 별도 프로세스에 한 번 더 로드된다 (메모리 주의).
"""
import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.topics import _greedy_groups

# 라벨이 100% 채워진 구간(2026-07-09~14) 중 하루 800건 이상인 날.
# 체리피킹 방지로 기본 3개 창을 돌린다 — 결론이 창마다 뒤집히면 그 사실을 리포트에 적는다.
DEFAULT_WINDOWS = ("2026-07-10", "2026-07-12", "2026-07-14")

VARIANTS = ("A", "B", "C")
VARIANT_DESC = {
    "A": "posts.text (현재 라이브)",
    "B": "topic_label 단독",
    "C": "posts.text + topic_label",
}

SWEEP = [round(t, 3) for t in np.arange(0.45, 0.9001, 0.025)]
SHORT_QUANTILE = 0.25     # 짧은 글 진단: text 길이 하위 25%


# ── 데이터 적재 (read-only) ──────────────────────────────────────────

def load_window(day: str, game_id: str) -> list[dict]:
    """하루치 창. 세 안이 완전히 같은 글 집합을 봐야 공정하므로 label 보유 글만."""
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT p.id, p.title, p.text, p.created_at, p.embedding, "
        "       a.topic_label, a.major "
        "FROM posts p JOIN post_analysis a "
        "  ON a.game_id = p.game_id AND a.post_id = p.id "
        "WHERE p.game_id = ? AND p.embedding IS NOT NULL "
        "  AND p.text IS NOT NULL AND a.topic_label IS NOT NULL "
        "  AND substr(p.created_at, 1, 10) = ? "
        "ORDER BY p.created_at",           # ⚠️ 시간순 필수 — _greedy_groups가 입력순으로 훑는다
        (game_id, day),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 임베딩 (안별) ────────────────────────────────────────────────────

def embed_variant(rows: list[dict], variant: str, cache_dir: Path | None) -> np.ndarray:
    """안에 해당하는 (N, d) 정규화 벡터. A는 저장된 BLOB 재사용 — 재계산 없음."""
    if variant == "A":
        return np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])

    cache = cache_dir / f"{variant}.npy" if cache_dir else None
    if cache and cache.is_file():
        vecs = np.load(cache)
        if len(vecs) == len(rows):
            print(f"    [{variant}] 캐시 재사용 ({len(vecs)}건)")
            return vecs
        print(f"    [{variant}] 캐시 건수 불일치 — 다시 인코딩")

    from app.services.embedding import get_embedder

    embedder = get_embedder()
    t0 = time.monotonic()

    if variant == "B":
        # 고유 라벨만 인코딩 후 매핑 — 6,024건이 아니라 ~1,495건이면 된다
        uniq = sorted({r["topic_label"] for r in rows})
        print(f"    [B] 고유 라벨 {len(uniq)}개 인코딩 (글 {len(rows)}건)")
        table = dict(zip(uniq, embedder.encode(uniq)))
        vecs = np.stack([table[r["topic_label"]] for r in rows])
    else:  # C
        texts = [f"{r['text']}\n{r['topic_label']}" for r in rows]
        print(f"    [C] 결합 텍스트 {len(texts)}건 인코딩")
        vecs = embedder.encode(texts)

    vecs = np.asarray(vecs, dtype=np.float32)
    print(f"    [{variant}] {time.monotonic() - t0:.1f}초")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, vecs)
    return vecs


# ── 지표 ─────────────────────────────────────────────────────────────

def cohesion(idxs: list[int], emb_a: np.ndarray) -> float:
    """멤버 쌍 평균 코사인 — 항상 A 공간에서 잰다.

    단위벡터 V(n×d)의 비정규화 평균 m 에 대해 ΣᵢΣⱼ vᵢ·vⱼ = n²|m|² 이므로
    (대각 n개는 자기 자신 = 1), 쌍 평균 = (n²|m|² − n) / (n(n−1)). O(n·d).
    """
    n = len(idxs)
    if n < 2:
        return 1.0
    m = emb_a[idxs].mean(axis=0)
    total = (n ** 2) * float(np.dot(m, m))
    return (total - n) / (n * (n - 1))


def measure(groups: list[list[int]], emb_a: np.ndarray, n_total: int) -> dict:
    if not groups:
        return {"n_topics": 0, "assigned": 0, "assign_rate": 0.0, "max_size": 0,
                "max_share": 0.0, "median_size": 0, "cohesion": 0.0}
    sizes = sorted(len(g) for g in groups)
    assigned = sum(sizes)
    cohs = [cohesion(g, emb_a) for g in groups]
    weights = [len(g) for g in groups]
    return {
        "n_topics": len(groups),
        "assigned": assigned,
        "assign_rate": round(assigned / n_total, 3),
        "max_size": sizes[-1],
        "max_share": round(sizes[-1] / assigned, 3),
        "median_size": sizes[len(sizes) // 2],
        # 글 수 가중 평균 — 큰 블롭의 낮은 응집도가 묻히지 않게
        "cohesion": round(float(np.average(cohs, weights=weights)), 4),
    }


def merge_factor(groups: list[list[int]], ref_groups: list[list[int]]) -> dict:
    """이 안의 주제 하나가 대조군(A) 주제 몇 개를 합치고 있는가."""
    ref_of = {}
    for gi, idxs in enumerate(ref_groups):
        for i in idxs:
            ref_of[i] = gi
    spans = []
    for idxs in groups:
        refs = {ref_of[i] for i in idxs if i in ref_of}
        if refs:
            spans.append(len(refs))
    if not spans:
        return {"mean": 0.0, "max": 0}
    return {"mean": round(sum(spans) / len(spans), 2), "max": max(spans)}


def short_post_concentration(groups: list[list[int]], rows: list[dict]) -> float:
    """짧은 글이 한 주제에 몰린 비율 — 캡션 정형화 사고(52건 필러) 재현 여부."""
    lengths = np.array([len(r["text"] or "") for r in rows])
    if len(lengths) == 0:
        return 0.0
    cut = float(np.quantile(lengths, SHORT_QUANTILE))
    short = {i for i, L in enumerate(lengths) if L <= cut}
    if not short:
        return 0.0
    counts = Counter()
    for gi, idxs in enumerate(groups):
        counts[gi] = sum(1 for i in idxs if i in short)
    if not counts:
        return 0.0
    return round(max(counts.values()) / len(short), 3)


def pick_at(sweep_rows: list[dict], key: str, target: float) -> dict | None:
    """목표값에 가장 가까운 스윕 지점 (동일 조건 비교용)."""
    usable = [r for r in sweep_rows if r["n_topics"] > 0]
    if not usable:
        return None
    return min(usable, key=lambda r: abs(r[key] - target))


# ── 실행 ─────────────────────────────────────────────────────────────

def run_window(day: str, game_id: str, out_dir: Path, use_cache: bool) -> dict | None:
    rows = load_window(day, game_id)
    print(f"\n── 창 {day} — 글 {len(rows)}건 ─────────────────────")
    if len(rows) < settings.topic_min_size * 3:
        print(f"    글이 너무 적어 건너뜀 (최소 {settings.topic_min_size * 3}건 필요)")
        return None

    cache_dir = (out_dir / "cache" / day) if use_cache else None
    embeds = {v: embed_variant(rows, v, cache_dir) for v in VARIANTS}

    result = {"window": day, "n_posts": len(rows), "variants": {}}
    groups_at = {}

    for v in VARIANTS:
        sweep_rows = []
        for t in SWEEP:
            groups = _greedy_groups(embeds[v], t)
            stat = measure(groups, embeds["A"], len(rows))
            stat["threshold"] = t
            sweep_rows.append(stat)
            groups_at[(v, t)] = groups
        result["variants"][v] = {"desc": VARIANT_DESC[v], "sweep": sweep_rows}

    # 대조군 기준점: A를 라이브 임계값(topic_link_sim)에서 돌린 결과
    base_t = settings.topic_link_sim
    base = next(r for r in result["variants"]["A"]["sweep"]
                if abs(r["threshold"] - base_t) < 1e-9)
    base_groups = groups_at[("A", base["threshold"])]
    result["baseline"] = base

    for v in VARIANTS:
        sweep_rows = result["variants"][v]["sweep"]
        matched = {}
        for label, key, target in (
            ("same_topic_count", "n_topics", base["n_topics"]),
            ("same_max_size", "max_size", base["max_size"]),
        ):
            hit = pick_at(sweep_rows, key, target)
            if hit is None:
                matched[label] = None
                continue
            g = groups_at[(v, hit["threshold"])]
            matched[label] = {
                **hit,
                "merge_factor": merge_factor(g, base_groups),
                "short_concentration": short_post_concentration(g, rows),
            }
        result["variants"][v]["matched"] = matched

    return result


def fmt_row(v: str, m: dict | None) -> str:
    if m is None:
        return f"| {v} | — | — | — | — | — | — |"
    mf = m["merge_factor"]
    return (f"| {v} | {m['threshold']:.3f} | {m['n_topics']} | {m['max_size']} "
            f"({m['max_share']:.0%}) | {m['cohesion']:.4f} | "
            f"{mf['mean']} (최대 {mf['max']}) | {m['short_concentration']:.0%} |")


def write_report(results: list[dict], out_dir: Path) -> Path:
    L: list[str] = []
    L.append("# adapters4 라벨 임베딩 비교 실험\n")
    L.append(f"- 묶기 알고리즘: `app.services.topics._greedy_groups` (라이브와 동일)")
    L.append(f"- `topic_min_size` = {settings.topic_min_size}, "
             f"대조군 기준 임계값 = {settings.topic_link_sim}")
    L.append(f"- 응집도는 **항상 A 공간(원문 임베딩)** 에서 측정 (글 수 가중 평균)")
    L.append(f"- 임계값 스윕: {SWEEP[0]} ~ {SWEEP[-1]} (0.025 간격)\n")
    L.append("| 안 | 임베딩 입력 |")
    L.append("|---|---|")
    for v in VARIANTS:
        L.append(f"| {v} | {VARIANT_DESC[v]} |")
    L.append("")

    for res in results:
        L.append(f"\n## 창 {res['window']} — 글 {res['n_posts']}건\n")
        b = res["baseline"]
        L.append(f"대조군 A @ {b['threshold']:.2f}: 주제 **{b['n_topics']}개**, "
                 f"최대 주제 **{b['max_size']}건({b['max_share']:.0%})**, "
                 f"배정률 {b['assign_rate']:.0%}, 응집도 **{b['cohesion']:.4f}**\n")

        for label, title in (("same_topic_count", "주제 수를 A와 맞춘 지점"),
                             ("same_max_size", "최대 주제 크기를 A와 맞춘 지점")):
            L.append(f"### {title}\n")
            L.append("| 안 | 임계값 | 주제 수 | 최대 주제 | 응집도 | A주제 병합 배수 | 짧은글 집중 |")
            L.append("|---|---|---|---|---|---|---|")
            for v in VARIANTS:
                L.append(fmt_row(v, res["variants"][v]["matched"][label]))
            L.append("")

        L.append("### 임계값 스윕 (주제 수 / 최대 주제 크기 / 응집도)\n")
        L.append("| 임계값 | " + " | ".join(f"{v}" for v in VARIANTS) + " |")
        L.append("|---|" + "---|" * len(VARIANTS))
        by_t = {v: {r["threshold"]: r for r in res["variants"][v]["sweep"]} for v in VARIANTS}
        for t in SWEEP:
            cells = []
            for v in VARIANTS:
                r = by_t[v][t]
                cells.append(f"{r['n_topics']} / {r['max_size']} / {r['cohesion']:.3f}"
                             if r["n_topics"] else "—")
            L.append(f"| {t:.3f} | " + " | ".join(cells) + " |")
        L.append("")

    L.append("\n## 읽는 법\n")
    L.append("- **응집도**가 높을수록 한 주제 안의 글들이 실제로 서로 비슷하다는 뜻이다.")
    L.append("- **A주제 병합 배수**는 그 안의 주제 하나가 대조군 주제 몇 개를 뭉뚱그리는지다."
             " 1.0에 가까울수록 대조군과 같은 해상도, 클수록 구별을 잃은 것이다.")
    L.append("- **짧은글 집중**은 길이 하위 25% 글이 한 주제에 몰린 비율이다."
             " 높으면 캡션 정형화 사고(무관한 단문들이 한 덩어리가 된 건)의 재현 신호다.")
    L.append("- **최대 주제 (%)** 가 크면 눈덩이 블롭이다"
             " (실측 전례: 멤버 유사도 0.577짜리 156건 블롭).")

    out = out_dir / "report.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="adapters4 라벨 임베딩 비교 실험 (오프라인, DB 읽기 전용)")
    ap.add_argument("--window", action="append", default=None,
                    help="창 날짜 YYYY-MM-DD (여러 번 지정 가능). 기본: "
                         + ", ".join(DEFAULT_WINDOWS))
    ap.add_argument("--game", type=str, default=settings.default_game_id)
    ap.add_argument("--no-cache", action="store_true", help="임베딩 캐시 무시하고 재인코딩")
    ap.add_argument("--out", type=str,
                    default=str(Path(settings.db_path).parent / "exp" / "label-embedding"))
    args = ap.parse_args()

    windows = args.window or list(DEFAULT_WINDOWS)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"DB(read-only): {settings.db_path}")
    print(f"창: {', '.join(windows)} · 게임: {args.game}")
    print(f"산출: {out_dir}")

    results = [r for r in (run_window(d, args.game, out_dir, not args.no_cache)
                           for d in windows) if r]
    if not results:
        print("\n분석할 창이 없습니다.")
        return

    (out_dir / "raw.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    report = write_report(results, out_dir)

    print("\n" + "=" * 64)
    for res in results:
        b = res["baseline"]
        line = [f"창 {res['window']}  A: 주제 {b['n_topics']}개 · "
                f"최대 {b['max_size']}({b['max_share']:.0%}) · 응집 {b['cohesion']:.4f}"]
        for v in ("B", "C"):
            m = res["variants"][v]["matched"]["same_topic_count"]
            if m:
                line.append(f"    {v} (주제수 맞춤 @{m['threshold']:.3f}): "
                            f"최대 {m['max_size']}({m['max_share']:.0%}) · "
                            f"응집 {m['cohesion']:.4f} · "
                            f"병합배수 {m['merge_factor']['mean']}")
        print("\n".join(line))
    print("=" * 64)
    print(f"리포트: {report}")
    print(f"원자료: {out_dir / 'raw.json'}")


if __name__ == "__main__":
    main()
