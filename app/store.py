"""최신 분석 결과 + 파이프라인 상태 인메모리 저장소 (07-pipeline-integration.md).

멀티게임: 최신 스냅샷은 game_id 별로 보관한다(_latest[game_id]). 파이프라인 상태
(_status)는 프로세스 단위 공용.
"""
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_lock = threading.Lock()

# game_id -> {updated_at, results(카테고리), topics(주제), stats}
_latest: dict[str, dict[str, Any]] = {}

_status: dict[str, Any] = {
    "loop_running": False,
    "last_run_at": None,
    "last_error": None,
    "run_count": 0,
    "slots": {},            # 각 단계 백엔드/준비 상태
}


def _empty() -> dict[str, Any]:
    return {"updated_at": None, "results": [], "topics": [], "stats": {}}


def save_latest(game_id: str, results: list[dict], stats: dict,
                topics: list[dict] | None = None) -> str:
    """게임의 최신 스냅샷을 게시하고 그 시각(ISO) 을 반환.

    반환값은 호출부가 같은 ts 로 DB 스냅샷(타임머신)을 적재하는 데 쓴다.
    """
    with _lock:
        ts = datetime.now(timezone.utc).isoformat()
        _latest[game_id] = {"updated_at": ts, "results": results,
                            "topics": topics or [], "stats": stats}
        _status["last_run_at"] = ts
        _status["last_error"] = None
        _status["run_count"] += 1
        return ts


def get_latest(game_id: str | None = None) -> dict:
    game_id = game_id or settings.default_game_id
    with _lock:
        return dict(_latest.get(game_id) or _empty())


def set_loop_running(running: bool) -> None:
    with _lock:
        _status["loop_running"] = running


def set_error(msg: str) -> None:
    with _lock:
        _status["last_error"] = msg


def set_slots(slots: dict) -> None:
    with _lock:
        _status["slots"] = slots


def get_status() -> dict:
    with _lock:
        return dict(_status)
