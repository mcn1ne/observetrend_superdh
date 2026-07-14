"""07-pipeline-integration.md — 전체 연결 (영속 카테고리 기반 실시간 관제).

- collect_step(매 1분): DB1(수집원) → 전처리 → DB2 적재 → 캡셔닝(이미지)
  → 새 글만 임베딩. 카테고리는 별도 adapters4 워커의 1회 분석 결과로 배정
- analyze_step(매 1분): 카테고리별 열기 점수·버스트 판정 → 점수 스냅샷 저장
  → 급증 카테고리만 요약(05) → 4B 알림 판단(06) → 쿨다운(카테고리 id 기준)

카테고리가 영속이므로 클러스터 번호 재추적(중심벡터 비교)이 필요 없다.
HDBSCAN은 배정 누락/카테고리 중복을 점검하는 안전망으로 유지 (recluster_check).
"""
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from app import db
from app.config import settings
from app.services.caption import get_captioner
from app.services.categorize import get_categorizer
from app.services.clustering import get_clusterer, group_by_label
from app.services.collect import DcinsideCollector, get_collector
from app.services.detection import cluster_heat, is_burst, post_age_minutes
from app.services.embedding import get_embedder
from app.services.judge import build_judge_input, get_judge
from app.services.preprocess import preprocess
from app.services.summarize import get_summarizer

_collector = None
_collectors: dict = {}   # game_id -> DcinsideCollector (커서 인메모리 유지)


def slot_status() -> dict:
    """각 단계 슬롯의 백엔드/준비 상태 (상태 API·UI 표시용)."""
    embedder, clusterer = get_embedder(), get_clusterer()
    summarizer, judge = get_summarizer(), get_judge()
    captioner, categorizer = get_captioner(), get_categorizer()
    return {
        "collector": {"backend": settings.collector_backend, "ready": settings.collector_backend != "mock",
                      "name": "mock (가상 게시판 시뮬레이터)" if settings.collector_backend == "mock"
                              else f"dcinside ({settings.dcinside_db_path})"},
        "captioner": {"backend": settings.captioner_backend, "ready": captioner.ready, "name": captioner.name},
        "embedding": {"backend": settings.embedding_backend, "ready": embedder.ready, "name": embedder.name},
        "categorizer": {"backend": "adapters4", "ready": settings.gemma_analysis_enabled,
                        "name": "Gemma 4 adapters4 (major + topic_label 규칙 매핑)"},
        "clustering": {"backend": settings.clustering_backend, "ready": clusterer.ready,
                       "name": clusterer.name + " — 안전망 점검용"},
        "summarizer": {"backend": settings.summarizer_backend, "ready": summarizer.ready, "name": summarizer.name},
        "judge": {"backend": settings.judge_backend, "ready": judge.ready, "name": judge.name,
                  "fused": bool(settings.judge_fused_path)},
    }


def collect_step() -> dict:
    """수집 → 전처리 → 캡셔닝 → 새 글만 임베딩 → 카테고리 배정 (다이얼 A).

    멀티게임: dcinside 는 settings.games 를 돌며 게임별 소스/커서로 수집(글에 game_id 태깅).
    임베딩은 게임 무관이라 전 게임 배치로, 카테고리 배정은 게임 파티션이라 게임별로 돈다.
    """
    global _collector
    total_fetched = total_saved = 0

    if settings.collector_backend == "dcinside":
        game_ids = [g.id for g in settings.games]
        for g in settings.games:
            c = _collectors.get(g.id)
            if c is None:
                c = _collectors[g.id] = DcinsideCollector(
                    g.id, g.source_db_path, g.source_game, g.source_gallery,
                )
            raw = c.fetch_new_posts()
            total_saved += db.save_posts(preprocess(raw))
            db.set_game_cursor(g.id, c._last_post_no)   # 커서 영속
            total_fetched += len(raw)
    else:
        if _collector is None:
            _collector = get_collector()
        raw = _collector.fetch_new_posts()
        dg = settings.default_game_id
        total_saved = db.save_posts(preprocess([{**p, "game_id": dg} for p in raw]))
        total_fetched = len(raw)
        game_ids = [dg]

    # 새 글만 1회 임베딩 (게임 무관 배치). 캡셔닝은 여기서 하지 않는다 —
    # 동기로 하면 버스트 때 수집 루프가 밀린다. 별도 캡션 워커(caption_batch)가
    # 한가할 때 다운로드→캡션 후 그 글만 재임베딩한다 (services/caption.py).
    pending = db.load_posts_without_embedding()
    embedded = 0
    if pending:
        vectors = get_embedder().encode([p["text"] for p in pending])
        db.save_embeddings([(p["game_id"], p["id"]) for p in pending], vectors)
        embedded = len(pending)

    # 카테고리는 별도 adapters4 워커가 글을 1회 분석한 결과(major+topic_label)로
    # 배정한다. 여기서 기본형 Gemma를 추가 호출하지 않는다.
    awaiting_category = sum(len(db.load_posts_without_category(gid, limit=501)) for gid in game_ids)
    return {"fetched": total_fetched, "saved": total_saved, "embedded": embedded,
            "awaiting_category": awaiting_category}


def analyze_step(game_id: str | None = None) -> tuple[list[dict], dict, list[dict]]:
    """매분 분석 (다이얼 B·C) — game_id 게임 단위로 독립 수행.

    1) 카테고리(대분류) 점수 집계 — 화면 표시·점수 이력용
    2) ★주제(topic) 탐지 — 코사인 묶기 + 영속 추적. 버스트 → 요약 → 알림 판단은
       이 주제 단위로 돈다 ("오늘 새로 뜬 구체적 이슈"가 알림의 단위)
    """
    game_id = game_id or settings.default_game_id
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)

    all_posts = db.load_recent_posts(settings.window_hours, game_id)
    posts = [p for p in all_posts if p.get("embedding") is not None]
    categories = {c["id"]: c for c in db.load_categories(game_id)}
    if len(posts) < settings.topic_min_size:
        return [], {"total_posts_window": len(posts), "n_categories": 0, "n_topics": 0,
                    "duration_sec": 0.0, "message": "분석할 글이 아직 부족합니다"}, []

    # ── 1) 카테고리 집계 (표시용 — 판단은 주제 계층에서) ──────────
    by_cat: dict[int, list[dict]] = defaultdict(list)
    for p in posts:
        if p.get("category_id") is not None:
            by_cat[p["category_id"]].append(p)

    results, snapshots = [], []
    for cat_id, group in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        cat = categories.get(cat_id)
        if cat is None:
            continue
        created_ats = [p["created_at"] for p in group]
        heat = cluster_heat(created_ats, now)            # 08 §5 감쇠 가중합 점수
        recent_count = sum(1 for c in created_ats
                           if post_age_minutes(c, now) <= settings.recent_window_min)
        snapshots.append((cat_id, round(heat, 3), recent_count))
        results.append({
            "category_id": cat_id,
            "name": cat["name"],
            "size": len(group),
            "total_count": cat["post_count"],
            "heat": round(heat, 2),
            "recent_count": recent_count,
            "is_burst": is_burst(max(recent_count, heat), len(group)),
            "preview": [
                {"id": p["id"], "title": p["title"], "url": p.get("url"),
                 "created_at": p["created_at"]}
                for p in group[-5:][::-1]
            ],
            "latest_at": max(created_ats),
            "last_alerted_at": cat["last_alerted_at"],
            "summary": None,
            "decision": None,
            "alerted": False,
        })

    db.save_score_snapshot(snapshots, game_id)
    db.prune_score_history()

    # ── 2) 주제 탐지 → 버스트만 요약 → 파인튜닝 4B 판단 → 알림 ────
    from app.services.topics import detect_topics

    embeddings = np.stack([p["embedding"] for p in posts])
    since_iso = (now - timedelta(hours=settings.window_hours)).isoformat()
    topic_groups = detect_topics(posts, embeddings, now, since_iso, game_id)

    summarizer, judge = get_summarizer(), get_judge()
    cooldown_sec = settings.cooldown_min * 60
    cat_names = {c["id"]: c["name"] for c in db.load_categories(game_id)}

    from app.services.topics import verify_topic_members

    topics = []
    verify_budget = 1        # 평시 정화 예산: 사이클당 LLM 1배치 (버스트 검증과 별도)
    for tg in topic_groups:
        # 여러 벡터 묶음이 한 영속 주제로 병합되면 idxs 의 시간 순서가 깨지므로
        # 반드시 재정렬 — preview(최신 5건)가 이 순서에 의존한다
        group = sorted((posts[i] for i in tg["idxs"]), key=lambda p: p["created_at"])

        # 관제 화면 정화: 검증 캐시는 모든 주제에 상시 적용, 미검증분은
        # 사이클당 예산 안에서 점진 판정 (topic_groups는 큰 주제 순 → 우선순위)
        group, vstats = verify_topic_members(tg["topic_id"], tg["name"], group,
                                             batch_limit=verify_budget)
        verify_budget -= vstats["spent"]
        if len(group) < settings.topic_min_size:
            continue                     # 검증으로 실체가 사라진 주제는 표시 안 함

        created_ats = [p["created_at"] for p in group]
        heat = cluster_heat(created_ats, now)
        recent_count = sum(1 for c in created_ats
                           if post_age_minutes(c, now) <= settings.recent_window_min)
        burst = is_burst(max(recent_count, heat), len(group))

        cat_comp = Counter(cat_names.get(p.get("category_id"), "미분류") for p in group)
        item = {
            "topic_id": tg["topic_id"],
            "name": tg["name"],
            "size": len(group),
            "heat": round(heat, 2),
            "recent_count": recent_count,
            "is_burst": burst,
            "verify": vstats,
            "categories": dict(cat_comp.most_common(3)),
            "preview": [
                {"id": p["id"], "title": p["title"], "url": p.get("url"),
                 "created_at": p["created_at"]}
                for p in group[-5:][::-1]
            ],
            # 이 사이클에 화면에 보인 멤버 전체 — 타임머신 "당시 구성" 복원용
            # (스냅샷 블롭에 그대로 저장됨, 건당 ~1KB·30일 +49MB 실측)
            "member_ids": [p["id"] for p in group],
            "latest_at": max(created_ats),
            "summary": None,
            "decision": None,
            "alerted": False,
        }

        if burst:                                        # 급증 주제만 요약·판단
            # 알림 길목은 예산과 무관하게 우선 검증 — 이물질이 요약·판단을
            # 오염시키면 안 된다. 미검증 잔여분은 다음 사이클에서 마저 처리.
            group, vstats = verify_topic_members(tg["topic_id"], tg["name"], group,
                                                 batch_limit=2)
            item["verify"] = vstats
            if len(group) < settings.topic_min_size:
                item["is_burst"] = False                 # 검증 후 실체 없음 → 취소
                topics.append(item)
                continue
            created_ats = [p["created_at"] for p in group]
            heat = cluster_heat(created_ats, now)
            recent_count = sum(1 for c in created_ats
                               if post_age_minutes(c, now) <= settings.recent_window_min)
            burst = is_burst(max(recent_count, heat), len(group))
            item.update(size=len(group), heat=round(heat, 2), recent_count=recent_count,
                        is_burst=burst,
                        member_ids=[p["id"] for p in group],
                        preview=[{"id": p["id"], "title": p["title"], "url": p.get("url"),
                                  "created_at": p["created_at"]} for p in group[-5:][::-1]])
            if not burst:                                # 이물질 빼니 급증이 아님
                topics.append(item)
                continue
            idx_by_id = {posts[i]["id"]: i for i in tg["idxs"]}
            vecs = embeddings[[idx_by_id[p["id"]] for p in group]]
            s = summarizer.summarize(group, vecs)
            # 라벨 = 멤버 글들의 adapters4 주제문구 다수결 — 판단 모델(adapters3)의
            # 학습 입력 라벨과 같은 어휘라 분포가 일치한다. 분석 전이면 주제명 폴백.
            s["label"] = (db.majority_topic_label([p["id"] for p in group], game_id)
                          or tg["name"])
            item["summary"] = s
            judge_input = build_judge_input(s["label"], s["title"], s["summary"])
            decision = judge.judge(judge_input)
            item["decision"] = decision

            if decision == "O":
                last = tg.get("last_alerted_at")
                elapsed = (now - datetime.fromisoformat(last)).total_seconds() if last else None
                if elapsed is None or elapsed >= cooldown_sec:
                    item["alerted"] = True
                    db.mark_topic_alerted(tg["topic_id"])
                    # judge_input 원문을 함께 저장 — 피드백(맞음/틀림)이 달리면
                    # 그대로 파인튜닝 학습쌍(input, output)이 된다
                    db.save_alert(None, tg["name"], len(group), heat, s["summary"],
                                  decision, judge_input=judge_input,
                                  topic_id=tg["topic_id"], game_id=game_id)
                    send_alert(tg["name"], s["summary"])
            else:
                # X(미발송) 판정도 이력에 남긴다 — 피드백으로 "알림 왔어야 했다"(미탐)를
                # 교정할 수 있어야 학습 데이터의 O/X 균형이 잡힌다.
                # 버스트 지속 중 매분 재판정되므로 쿨다운 간격으로만 기록.
                if not db.has_recent_judgment(tg["name"], "X", settings.cooldown_min, game_id):
                    db.save_alert(None, tg["name"], len(group), heat, s["summary"],
                                  decision, judge_input=judge_input,
                                  topic_id=tg["topic_id"], game_id=game_id)

        topics.append(item)

    # ── 소강 감시: 알림된 주제가 가라앉으면 종료 알림 (기계적 — LLM 불필요) ──
    # 기준: heat ≤ 정점×calm_heat_ratio (상대) AND 최근 창 글 수 < min_recent
    # (절대) 가 calm_streak_min 사이클 연속. 창에서 사라진 주제는 heat=0 취급.
    seen_by_id = {t["topic_id"]: t for t in topics}
    for t in db.load_topics(game_id):
        last = t.get("last_alerted_at")
        if not last:
            continue                                 # 알림 이력 없음 — 감시 대상 아님
        if t.get("calmed_at") and t["calmed_at"] >= last:
            if t["id"] in seen_by_id:
                seen_by_id[t["id"]]["calmed"] = True  # 이번 사이클은 이미 종료 상태
            continue
        item = seen_by_id.get(t["id"])
        heat = item["heat"] if item else 0.0          # 창에서 사라짐 = 완전 소강
        recent = item["recent_count"] if item else 0
        peak = max(t.get("peak_heat") or 0.0, heat)
        calm = heat <= settings.calm_heat_ratio * peak and recent < settings.min_recent
        streak = (t.get("calm_streak") or 0) + 1 if calm else 0
        if streak >= settings.calm_streak_min:
            ratio = heat / peak if peak > 0 else 0.0
            summary = (f"정점 heat {peak:.1f} → 현재 {heat:.1f} ({ratio:.0%}), "
                       f"최근 1시간 {recent}건 — 이슈 소강")
            db.save_alert(None, t["name"], item["size"] if item else 0, heat, summary,
                          "소강", topic_id=t["id"], game_id=game_id)
            db.mark_topic_calmed(t["id"])
            db.update_topic_watch(t["id"], peak, 0)
            send_alert(f"{t['name']} (소강)", summary)
            if item:
                item["calmed"] = True
        else:
            db.update_topic_watch(t["id"], peak, streak)
            if item:
                item["calmed"] = False

    topics.sort(key=lambda t: (-(t["decision"] == "O"), -t["is_burst"], -t["heat"]))

    # 주제 시계열 적재 (category_scores와 대칭 — 타임머신 범위 집계용 파생 인덱스)
    db.save_topic_scores([
        {"topic_id": t["topic_id"], "heat": t["heat"], "recent_count": t["recent_count"],
         "size": t["size"], "is_burst": t["is_burst"], "decision": t["decision"]}
        for t in topics
    ], game_id)
    db.prune_topic_scores(settings.snapshot_retention_days)

    stats = {
        "total_posts_window": len(posts),
        "n_categories": len(by_cat),
        "n_topics": len(topics),
        "duration_sec": round(time.monotonic() - t0, 2),
    }
    return results, stats, topics


def recluster_check() -> dict:
    """HDBSCAN 안전망: 최근 창을 재클러스터링해 카테고리 배정과 비교.

    - 한 클러스터가 여러 카테고리에 걸쳐 있으면 → 해당 카테고리들이 사실상
      같은 주제일 수 있음 (병합 검토)
    - 실행 주기: 필요할 때 수동 (API/UI 버튼), 또는 하루 1회 권장
    """
    posts = [p for p in db.load_recent_posts(settings.window_hours)
             if p.get("embedding") is not None and p.get("category_id") is not None]
    if len(posts) < settings.min_cluster_size:
        return {"checked": len(posts), "suggestions": []}

    embeddings = np.stack([p["embedding"] for p in posts])
    labels = get_clusterer().fit_predict(embeddings)
    clusters = group_by_label(labels)
    cat_names = {c["id"]: c["name"] for c in db.load_categories()}

    suggestions, cluster_details = [], []
    for label_id, idxs in sorted(clusters.items(), key=lambda x: -len(x[1])):
        comp = Counter(cat_names.get(posts[i]["category_id"], "?") for i in idxs)
        top = comp.most_common()
        cluster_details.append({
            "cluster": int(label_id),
            "size": len(idxs),
            "categories": dict(top),                      # {카테고리명: 글 수}
            "samples": [
                {"title": posts[i]["title"], "url": posts[i].get("url")}
                for i in idxs[:8]
            ],
        })
        if len(comp) >= 2:                                # 여러 카테고리에 걸친 클러스터
            if top[1][1] >= max(3, len(idxs) * 0.2):      # 2위도 유의미한 비중일 때만
                suggestions.append({
                    "cluster_size": len(idxs),
                    "categories": dict(top),
                    "hint": " / ".join(n for n, _ in top[:3]) + " 병합 검토",
                })
    return {"checked": len(posts),
            "n_clusters": len(clusters),
            "noise_count": int((labels == -1).sum()),
            "noise_ratio": round(float((labels == -1).mean()), 3),
            "min_cluster_size": settings.min_cluster_size,
            "suggestions": suggestions,
            "clusters": cluster_details}


def cosine_cluster(threshold: float, hours: int | None = None) -> dict:
    """순수 코사인 유사도 묶기 (실험/탐색용 페이지).

    "두 글의 유사도 ≥ threshold 면 같은 묶음" 규칙의 연결 요소(union-find).
    카테고리·HDBSCAN과 무관하게 임베딩 공간을 그대로 보여준다.
    """
    posts = [p for p in db.load_recent_posts(hours or settings.window_hours)
             if p.get("embedding") is not None]
    n = len(posts)
    if n < 2:
        return {"checked": n, "threshold": threshold, "groups": [], "singles": n}

    E = np.stack([p["embedding"] for p in posts]).astype(np.float32)
    sims = E @ E.T                                   # 정규화 벡터 → 코사인 유사도

    pair_idx = np.argwhere(np.triu(sims >= threshold, k=1))
    if len(pair_idx) > 2_000_000:
        return {"error": f"임계값 {threshold}에서는 연결 쌍이 {len(pair_idx):,}개로 너무 많습니다. "
                         "임계값을 높여주세요.", "threshold": threshold, "checked": n}

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pair_idx:
        ri, rj = find(int(i)), find(int(j))
        if ri != rj:
            parent[ri] = rj

    members: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        members[find(i)].append(i)

    cat_names = {c["id"]: c["name"] for c in db.load_categories()}
    groups, singles = [], 0
    for idxs in members.values():
        if len(idxs) < 2:
            singles += 1
            continue
        comp = Counter(cat_names.get(posts[i]["category_id"], "미배정") for i in idxs)
        groups.append({
            "size": len(idxs),
            "categories": dict(comp.most_common()),
            "samples": [
                {"title": posts[i]["title"], "url": posts[i].get("url")}
                for i in idxs[:8]
            ],
        })
    groups.sort(key=lambda g: -g["size"])
    return {"checked": n, "threshold": threshold, "n_groups": len(groups),
            "singles": singles, "groups": groups[:60]}   # 상위 60개만 (표시용)


def send_alert(label: str, summary: str) -> None:
    """담당자 알림 발송 — 현재는 로그 출력. 실제 채널(슬랙/메일 등) 연동 지점."""
    print(f"[알림 발송] {label}: {summary[:100]}")


def run_pipeline() -> tuple[list[dict], dict, list[dict]]:
    """배치 1회 실행 (수집 + 분석). 수동 트리거 API에서 사용."""
    collect_stats = collect_step()
    results, stats, topics = analyze_step()
    stats.update({f"collect_{k}": v for k, v in collect_stats.items()})
    return results, stats, topics
