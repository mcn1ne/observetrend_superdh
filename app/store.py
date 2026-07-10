"""최신 분석 결과 + 파이프라인 상태 인메모리 저장소 (07-pipeline-integration.md)."""
import threading
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()

_latest: dict[str, Any] = {
    "updated_at": None,
    "results": [],          # 카테고리(대분류)별 결과
    "topics": [],           # 주제(미세 이슈)별 결과 — 버스트·알림의 단위
    "stats": {},            # 실행 통계 (글 수, 소요 시간 등)
}

_status: dict[str, Any] = {
    "loop_running": False,
    "last_run_at": None,
    "last_error": None,
    "run_count": 0,
    "slots": {},            # 각 단계 백엔드/준비 상태
}


def save_latest(results: list[dict], stats: dict, topics: list[dict] | None = None) -> str:
    """최신 스냅샷을 인메모리에 게시하고 그 시각(ISO) 을 반환.

    반환값은 호출부가 같은 ts 로 DB 스냅샷(타임머신)을 적재하는 데 쓴다.
    """
    with _lock:
        _latest["updated_at"] = datetime.now(timezone.utc).isoformat()
        _latest["results"] = results
        _latest["topics"] = topics or []
        _latest["stats"] = stats
        _status["last_run_at"] = _latest["updated_at"]
        _status["last_error"] = None
        _status["run_count"] += 1
        return _latest["updated_at"]


def get_latest() -> dict:
    with _lock:
        return dict(_latest)


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
