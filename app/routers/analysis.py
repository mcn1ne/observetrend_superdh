"""API 입구 — 얇게 유지, 로직 없음 (07-pipeline-integration.md)."""
import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app import db, store
from app.config import settings
from app.services.judge import get_judge
from app.services.pipeline import cosine_cluster, recluster_check, run_pipeline, slot_status

router = APIRouter(prefix="/api", tags=["분석"])


@router.get("/status")
async def get_status():
    """파이프라인 루프 상태 + 각 단계 슬롯 준비 상태 + 다이얼 설정."""
    status = store.get_status()
    status["slots"] = slot_status()
    status["config"] = {
        "collect_interval_sec": settings.collect_interval_sec,
        "analyze_interval_sec": settings.analyze_interval_sec,
        "window_hours": settings.window_hours,
        "recent_window_min": settings.recent_window_min,
        "half_life_min": settings.half_life_min,
        "burst_ratio": settings.burst_ratio,
        "min_recent": settings.min_recent,
        "cooldown_min": settings.cooldown_min,
        "category_assign_sim": settings.category_assign_sim,
    }
    status["post_count"] = db.count_posts()
    return status


@router.get("/games")
async def get_games():
    """등록된 게임 목록 — 프런트 게임 선택기용."""
    return {"default": settings.default_game_id,
            "games": [{"id": g.id, "name": g.name} for g in settings.games]}


@router.get("/categories")
async def get_categories(game: str | None = None):
    """최신 분석 결과 (카테고리별 점수·버스트·판정) + 점수 추이 이력.

    정렬 규약: 알림된 카테고리가 상단 (관제 화면의 상단 고정은 프런트 정렬로 처리)
    """
    data = store.get_latest(game)
    history = db.load_score_history(hours=6, game_id=game)
    for r in data["results"]:
        r["score_history"] = [h["heat"] for h in history.get(r["category_id"], [])]
    return data


@router.get("/categories/{category_id}/posts")
async def get_category_posts(category_id: int, hours: int = 24, limit: int = 50,
                             game: str | None = None):
    """특정 카테고리의 최근 글 목록."""
    posts = [
        {k: v for k, v in p.items() if k != "embedding"}
        for p in db.load_recent_posts(hours, game)
        if p.get("category_id") == category_id
    ]
    return posts[-limit:][::-1]


@router.get("/topics")
async def get_topics(game: str | None = None):
    """최신 주제(미세 이슈) 목록 — 버스트·요약·알림 판단의 단위."""
    data = store.get_latest(game)
    return {"updated_at": data["updated_at"],
            "topics": data.get("topics", []),
            "stats": data.get("stats", {})}


@router.get("/topics/{topic_id}/posts")
async def get_topic_posts(topic_id: int, q: str = "", sort: str = "created_at",
                          order: str = "desc", page: int = 1, per_page: int = 50,
                          hours: float | None = None):
    """특정 주제에 묶인 글 목록 — 검색(q: 제목·본문)·정렬·페이지네이션.

    기본은 현재 분석 창(window_hours)의 현재 구성원만 — 주제 의미가 바뀌어도
    과거 글이 계속 보이던 혼재 체감 문제를 막는다. hours=0 이면 전체 이력
    (지난 알림 이력에서 들어올 때 당시 글을 보는 용도, total_all 로 건수 제공).
    """
    if hours is None:
        hours = settings.window_hours
    return db.load_topic_posts(topic_id, q=q, sort=sort, order=order,
                               page=page, per_page=per_page,
                               hours=hours if hours > 0 else None)


@router.get("/topics/{topic_id}/posts-at")
async def get_topic_posts_at(topic_id: int, at: str = Query(...),
                             game: str | None = None):
    """타임머신용: 스냅샷 시점(at 이하 최신)의 주제 멤버 글 복원.

    구 스냅샷(member_ids 저장 전)은 has_members=false + 미리보기 5건 폴백.
    """
    snap = await asyncio.to_thread(db.load_snapshot_topic_posts, at, topic_id, game)
    if snap is None:
        return {"snapshot_ts": None, "topic_id": topic_id, "name": None,
                "has_members": False, "posts": []}
    return snap


@router.get("/alerts")
async def get_alerts(game: str | None = None):
    """알림 대상으로 판정된 주제 + 발송 이력."""
    data = store.get_latest(game)
    return {
        "updated_at": data["updated_at"],
        "results": [t for t in data.get("topics", []) if t["decision"] == "O"],
        "history": db.load_alerts(limit=100, game_id=game),
    }


@router.get("/snapshots")
async def get_snapshots(from_: str | None = Query(None, alias="from"),
                        to: str | None = None, game: str | None = None):
    """타임머신 스크럽 인덱스 — 구간 내 스냅샷 ts + 카운트(payload 제외)."""
    return {"snapshots": await asyncio.to_thread(db.list_snapshots, from_, to, game)}


@router.get("/snapshot")
async def get_snapshot(at: str | None = None, game: str | None = None):
    """특정 시각의 대시보드 전체 상태 복원 (at 이하 최신, 없으면 가장 최근)."""
    snap = await asyncio.to_thread(db.load_snapshot, at, game)
    if snap is None:
        return {"snapshot_ts": None, "updated_at": None, "results": [],
                "topics": [], "stats": {}, "message": "해당 구간에 스냅샷이 없습니다."}
    return snap


@router.get("/history/range")
async def get_history_range(from_: str = Query(..., alias="from"), to: str = Query(...),
                            game: str | None = None):
    """시간대(from~to) 집계 — 알림 이벤트·버스트 상위 주제·카테고리 열기."""
    return await asyncio.to_thread(db.range_summary, from_, to, game)


class FeedbackRequest(BaseModel):
    feedback: str | None = None  # 'O'(알림 적절) / 'X'(불필요) / null(평가 취소)


@router.post("/alerts/{alert_id}/feedback")
async def set_feedback(alert_id: int, req: FeedbackRequest):
    """알림 이력에 담당자 피드백 기록 — 파인튜닝 학습 데이터로 축적."""
    if req.feedback not in ("O", "X", None):
        return {"ok": False, "error": "feedback은 'O', 'X', null 중 하나"}
    ok = db.set_alert_feedback(alert_id, req.feedback)
    return {"ok": ok}


@router.get("/alerts/export")
async def export_feedback():
    """피드백 달린 알림을 파인튜닝용 CSV로 다운로드 (GreatestStep notify_raw.csv 형식).

    엑셀에서 한글이 깨지지 않도록 UTF-8 BOM 포함.
    """
    import csv
    import io

    from fastapi.responses import Response

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["input", "output"])
    for judge_input, feedback in db.export_feedback_rows():
        writer.writerow([judge_input, feedback])
    return Response(
        content="﻿" + buf.getvalue(),   # UTF-8 BOM — 엑셀 한글 호환
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="alerts_feedback.csv"'},
    )


@router.get("/posts/recent")
async def get_recent_posts(hours: int = 1, limit: int = 50, game: str | None = None):
    """최근 수집 글 조회 (임베딩 벡터 제외)."""
    posts = db.load_recent_posts(hours, game)
    posts = posts[-limit:][::-1]
    return [
        {k: v for k, v in p.items() if k != "embedding"} | {"embedded": p.get("embedding") is not None}
        for p in posts
    ]


@router.post("/run")
async def trigger_run():
    """파이프라인 1회 수동 실행 (백그라운드 루프와 별개) — 기본 게임 대상."""
    gid = settings.default_game_id
    results, stats, topics = await asyncio.to_thread(run_pipeline)
    ts = store.save_latest(gid, results, stats, topics)
    await asyncio.to_thread(_persist_run_snapshot, results, stats, topics, ts, gid)
    return {"ok": True, "stats": stats, "n_results": len(results), "n_topics": len(topics)}


def _persist_run_snapshot(results, stats, topics, ts, game_id) -> None:
    db.save_snapshot(results, stats, topics, ts, game_id)
    db.prune_snapshots(settings.snapshot_retention_days)


@router.get("/maintenance/cosine-cluster")
async def get_cosine_cluster(threshold: float = 0.92, hours: int = 24):
    """순수 코사인 유사도 묶기 (탐색용) — 유사도 ≥ threshold 연결 요소."""
    return await asyncio.to_thread(cosine_cluster, threshold, hours)


@router.post("/maintenance/reclassify")
async def trigger_reclassify(hours: int = 72, max_sim: float | None = None,
                             game: str | None = None):
    """전체 재분류: 현재 카테고리 체계로 창 내 모든 글을 LLM 배치 재분류.

    콜드스타트 오배정 교정용. 글 수천 건 기준 수십 분 소요될 수 있음.
    max_sim 지정 시 자기 카테고리 중심과 유사도 < max_sim 인 의심 글만 교정.
    game 미지정 시 기본 게임.
    """
    from app.services.categorize import reclassify_window

    return await asyncio.to_thread(reclassify_window, hours, 10, max_sim, game)


@router.post("/maintenance/recluster-check")
async def trigger_recluster_check():
    """HDBSCAN 안전망: 재클러스터링으로 카테고리 중복/누락 점검 (하루 1회 권장)."""
    return await asyncio.to_thread(recluster_check)


@router.get("/gemma/analyses")
async def get_gemma_analyses(hours: int = 24, limit: int = 200, game: str | None = None):
    """글 단위 분석(adapters4) 결과 + 큐 상태."""
    return {
        "queue": db.analysis_queue_stats(game),
        "results": db.load_analyses(hours, limit, game),
    }


class JudgeRequest(BaseModel):
    text: str  # 형식: "주제___제목___요약" 또는 자유 텍스트


@router.post("/judge/test")
async def judge_test(req: JudgeRequest):
    """파인튜닝 4B 판단 모델 단독 테스트 (오탐/미탐 점검용)."""
    decision = await asyncio.to_thread(get_judge().judge, req.text)
    return {"input": req.text, "decision": decision,
            "meaning": "발송" if decision == "O" else "미발송"}
