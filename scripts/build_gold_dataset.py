"""동향 정답 데이터셋 구축 — 오프라인 배치 (라이브 파이프라인과 별개).

깔때기:
  [전체 글] → HDBSCAN 1차 클러스터링
      ├─ 클러스터 멤버 → Gemma 4 정밀 검증 (O/X + X 사유: 복합/단문/뻘글/무관)
      └─ 노이즈(-1)   → ⚠️ 기본 제외하지 않고 구출 후보로 보냄
  [구출] LLM-X 풀 + 노이즈 풀 전부 → 문장 쪼개기 → 검증된 클러스터 중심과
         코사인 유사도 ≥ t → 해당 클러스터로 구출 (한 글이 여러 클러스터 가능)
  [산출] 멀티라벨 정답셋 (글 단위) + 문장 단위 상세 + 부정 클래스 + 리포트

임계값 t는 재사용하지 않는다: 문장조각↔중심 분포는 글↔글·글↔중심과 다르므로
(실측 교훈: 캘리브레이션은 용도에 맞는 분포로) 이 스크립트가 검증된 멤버의
문장으로 양성/음성 분포를 직접 재서 고른다. 분포가 안 갈리면 구출을 중단하고
경고한다 — "대분류는 벡터로 불가" 전례가 문장 단위에서도 재현될 수 있다.

사용:
  uv run python scripts/build_gold_dataset.py                # 전체 글, LLM 검증 포함
  uv run python scripts/build_gold_dataset.py --hours 48     # 최근 48h만
  uv run python scripts/build_gold_dataset.py --no-llm       # 배선 확인 (검증 생략)
  uv run python scripts/build_gold_dataset.py --resume       # 검증 체크포인트 이어가기

⚠️ LLM 검증은 배치당 ~40초(사고 채널 포함) — 시작 전에 예상 소요를 출력한다.
⚠️ pipeline.db는 read-only(mode=ro)로 연다 — 서버가 떠 있어도 잠금 충돌 없음.
"""
import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.gemma_analyze import DOMAIN_CONTEXT

VERIFY_BATCH = 5          # gemma_batch_size와 동일 근거 (건성 라벨링 방지)
EXEMPLARS_N = 8           # 클러스터 명명·검증 기준으로 보여줄 대표글 수
MIN_SEG_CHARS = 6         # 이보다 짧은 문장 조각은 임베딩이 노이즈라 버림
SEC_PER_LLM_CALL = 40     # 실측: 사고 채널 포함 배치당 소요 (ETA 표시용)

REASONS = ("복합", "단문", "뻘글", "무관")


# ── 데이터 적재 (read-only) ──────────────────────────────────────────

def load_posts(hours: int) -> list[dict]:
    """embedding이 있는 글 전부(또는 최근 창). 서버와 잠금 충돌 방지로 mode=ro."""
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    sql = "SELECT id, title, text, created_at, embedding FROM posts WHERE embedding IS NOT NULL"
    params: tuple = ()
    if hours > 0:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sql += " AND created_at > ?"
        params = (since,)
    rows = conn.execute(sql + " ORDER BY created_at", params).fetchall()
    conn.close()

    posts = []
    for r in rows:
        p = dict(r)
        p["embedding"] = np.frombuffer(p["embedding"], dtype=np.float32)
        posts.append(p)
    if not posts:
        return posts
    # 임베딩 모델 교체 이력이 있으면 차원이 섞여 있을 수 있다 — 다수 차원만 사용
    dims = defaultdict(int)
    for p in posts:
        dims[len(p["embedding"])] += 1
    major = max(dims, key=dims.get)
    kept = [p for p in posts if len(p["embedding"]) == major]
    if len(kept) < len(posts):
        print(f"⚠️ 임베딩 차원 혼재 {dict(dims)} — {major}차원 {len(kept)}건만 사용")
    return kept


# ── 1차 클러스터링 ───────────────────────────────────────────────────

def run_hdbscan(embeddings: np.ndarray, min_cluster_size: int) -> np.ndarray:
    from sklearn.cluster import HDBSCAN  # uv sync --extra ml

    return HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean").fit_predict(embeddings)


# ── LLM 검증 (클러스터 명명 → 멤버 O/X + X 사유) ─────────────────────

_NAME_SYS = (
    "너는 게임 커뮤니티 게시판의 동향 분석가다.\n" + DOMAIN_CONTEXT + "\n\n"
    "아래 글 목록이 하나의 클러스터다. '이름|성격' 형식 한 줄만 출력하라.\n"
    "- 이름: 한국어 2~4어절의 일반화된 명사구. 특정 글 제목 복사 금지.\n"
    "  좋은 예: '뽑기 확률 불만', '신규 전장 공략 질문' / 나쁜 예: 제목 그대로, 한 단어\n"
    "- 성격: 특정 사건·주제·이슈가 있으면 '동향', 정서·밈·잡담의 덩어리일 뿐이면 '잡담'\n"
    "[예시] 뽑기 확률 불만|동향  /  신세 한탄 푸념|잡담"
)

_VERIFY_SYS_TMPL = (
    "너는 게임 커뮤니티 게시판의 동향 분석가다.\n" + DOMAIN_CONTEXT + "\n\n"
    "동향(클러스터): 「{name}」\n대표 글:\n{exemplars}\n\n"
    "아래 [검증 목록]의 각 글이 이 동향에 속하는지 판정한다 (충분히 생각한 뒤 답하라).\n"
    "글마다 아래 형식으로 한 줄씩, 반드시 2개 필드를 출력한다.\n"
    "글번호: O|해당   ← 이 동향에 속함\n"
    "글번호: X|사유   ← 속하지 않음. 사유는 넷 중 하나:\n"
    "- 복합: 여러 주제가 섞인 글이라 일부만 이 동향과 관련\n"
    "- 단문: 너무 짧아 어떤 동향인지 판단 불가\n"
    "- 뻘글: 잡담·드립·밈 (특정 동향 없음)\n"
    "- 무관: 내용은 명확하나 이 동향과 다른 주제\n\n"
    "[판정 예시 — 반드시 2개 필드]\n"
    "1. O|해당\n"
    "2. X|복합\n"
    "3. X|뻘글"
)

_VERIFY_LINE = re.compile(r"\s*(\d+)\s*[:.]\s*([OX])\s*\|\s*(\S+)")


def _llm(messages: list[dict], max_tokens: int) -> str:
    """Gemma 4 호출 + 최종 채널 추출. 빈 답(사고만 하다 잘림)은 1회 재시도."""
    from app.services.mlx_runtime import extract_final_channel, generate_text

    for _ in range(2):
        out = extract_final_channel(
            generate_text(settings.categorizer_model, messages, max_tokens=max_tokens)
        )
        if out:
            return out
    return ""


def name_cluster(exemplars: list[dict]) -> tuple[str, str]:
    """→ (이름, 성격). 성격이 '잡담'인 클러스터는 구출 대상 중심에서 제외된다
    (잡담 중심은 노이즈 뻘글과도 유사도가 높아 구출 단계의 블랙홀이 됨 — 실측)."""
    listing = "\n".join(f"- {p['title']}" for p in exemplars)
    out = _llm(
        [{"role": "system", "content": _NAME_SYS},
         {"role": "user", "content": f"[글 목록]\n{listing}"}],
        max_tokens=1500,  # 사고 채널 여유
    )
    line = out.strip().strip('"\'` ').splitlines()[0] if out else ""
    name, _, kind = line.partition("|")
    name, kind = name.strip()[:40], kind.strip()
    if len(name) < 2:
        return "(명명 실패)", "잡담"          # 명명 실패 클러스터로 구출하지 않음
    return name, (kind if kind in ("동향", "잡담") else "동향")


def verify_members(name: str, exemplars: list[dict], members: list[dict]) -> dict[str, dict]:
    """멤버 글 → {post_id: {verdict: 'O'/'X', reason}}. 파싱 실패분은 누락(재시도 없음)."""
    ex_listing = "\n".join(f"- {p['title']}" for p in exemplars)
    system = _VERIFY_SYS_TMPL.format(name=name, exemplars=ex_listing)
    results: dict[str, dict] = {}
    for i in range(0, len(members), VERIFY_BATCH):
        chunk = members[i:i + VERIFY_BATCH]
        listing = "\n".join(
            f"{n}. {p['title']} — {(p.get('text') or '')[:500]}"
            for n, p in enumerate(chunk, 1)
        )
        out = _llm(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"[검증 목록]\n{listing}"}],
            max_tokens=120 * len(chunk) + settings.reclassify_extra_tokens,
        )
        for line in out.splitlines():
            m = _VERIFY_LINE.match(line)
            if not m:
                continue
            n = int(m.group(1))
            if 1 <= n <= len(chunk):
                reason = m.group(3).strip()
                results[chunk[n - 1]["id"]] = {
                    "verdict": m.group(2),
                    "reason": "해당" if m.group(2) == "O" else (reason if reason in REASONS else "무관"),
                }
    return results


# ── 문장 쪼개기 + 캘리브레이션 + 구출 ────────────────────────────────

_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+|(?<=[다요죠까])\s{2,}")


def split_sentences(text: str) -> list[str]:
    segs = [s.strip() for s in _SENT_SPLIT.split(text or "") if s and s.strip()]
    segs = [s for s in segs if len(s) >= MIN_SEG_CHARS]
    # 조각이 하나도 안 남는 단문은 글 전체를 조각 1개로 (단문 구출 경로)
    return segs or ([text.strip()] if text and text.strip() else [])


def calibrate_threshold(
    seg_vecs_by_cluster: dict[int, np.ndarray], centroids: dict[int, np.ndarray],
    junk_vecs: np.ndarray | None = None,
) -> tuple[float | None, dict]:
    """검증된 멤버의 문장조각으로 양성(자기 중심)/음성 분포를 재고 음성
    99퍼센타일을 임계값으로 고른다. 분포가 안 갈리면 (None, 진단) 반환.

    음성은 두 갈래: ① 진짜 멤버 문장 ↔ 남의 중심 최근접 (교차 클러스터),
    ② LLM이 뻘글로 판정한 글의 문장 ↔ 모든 중심 최근접 (진짜 쓰레기 표본).
    클러스터가 2개뿐이면 ①이 빈약해 임계값이 느슨해지므로 ②가 안전판이다."""
    cids = list(centroids)
    if len(cids) < 2:
        return None, {"error": "클러스터 2개 미만 — 음성 분포를 잴 수 없음"}
    C = np.stack([centroids[c] for c in cids])          # (K, dim)
    pos, neg = [], []
    for ci, cid in enumerate(cids):
        vecs = seg_vecs_by_cluster.get(cid)
        if vecs is None or len(vecs) == 0:
            continue
        sims = vecs @ C.T                                # (n_seg, K)
        pos.extend(sims[:, ci].tolist())
        others = np.delete(sims, ci, axis=1)
        neg.extend(others.max(axis=1).tolist())
    n_junk = 0
    if junk_vecs is not None and len(junk_vecs):
        neg.extend((junk_vecs @ C.T).max(axis=1).tolist())
        n_junk = len(junk_vecs)
    if len(pos) < 30 or len(neg) < 30:
        return None, {"error": f"표본 부족 (양성 {len(pos)}, 음성 {len(neg)})"}
    pos_a, neg_a = np.array(pos), np.array(neg)
    t = float(np.percentile(neg_a, 99))                  # 오배정 ~1% 수준으로 억제
    diag = {
        "n_pos": len(pos), "n_neg": len(neg), "n_neg_junk": n_junk,
        "pos_median": round(float(np.median(pos_a)), 4),
        "pos_p25": round(float(np.percentile(pos_a, 25)), 4),
        "neg_median": round(float(np.median(neg_a)), 4),
        "neg_p99": round(t, 4),
        "recall_at_t": round(float((pos_a >= t).mean()), 4),  # 임계값에서 양성 통과율
    }
    # 안 갈리는 분포 가드: 양성 중앙값조차 임계값 미만이면 구출은 동전 던지기다
    if diag["pos_median"] < t:
        diag["error"] = "양성/음성 분포 미분리 — 문장 단위 구출 신뢰 불가 (수동 --sim-threshold 필요)"
        return None, diag
    return t, diag


# ── 메인 ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="동향 정답 데이터셋 구축 (오프라인 배치)")
    ap.add_argument("--hours", type=int, default=0, help="분석 창(시간). 0=전체 (기본)")
    # 기본 10 (라이브 안전망의 5와 다름): 48h 실측에서 5 이하는 잡담 연속체가 다리가
    # 되어 전체가 클러스터 1개로 뭉치고(2,224건 블랙홀), 10이어야 조밀한 동향 시드만
    # 남는다. 시드에서 빠진 글은 노이즈로 가서 구출 단계가 회수하므로 리콜 손실 아님.
    ap.add_argument("--min-cluster-size", type=int, default=10)
    ap.add_argument("--no-llm", action="store_true", help="LLM 검증 생략 (배선 확인용 — 멤버 전원 O 취급)")
    ap.add_argument("--sim-threshold", type=float, default=None, help="구출 임계값 수동 지정 (캘리브레이션 무시)")
    ap.add_argument("--resume", action="store_true", help="검증 체크포인트(verify.jsonl) 이어가기")
    ap.add_argument("--out", type=str, default=str(Path(settings.db_path).parent / "gold"))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = out_dir / "verify.jsonl"

    # 1) 적재 + 1차 클러스터링 ---------------------------------------
    posts = load_posts(args.hours)
    if len(posts) < args.min_cluster_size * 2:
        sys.exit(f"글이 너무 적음 ({len(posts)}건) — --hours 를 늘리거나 0(전체)으로")
    X = np.stack([p["embedding"] for p in posts])
    labels = run_hdbscan(X, args.min_cluster_size)

    clusters: dict[int, list[int]] = defaultdict(list)
    noise_idx: list[int] = []
    for i, lb in enumerate(labels):
        (noise_idx if lb == -1 else clusters[int(lb)]).append(i)
    clusters = dict(clusters)
    n_clustered = sum(len(v) for v in clusters.values())
    print(f"글 {len(posts)}건 → 클러스터 {len(clusters)}개({n_clustered}건) + 노이즈 {len(noise_idx)}건 "
          f"(노이즈 비율 {len(noise_idx) / len(posts):.1%})")

    # 2) LLM 정밀 검증 (클러스터 명명 → 멤버 O/X) ---------------------
    verdicts: dict[str, dict] = {}          # post_id → {verdict, reason, cluster}
    names: dict[int, str] = {}
    kinds: dict[int, str] = {}              # cluster → 동향 | 잡담
    if args.resume and ckpt_path.exists():
        for line in ckpt_path.read_text().splitlines():
            row = json.loads(line)
            if row.get("kind") == "name":
                names[row["cluster"]] = row["name"]
                kinds[row["cluster"]] = row.get("nature", "동향")
            else:
                verdicts[row["post_id"]] = row
        print(f"체크포인트 재개: 명명 {len(names)}개, 판정 {len(verdicts)}건 로드")

    if args.no_llm:
        for cid, idxs in clusters.items():
            names.setdefault(cid, f"cluster-{cid}")
            kinds.setdefault(cid, "동향")
            for i in idxs:
                verdicts.setdefault(posts[i]["id"], {"verdict": "O", "reason": "해당", "cluster": cid})
        print("--no-llm: 검증 생략, 멤버 전원 O 취급 (배선 확인 모드)")
    else:
        n_calls = sum(1 + -(-len(v) // VERIFY_BATCH) for v in clusters.values())
        print(f"LLM 검증 예상: 호출 {n_calls}회 ≈ {n_calls * SEC_PER_LLM_CALL / 3600:.1f}시간")
        ckpt = ckpt_path.open("a")
        for cid, idxs in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
            members = [posts[i] for i in idxs]
            done = [p for p in members if p["id"] in verdicts]
            if len(done) == len(members) and cid in names:
                continue                                     # 체크포인트로 완료된 클러스터
            centroid = X[idxs].mean(axis=0)
            centroid /= np.linalg.norm(centroid) or 1.0
            order = np.argsort(-(X[idxs] @ centroid))        # 중심에 가까운 순
            exemplars = [members[j] for j in order[:EXEMPLARS_N]]
            if cid not in names:
                names[cid], kinds[cid] = name_cluster(exemplars)
                ckpt.write(json.dumps({"kind": "name", "cluster": cid, "name": names[cid],
                                       "nature": kinds[cid]}, ensure_ascii=False) + "\n")
            todo = [p for p in members if p["id"] not in verdicts]
            res = verify_members(names[cid], exemplars, todo)
            for pid, r in res.items():
                row = {"post_id": pid, "cluster": cid, **r}
                verdicts[pid] = row
                ckpt.write(json.dumps(row, ensure_ascii=False) + "\n")
            ckpt.flush()
            n_true = sum(1 for p in members if verdicts.get(p["id"], {}).get("verdict") == "O")
            print(f"  클러스터 {cid} 「{names[cid]}」: {len(members)}건 중 O {n_true}, "
                  f"X {len(res) - (n_true - len(done))}건 판정")
        ckpt.close()

    idx_by_id = {p["id"]: i for i, p in enumerate(posts)}
    true_idx: dict[int, list[int]] = defaultdict(list)
    false_pool: list[tuple[int, str]] = []               # (post_idx, X 사유)
    for pid, v in verdicts.items():
        if pid not in idx_by_id:
            continue
        if v["verdict"] == "O":
            true_idx[v["cluster"]].append(idx_by_id[pid])
        else:
            false_pool.append((idx_by_id[pid], v["reason"]))
    # 파싱 누락분(판정 없음)은 X-무관 취급해 구출 풀로
    judged = set(verdicts)
    for cid, idxs in clusters.items():
        for i in idxs:
            if posts[i]["id"] not in judged:
                false_pool.append((i, "무관"))

    # 검증된 중심벡터 (True 멤버만) — 구출·캘리브레이션 공용.
    # 잡담 성격 클러스터는 제외: 잡담 중심은 노이즈 뻘글과도 0.8+ 유사도가 나와
    # 구출 단계의 블랙홀이 된다 (실측). 멤버 라벨 자체는 유지된다.
    centroids: dict[int, np.ndarray] = {}
    for cid, idxs in true_idx.items():
        if len(idxs) >= 2 and kinds.get(cid) == "동향":
            c = X[idxs].mean(axis=0)
            centroids[cid] = c / (np.linalg.norm(c) or 1.0)
    n_chat = sum(1 for k in kinds.values() if k == "잡담")
    print(f"검증 통과 동향 클러스터 {len(centroids)}개 (잡담 성격 {n_chat}개는 구출 제외), "
          f"LLM-X {len(false_pool)}건 → 구출 풀 합류")

    # 3) 캘리브레이션 + 구출 (LLM-X 풀 + 노이즈 풀 전부) --------------
    from app.services.embedding import get_embedder

    embedder = get_embedder()
    if not embedder.ready:
        sys.exit("임베딩 백엔드가 stub — .env 의 EMBEDDING_BACKEND 확인 (구출 불가)")

    def embed_segments(idxs: list[int]) -> tuple[list[tuple[int, int, str]], np.ndarray]:
        keys, texts = [], []
        for i in idxs:
            for si, seg in enumerate(split_sentences(posts[i].get("text") or posts[i]["title"])):
                keys.append((i, si, seg))
                texts.append(seg)
        return keys, (embedder.encode(texts) if texts else np.empty((0, X.shape[1])))

    # 구출 풀은 캘리브레이션 전에 임베딩 — 뻘글 판정 글의 문장이 음성 표본이 된다
    rescue_pool = [(i, reason) for i, reason in false_pool] + [(i, "노이즈") for i in noise_idx]
    reason_by_idx = dict(rescue_pool)
    keys, vecs = embed_segments([i for i, _ in rescue_pool])

    threshold = args.sim_threshold
    diag: dict = {"manual": threshold is not None}
    if threshold is None and centroids:
        seg_vecs_by_cluster = {}
        for cid, idxs in true_idx.items():
            if cid in centroids:
                _, tv = embed_segments(idxs)
                seg_vecs_by_cluster[cid] = tv
        junk_rows = [k for k, (i, _, _) in enumerate(keys) if reason_by_idx[i] == "뻘글"]
        junk_vecs = vecs[junk_rows] if junk_rows else None
        threshold, diag = calibrate_threshold(seg_vecs_by_cluster, centroids, junk_vecs)
        if threshold is None:
            print(f"⚠️ 캘리브레이션 실패: {diag.get('error')} — 구출 단계 생략")
        else:
            print(f"문장 단위 임계값 t={threshold:.4f} (음성 p99, 뻘글 음성 {diag['n_neg_junk']}건 포함) | "
                  f"양성 중앙값 {diag['pos_median']}, 임계값 통과율 {diag['recall_at_t']:.1%}")

    rescued: list[dict] = []                             # 문장 단위 구출 레코드
    if threshold is not None and centroids:
        cids = list(centroids)
        C = np.stack([centroids[c] for c in cids])
        if len(vecs):
            sims = vecs @ C.T
            for (i, si, seg), row in zip(keys, sims):
                for k in np.flatnonzero(row >= threshold):   # 복합글: 여러 클러스터 허용
                    rescued.append({
                        "post_id": posts[i]["id"], "sentence_idx": si, "sentence": seg,
                        "cluster": int(cids[k]), "sim": round(float(row[k]), 4),
                        "source": "rescue-noise" if reason_by_idx[i] == "노이즈" else "rescue-false",
                        "x_reason": reason_by_idx[i],
                    })
        n_posts_rescued = len({r["post_id"] for r in rescued})
        n_from_noise = len({r["post_id"] for r in rescued if r["source"] == "rescue-noise"})
        print(f"구출: 문장 {len(rescued)}건 / 글 {n_posts_rescued}건 "
              f"(노이즈에서 {n_from_noise}건 = 노이즈 누수율 {n_from_noise / max(len(noise_idx), 1):.1%})")

    # 4) 멀티라벨 정답셋 산출 ----------------------------------------
    post_labels: dict[str, dict[int, str]] = defaultdict(dict)   # post_id → {cluster: source}
    for cid, idxs in true_idx.items():
        for i in idxs:
            post_labels[posts[i]["id"]][cid] = "hdbscan+llm"
    for r in rescued:
        post_labels[r["post_id"]].setdefault(r["cluster"], r["source"])

    reason_by_id = {posts[i]["id"]: reason for i, reason in false_pool}
    reason_by_id.update({posts[i]["id"]: "노이즈" for i in noise_idx})

    with (out_dir / "clusters.jsonl").open("w") as f:
        for cid in sorted(true_idx):
            f.write(json.dumps({
                "cluster": cid, "name": names.get(cid, f"cluster-{cid}"),
                "nature": kinds.get(cid, "동향"),
                "size_raw": len(clusters[cid]), "size_verified": len(true_idx[cid]),
                "size_final": sum(1 for ls in post_labels.values() if cid in ls),
            }, ensure_ascii=False) + "\n")
    with (out_dir / "labels.jsonl").open("w") as f:
        for p in posts:
            ls = post_labels.get(p["id"])
            if ls:
                f.write(json.dumps({
                    "post_id": p["id"], "title": p["title"], "created_at": p["created_at"],
                    "labels": [{"cluster": c, "name": names.get(c, ""), "source": s}
                               for c, s in sorted(ls.items())],
                }, ensure_ascii=False) + "\n")
    with (out_dir / "sentences.jsonl").open("w") as f:
        for r in rescued:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "negatives.jsonl").open("w") as f:               # 판단 모델용 부정 클래스
        for p in posts:
            if p["id"] not in post_labels:
                f.write(json.dumps({
                    "post_id": p["id"], "title": p["title"], "created_at": p["created_at"],
                    "reason": reason_by_id.get(p["id"], "노이즈"),
                }, ensure_ascii=False) + "\n")

    n_multi = sum(1 for ls in post_labels.values() if len(ls) > 1)
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "hours": args.hours, "min_cluster_size": args.min_cluster_size, "no_llm": args.no_llm,
        "funnel": {
            "posts": len(posts),
            "clustered": n_clustered, "noise": len(noise_idx),
            "llm_true": sum(len(v) for v in true_idx.values()),
            "llm_false": len(false_pool),
            "x_reasons": {r: sum(1 for _, x in false_pool if x == r) for r in REASONS},
            "rescued_posts": len({r["post_id"] for r in rescued}),
            "rescued_from_noise": len({r["post_id"] for r in rescued if r["source"] == "rescue-noise"}),
            "final_labeled": len(post_labels), "multi_label": n_multi,
            "negatives": len(posts) - len(post_labels),
        },
        "calibration": {"threshold": threshold, **diag},
        "embedder": embedder.name,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n산출 완료 → {out_dir}/ (clusters/labels/sentences/negatives.jsonl + report.json)")
    print(json.dumps(report["funnel"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
