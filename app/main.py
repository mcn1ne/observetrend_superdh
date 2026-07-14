"""FastAPI 앱 + 백그라운드 분석 루프 (07-pipeline-integration.md).

서버가 켜져 있는 동안:
- 매 collect_interval_sec(다이얼 A): 수집 → 새 글만 임베딩·저장
- 매 analyze_interval_sec(다이얼 B): 최근 window_hours(다이얼 C) 창 분석

실행:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8007
프론트엔드 빌드(frontend/dist)가 있으면 / 에서 함께 서빙한다.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db, store
from app.config import BASE_DIR, settings
from app.routers import analysis
from app.services.pipeline import analyze_step, collect_step, slot_status


def _analyze_and_publish(game_id: str) -> None:
    """게임 1개를 분석하고 store 게시 + 타임머신 스냅샷 적재 (전용 스레드에서)."""
    results, stats, topics = analyze_step(game_id)
    ts = store.save_latest(game_id, results, stats, topics)
    db.save_snapshot(results, stats, topics, ts, game_id)
    db.prune_snapshots(settings.snapshot_retention_days)


async def pipeline_loop():
    """수집·분석을 주기적으로 반복하는 백그라운드 루프.

    CPU를 오래 쓰는 작업은 asyncio.to_thread 로 감싸 서버 응답을 막지 않는다.
    한 번 실패해도 루프는 멈추지 않는다.
    """
    store.set_loop_running(True)
    last_analyze = 0.0
    loop = asyncio.get_event_loop()
    while True:
        try:
            # ① 수집 (다이얼 A): 새 글만 가져와 임베딩·누적 저장
            await asyncio.to_thread(collect_step)

            # ② 분석 (다이얼 B): 등록된 게임마다 독립 수행 (최근 창 전체, 다이얼 C)
            now = loop.time()
            if now - last_analyze >= settings.analyze_interval_sec - 1:
                for g in settings.games:
                    await asyncio.to_thread(_analyze_and_publish, g.id)
                last_analyze = now
        except Exception as e:
            print(f"[분석 루프 오류] {e}")
            store.set_error(str(e))
        await asyncio.sleep(settings.collect_interval_sec)


async def gemma_worker_loop():
    """Gemma 4 글 분석 큐 워커 — 수집/분석 루프와 독립적으로 큐를 소화한다.

    글이 폭증하면 큐가 쌓였다가 한가할 때 자동으로 따라잡는다 (유실 없음).
    큐가 남아 있으면 쉬지 않고 연속으로 배치를 돌린다.
    """
    from app.services.gemma_analyze import analyze_batch

    while True:
        try:
            # 게임별로 한 배치씩 공정하게 처리한다. 한 게임의 대량 백로그가
            # 다른 게임의 최신 글 분석을 막지 않도록 라운드로빈 형태로 순회한다.
            queue_busy = False
            for g in settings.games:
                stats = await asyncio.to_thread(analyze_batch, g.id)
                if stats["taken"] >= settings.gemma_batch_size:
                    queue_busy = True
            if queue_busy:
                continue                     # 큐가 밀려 있음 → 곧바로 다음 배치
        except Exception as e:
            print(f"[Gemma 분석 워커 오류] {e}")
        await asyncio.sleep(settings.gemma_worker_interval_sec)


async def caption_worker_loop():
    """이미지 캡션 큐 워커 — 다운로드→캡션→재임베딩 (수집·분석 루프와 독립).

    글은 이미 텍스트만으로 임베딩·분석돼 있으므로 캡션이 밀려도 관제는
    실시간으로 돈다. 캡션이 완료된 글은 다음 분석 사이클부터 이미지 내용이
    주제 묶기에 반영된다. 큐가 남아 있으면 쉬지 않고 연속으로 소화한다.
    """
    from app.services.caption import caption_batch

    while True:
        try:
            queue_busy = False
            for g in settings.games:
                stats = await asyncio.to_thread(caption_batch, g.id)
                if stats["taken"] >= settings.caption_batch_size:
                    queue_busy = True
            if queue_busy:
                continue                     # 큐가 밀려 있음 → 곧바로 다음 배치
        except Exception as e:
            print(f"[캡션 워커 오류] {e}")
        await asyncio.sleep(settings.caption_worker_interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    store.set_slots(slot_status())
    task = asyncio.create_task(pipeline_loop())   # 서버 시작 시 루프 가동
    gemma_task = None
    if settings.gemma_analysis_enabled:
        gemma_task = asyncio.create_task(gemma_worker_loop())
    caption_task = None
    if settings.captioner_backend == "mlx-vlm":
        caption_task = asyncio.create_task(caption_worker_loop())
    yield                                          # ← 이 동안 서버가 요청을 받음
    task.cancel()                                  # 서버 종료 시 루프 정리
    if gemma_task:
        gemma_task.cancel()
    if caption_task:
        caption_task.cancel()
    store.set_loop_running(False)


app = FastAPI(title="게임 게시판 주제 분석", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,   # Vite 개발 서버(5173)에서 API 호출 허용
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis.router)

# 프론트엔드 빌드 결과물 서빙 (frontend/ 에서 npm run build 후 생성됨)
# SPA 폴백: /categories, /alerts 같은 라우터 경로로 직접 접속·새로고침해도
# index.html 을 내려줘야 Vue Router가 화면을 그린다 (없으면 404).
_dist = Path(BASE_DIR) / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        file = (_dist / path).resolve()
        # dist 안의 실제 파일(favicon 등)은 그대로, 나머지는 index.html
        if path and file.is_file() and file.is_relative_to(_dist):
            return FileResponse(file)
        # index.html 은 캐시 금지 — 새 빌드 배포 시 브라우저가 옛 JS 해시를
        # 계속 물고 있는 문제 방지 (assets/*는 해시 파일명이라 캐시돼도 안전)
        return FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})
