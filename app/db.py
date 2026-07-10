"""SQLite 저장소 — 게시글 + 임베딩 + 영속 카테고리 + 점수 이력.

08-realtime-detection.md §6 증분 처리 3원칙:
1. 임베딩은 새 글만 1회 계산 후 저장 (재임베딩 금지)
2. created_at 인덱스로 시간 창만 조회 (전체 스캔 금지)
3. 최근 창만 메모리에서 분석

카테고리는 클러스터 번호와 달리 **영속 자산**이다: 한 번 만들어지면 id·이름·
중심벡터가 유지되고, 새 글은 중심벡터 유사도(+LLM 보조)로 배정된다.
쿨다운도 카테고리 id 기준으로 기록한다 (중심벡터 재추적 불필요).
"""
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,          -- ISO8601 (UTC)
    text        TEXT,                    -- 전처리된 텍스트 (02)
    image_path  TEXT,                    -- 첨부 이미지 경로/URL (없으면 NULL)
    caption     TEXT,                    -- 이미지 캡션 (캡셔닝 슬롯 결과)
    embedding   BLOB,                    -- float32 벡터 (03), NULL = 미계산
    embedded_at TEXT,
    category_id INTEGER                  -- 배정된 카테고리 (NULL = 미배정)
);
CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at);

CREATE TABLE IF NOT EXISTS categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    centroid        BLOB NOT NULL,       -- 정규화된 중심벡터 (float32)
    post_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_alerted_at TEXT                 -- 쿨다운 기준 (NULL = 알림 이력 없음)
);

CREATE TABLE IF NOT EXISTS category_scores (
    category_id  INTEGER NOT NULL,
    ts           TEXT NOT NULL,          -- 스냅샷 시각
    heat         REAL NOT NULL,          -- 감쇠 가중합 점수
    recent_count INTEGER NOT NULL,       -- 최근 창 글 수
    PRIMARY KEY (category_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_scores_ts ON category_scores(ts);

-- 관제 타임머신: 매 분석 사이클의 대시보드 전체 상태를 gzip JSON 블롭으로 보관.
-- 재생(임의 시각 복원)의 원천. preview는 post_id 참조로 축소해 저장한다.
CREATE TABLE IF NOT EXISTS snapshots (
    ts        TEXT PRIMARY KEY,      -- ISO8601 UTC (= store updated_at)
    payload   BLOB NOT NULL,         -- gzip(json(압축 스냅샷))
    n_topics  INTEGER NOT NULL,
    n_alerts  INTEGER NOT NULL,      -- 이 사이클 decision='O' 주제 수
    n_bursts  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

-- 주제 시계열 (category_scores의 주제 계층 판) — 빠른 범위 집계용 파생 인덱스.
CREATE TABLE IF NOT EXISTS topic_scores (
    topic_id     INTEGER NOT NULL,
    ts           TEXT NOT NULL,
    heat         REAL NOT NULL,
    recent_count INTEGER NOT NULL,
    size         INTEGER NOT NULL,
    is_burst     INTEGER NOT NULL,
    decision     TEXT,                -- NULL / 'O' / 'X'
    PRIMARY KEY (topic_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_topic_scores_ts ON topic_scores(ts);

CREATE TABLE IF NOT EXISTS topics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    centroid        BLOB NOT NULL,       -- 정규화된 중심벡터 (영속 추적용)
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,       -- 마지막으로 창에서 관측된 시각
    last_alerted_at TEXT                 -- 주제 단위 쿨다운
);

CREATE TABLE IF NOT EXISTS post_analysis (
    post_id     TEXT PRIMARY KEY,        -- posts.id
    sentiment   TEXT NOT NULL,           -- adapters4 감성 7종 (구버전 행: 긍정/중립/부정)
    major       TEXT,                    -- 대분류: 일반/콘텐츠/운영/밸런스/과금/버그
    topic_label TEXT,                    -- '[대분류] 주제문구' — judge 학습 라벨과 같은 어휘
    urgency     INTEGER,                 -- 구버전 (2026-07-09 긴급도 폐기 — 신규 미기록)
    gist        TEXT,                    -- 구버전 (topic_label 로 대체)
    analyzed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    category_id INTEGER,
    label       TEXT NOT NULL,
    size        INTEGER NOT NULL,
    heat        REAL NOT NULL,
    summary     TEXT NOT NULL,
    decision    TEXT NOT NULL            -- 'O' / 'X'
);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
"""

# 구버전 DB에 추가된 컬럼 (없으면 ALTER)
_MIGRATIONS = [
    ("posts", "image_path", "ALTER TABLE posts ADD COLUMN image_path TEXT"),
    ("posts", "caption", "ALTER TABLE posts ADD COLUMN caption TEXT"),
    ("posts", "category_id", "ALTER TABLE posts ADD COLUMN category_id INTEGER"),
    ("posts", "url", "ALTER TABLE posts ADD COLUMN url TEXT"),
    ("posts", "analysis_attempts", "ALTER TABLE posts ADD COLUMN analysis_attempts INTEGER NOT NULL DEFAULT 0"),
    ("posts", "topic_id", "ALTER TABLE posts ADD COLUMN topic_id INTEGER"),
    ("alerts", "category_id", "ALTER TABLE alerts ADD COLUMN category_id INTEGER"),
    # 알림 → 주제 상세 페이지 연결용 (구버전 행은 load_alerts 가 이름 매칭으로 보정)
    ("alerts", "topic_id", "ALTER TABLE alerts ADD COLUMN topic_id INTEGER"),
    # 피드백 학습 데이터: judge_input = 판단 모델에 들어간 원문 그대로,
    # feedback = 담당자 평가 ('O' 알림 적절 / 'X' 불필요 / NULL 미평가)
    ("alerts", "judge_input", "ALTER TABLE alerts ADD COLUMN judge_input TEXT"),
    ("alerts", "feedback", "ALTER TABLE alerts ADD COLUMN feedback TEXT"),
    # 소강 감시 상태: 알림 이후 정점 heat, 소강 조건 연속 충족 수, 소강 발송 시각
    ("topics", "peak_heat", "ALTER TABLE topics ADD COLUMN peak_heat REAL"),
    ("topics", "calm_streak", "ALTER TABLE topics ADD COLUMN calm_streak INTEGER NOT NULL DEFAULT 0"),
    ("topics", "calmed_at", "ALTER TABLE topics ADD COLUMN calmed_at TEXT"),
    # 이름을 지었던 시점의 중심벡터 — 드리프트 재명명 판단 기준점
    ("topics", "named_centroid", "ALTER TABLE topics ADD COLUMN named_centroid BLOB"),
]


def _connect() -> sqlite3.Connection:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # post_analysis 개편 (2026-07-09, adapters4 전환): major/topic_label 추가,
        # urgency/gist 는 NOT NULL 해제가 필요해 컬럼 ALTER 대신 테이블 재구축
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(post_analysis)")}
        if "major" not in cols:
            conn.executescript("""
                ALTER TABLE post_analysis RENAME TO post_analysis_old;
                CREATE TABLE post_analysis (
                    post_id     TEXT PRIMARY KEY,
                    sentiment   TEXT NOT NULL,
                    major       TEXT,
                    topic_label TEXT,
                    urgency     INTEGER,
                    gist        TEXT,
                    analyzed_at TEXT NOT NULL
                );
                INSERT INTO post_analysis (post_id, sentiment, urgency, gist, analyzed_at)
                    SELECT post_id, sentiment, urgency, gist, analyzed_at
                    FROM post_analysis_old;
                DROP TABLE post_analysis_old;
            """)
        for table, column, ddl in _MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(ddl)
        # 마이그레이션으로 추가되는 컬럼의 인덱스는 ALTER 이후에 생성
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_category ON posts(category_id)")


# ── 게시글 ──────────────────────────────────────────────────────────

def save_posts(posts: list[dict]) -> int:
    """새 글만 저장 (id 중복은 무시). posts: {id,title,body,created_at,text[,image_path]}"""
    if not posts:
        return 0
    with _connect() as conn:
        cur = conn.executemany(
            "INSERT OR IGNORE INTO posts (id, title, body, created_at, text, image_path, url) "
            "VALUES (:id, :title, :body, :created_at, :text, :image_path, :url)",
            [{"image_path": None, "url": None, **p} for p in posts],
        )
        return cur.rowcount


def load_posts_without_embedding(limit: int = 500) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, body, created_at, text, image_path FROM posts "
            "WHERE embedding IS NULL AND text IS NOT NULL ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_embeddings(ids: list[str], vectors: np.ndarray, captions: list[str | None] | None = None) -> None:
    now = _now()
    captions = captions or [None] * len(ids)
    with _connect() as conn:
        conn.executemany(
            "UPDATE posts SET embedding = ?, embedded_at = ?, caption = ? WHERE id = ?",
            [
                (np.asarray(v, dtype=np.float32).tobytes(), now, cap, pid)
                for pid, v, cap in zip(ids, vectors, captions)
            ],
        )


def load_posts_without_category(limit: int = 500) -> list[dict]:
    """임베딩은 있지만 카테고리 미배정인 글 (배정 대기열)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, text, created_at, embedding FROM posts "
            "WHERE embedding IS NOT NULL AND category_id IS NULL "
            "ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [_hydrate(r) for r in rows]


def assign_category(post_id: str, category_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE posts SET category_id = ? WHERE id = ?", (category_id, post_id))


def load_recent_posts(hours: int) -> list[dict]:
    """최근 N시간 창 조회 (created_at 인덱스 사용). embedding은 ndarray로 복원."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, body, created_at, text, embedding, category_id, topic_id, url FROM posts "
            "WHERE created_at > ? ORDER BY created_at",
            (since,),
        ).fetchall()
    return [_hydrate(r) for r in rows]


def _hydrate(row: sqlite3.Row) -> dict:
    p = dict(row)
    if p.get("embedding") is not None:
        p["embedding"] = np.frombuffer(p["embedding"], dtype=np.float32)
    return p


def count_posts() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]


# ── 카테고리 ────────────────────────────────────────────────────────

def load_categories() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    out = []
    for r in rows:
        c = dict(r)
        c["centroid"] = np.frombuffer(c["centroid"], dtype=np.float32)
        out.append(c)
    return out


def create_category(name: str, centroid: np.ndarray) -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO categories (name, centroid, post_count, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (name, np.asarray(centroid, dtype=np.float32).tobytes(), now, now),
        )
        return cur.lastrowid


def find_category_by_name(name: str) -> dict | None:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()
    if r is None:
        return None
    c = dict(r)
    c["centroid"] = np.frombuffer(c["centroid"], dtype=np.float32)
    return c


def update_category_centroid(category_id: int, centroid: np.ndarray, post_count: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE categories SET centroid = ?, post_count = ?, updated_at = ? WHERE id = ?",
            (np.asarray(centroid, dtype=np.float32).tobytes(), post_count, _now(), category_id),
        )


def mark_category_alerted(category_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE categories SET last_alerted_at = ? WHERE id = ?", (_now(), category_id)
        )


# ── 점수 이력 (시간별 점수 시스템) ───────────────────────────────────

def save_score_snapshot(rows: list[tuple[int, float, int]]) -> None:
    """rows: [(category_id, heat, recent_count)] — 현재 시각으로 스냅샷 저장."""
    if not rows:
        return
    ts = _now()
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO category_scores (category_id, ts, heat, recent_count) "
            "VALUES (?, ?, ?, ?)",
            [(cid, ts, heat, rc) for cid, heat, rc in rows],
        )


def load_score_history(hours: int = 6) -> dict[int, list[dict]]:
    """카테고리별 점수 추이 (스파크라인용). {category_id: [{ts, heat}...]}"""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT category_id, ts, heat FROM category_scores WHERE ts > ? ORDER BY ts",
            (since,),
        ).fetchall()
    history: dict[int, list[dict]] = {}
    for r in rows:
        history.setdefault(r["category_id"], []).append({"ts": r["ts"], "heat": r["heat"]})
    return history


def prune_score_history(days: int = 7) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM category_scores WHERE ts < ?", (cutoff,))


# ── 관제 타임머신: 전체 스냅샷 블롭 + 주제 시계열 ────────────────────────

def _compact_board_item(item: dict) -> dict:
    """대시보드 item(results/topics)을 스냅샷 저장용으로 축소.

    스칼라·요약·판정 등 '그 시각에만 존재하는' 필드는 인라인 유지하고,
    preview(글 5건 객체)는 preview_ids(post_id 리스트)로 축소한다. 내부 계산용
    verify 는 버린다. 복원 시 load_snapshot 이 preview_ids → posts 로 되살린다.
    """
    keep = {k: v for k, v in item.items() if k not in ("preview", "verify")}
    keep["preview_ids"] = [p["id"] for p in item.get("preview", []) if p.get("id")]
    return keep


def save_snapshot(results: list[dict], stats: dict, topics: list[dict], ts: str) -> None:
    """대시보드 전체 상태를 gzip JSON 블롭으로 1행 저장 (재생 원천)."""
    payload = {
        "updated_at": ts,
        "stats": stats,
        "results": [_compact_board_item(r) for r in results],
        "topics": [_compact_board_item(t) for t in topics],
    }
    blob = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    n_alerts = sum(1 for t in topics if t.get("decision") == "O")
    n_bursts = sum(1 for t in topics if t.get("is_burst"))
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO snapshots (ts, payload, n_topics, n_alerts, n_bursts) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, blob, len(topics), n_alerts, n_bursts),
        )


def prune_snapshots(days: int) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))


def list_snapshots(frm: str | None = None, to: str | None = None,
                   limit: int = 5000) -> list[dict]:
    """스크럽 타임라인 인덱스 — payload 제외, 가벼운 메타만."""
    where, params = [], []
    if frm:
        where.append("ts >= ?"); params.append(frm)
    if to:
        where.append("ts <= ?"); params.append(to)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT ts, n_topics, n_alerts, n_bursts FROM snapshots {clause} "
            f"ORDER BY ts LIMIT ?", params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


def load_snapshot(at: str | None = None) -> dict | None:
    """at(ISO) 이하 최신 스냅샷 1건 복원. at 없으면 가장 최근.

    preview_ids 를 posts 에서 단일 조회로 되살려 preview(title/url/created_at)를 재구성.
    반환 shape 는 라이브 대시보드(results/topics)와 동일 — 프런트 렌더 재사용.
    """
    with _connect() as conn:
        if at:
            row = conn.execute(
                "SELECT ts, payload FROM snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                (at,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT ts, payload FROM snapshots ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(gzip.decompress(row["payload"]).decode("utf-8"))

        # preview_ids 전부 모아 한 번에 조회
        all_ids = {pid for item in (payload["results"] + payload["topics"])
                   for pid in item.get("preview_ids", [])}
        posts_by_id: dict = {}
        if all_ids:
            ph = ",".join("?" * len(all_ids))
            for p in conn.execute(
                f"SELECT id, title, url, created_at FROM posts WHERE id IN ({ph})",
                list(all_ids),
            ).fetchall():
                posts_by_id[p["id"]] = dict(p)

    def _restore(item: dict) -> dict:
        ids = item.pop("preview_ids", [])
        item["preview"] = [
            posts_by_id.get(pid, {"id": pid, "title": "(삭제된 글)", "url": None,
                                  "created_at": None})
            for pid in ids
        ]
        return item

    payload["results"] = [_restore(r) for r in payload["results"]]
    payload["topics"] = [_restore(t) for t in payload["topics"]]
    payload["snapshot_ts"] = row["ts"]      # 실제로 복원된 스냅샷 시각
    return payload


def save_topic_scores(rows: list[dict]) -> None:
    """rows: [{topic_id, heat, recent_count, size, is_burst, decision}] — 현재 시각 적재."""
    if not rows:
        return
    ts = _now()
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO topic_scores "
            "(topic_id, ts, heat, recent_count, size, is_burst, decision) "
            "VALUES (:topic_id, :ts, :heat, :recent_count, :size, :is_burst, :decision)",
            [{**r, "ts": ts, "is_burst": int(bool(r.get("is_burst")))} for r in rows],
        )


def prune_topic_scores(days: int) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM topic_scores WHERE ts < ?", (cutoff,))


def range_summary(frm: str, to: str) -> dict:
    """시간대(frm~to) 집계 — 블롭 해제 없이 숫자 테이블·alerts 로그로 빠르게.

    반환: 커버리지(스냅샷 수·범위), 알림 이벤트(발송·소강), 버스트 상위 주제,
    카테고리 평균/최고 heat.
    """
    with _connect() as conn:
        cov = conn.execute(
            "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
            "       COALESCE(SUM(n_alerts),0) AS alerts, COALESCE(SUM(n_bursts),0) AS bursts "
            "FROM snapshots WHERE ts BETWEEN ? AND ?",
            (frm, to),
        ).fetchone()

        alert_events = [dict(r) for r in conn.execute(
            "SELECT id, created_at, topic_id, label, size, heat, summary, decision, feedback "
            "FROM alerts WHERE created_at BETWEEN ? AND ? ORDER BY created_at DESC",
            (frm, to),
        ).fetchall()]

        # 구간 내 주제별 최고 heat·버스트 여부 (topic 이름은 topics 테이블에서 조인)
        top_topics = [dict(r) for r in conn.execute(
            "SELECT ts.topic_id, t.name, MAX(ts.heat) AS peak_heat, "
            "       MAX(ts.is_burst) AS ever_burst, MAX(ts.size) AS peak_size "
            "FROM topic_scores ts LEFT JOIN topics t ON t.id = ts.topic_id "
            "WHERE ts.ts BETWEEN ? AND ? "
            "GROUP BY ts.topic_id ORDER BY peak_heat DESC LIMIT 20",
            (frm, to),
        ).fetchall()]

        top_cats = [dict(r) for r in conn.execute(
            "SELECT cs.category_id, c.name, AVG(cs.heat) AS avg_heat, MAX(cs.heat) AS peak_heat "
            "FROM category_scores cs LEFT JOIN categories c ON c.id = cs.category_id "
            "WHERE cs.ts BETWEEN ? AND ? "
            "GROUP BY cs.category_id ORDER BY peak_heat DESC LIMIT 20",
            (frm, to),
        ).fetchall()]

    return {
        "from": frm, "to": to,
        "coverage": {"snapshots": cov["n"], "first_ts": cov["first_ts"],
                     "last_ts": cov["last_ts"], "alert_cycles": cov["alerts"],
                     "burst_cycles": cov["bursts"]},
        "alerts": alert_events,
        "top_topics": top_topics,
        "top_categories": top_cats,
    }


# ── 주제 (topics) ───────────────────────────────────────────────────

def load_topics() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM topics ORDER BY id").fetchall()
    out = []
    for r in rows:
        t = dict(r)
        t["centroid"] = np.frombuffer(t["centroid"], dtype=np.float32)
        if t.get("named_centroid") is not None:
            t["named_centroid"] = np.frombuffer(t["named_centroid"], dtype=np.float32)
        out.append(t)
    return out


def create_topic(name: str, centroid: np.ndarray) -> int:
    now = _now()
    blob = np.asarray(centroid, dtype=np.float32).tobytes()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO topics (name, centroid, named_centroid, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, blob, blob, now, now),
        )
        return cur.lastrowid


def rename_topic(topic_id: int, name: str, named_centroid: np.ndarray) -> None:
    """이름 갱신 + 명명 기준점 재설정 (이름을 유지하며 기준점만 갱신할 때도 사용)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE topics SET name = ?, named_centroid = ? WHERE id = ?",
            (name, np.asarray(named_centroid, dtype=np.float32).tobytes(), topic_id),
        )


def merge_topics(keep_id: int, drop_id: int, centroid: np.ndarray) -> None:
    """같은 사건으로 판정된 두 주제 병합 — keep 쪽으로 글·이력 통합 후 drop 삭제.

    쿨다운(last_alerted_at)·소강 시각은 둘 중 최신을 취해 병합 직후
    중복 알림이 나가지 않게 한다. peak_heat 는 큰 쪽 유지.
    """
    with _connect() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM topics WHERE id IN (?, ?)", (keep_id, drop_id))}
        if len(rows) < 2:
            return
        keep, drop = rows[keep_id], rows[drop_id]
        conn.execute("UPDATE posts SET topic_id = ? WHERE topic_id = ?", (keep_id, drop_id))
        conn.execute(
            "UPDATE topics SET centroid = ?, last_seen_at = ?, last_alerted_at = ?, "
            "calmed_at = ?, peak_heat = ? WHERE id = ?",
            (np.asarray(centroid, dtype=np.float32).tobytes(),
             max(keep["last_seen_at"], drop["last_seen_at"]),
             max(filter(None, [keep["last_alerted_at"], drop["last_alerted_at"]]), default=None),
             max(filter(None, [keep["calmed_at"], drop["calmed_at"]]), default=None),
             max(keep["peak_heat"] or 0.0, drop["peak_heat"] or 0.0) or None,
             keep_id),
        )
        conn.execute("DELETE FROM topics WHERE id = ?", (drop_id,))


def load_topic_titles(topic_id: int, limit: int = 6) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT title FROM posts WHERE topic_id = ? ORDER BY created_at DESC LIMIT ?",
            (topic_id, limit),
        ).fetchall()
    return [r["title"] for r in rows]


def touch_topic(topic_id: int, centroid: np.ndarray) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE topics SET centroid = ?, last_seen_at = ? WHERE id = ?",
            (np.asarray(centroid, dtype=np.float32).tobytes(), _now(), topic_id),
        )


def mark_topic_alerted(topic_id: int) -> None:
    """알림 발송 기록 + 소강 감시 사이클 리셋 (정점·연속 카운터·소강 시각 초기화)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE topics SET last_alerted_at = ?, peak_heat = NULL, "
            "calm_streak = 0, calmed_at = NULL WHERE id = ?",
            (_now(), topic_id),
        )


def update_topic_watch(topic_id: int, peak_heat: float, calm_streak: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE topics SET peak_heat = ?, calm_streak = ? WHERE id = ?",
            (peak_heat, calm_streak, topic_id),
        )


def mark_topic_calmed(topic_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE topics SET calmed_at = ? WHERE id = ?", (_now(), topic_id))


def set_topic_assignments(assignments: dict[str, int | None], since_iso: str) -> None:
    """창 내 글의 topic_id 를 일괄 갱신 (클러스터링은 매 실행 새로 계산되므로)."""
    with _connect() as conn:
        conn.execute("UPDATE posts SET topic_id = NULL WHERE created_at > ?", (since_iso,))
        conn.executemany(
            "UPDATE posts SET topic_id = ? WHERE id = ?",
            [(tid, pid) for pid, tid in assignments.items() if tid is not None],
        )


# ── Gemma 4 글 분석 큐 ──────────────────────────────────────────────
# "분석 결과가 없는 글" 자체가 대기열(FIFO)이다. DB에 있으므로 재시작·폭증에도
# 유실이 없고, 워커가 배치로 소화한다. 파싱 실패는 attempts 3회까지 재시도.

def load_analysis_queue(limit: int = 10, max_attempts: int = 3) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.title, p.text, p.created_at, p.embedding FROM posts p "
            "LEFT JOIN post_analysis a ON a.post_id = p.id "
            "WHERE a.post_id IS NULL AND p.text IS NOT NULL AND p.analysis_attempts < ? "
            "ORDER BY p.created_at DESC LIMIT ?",  # 최신 우선 — 관제는 신선도가 중요. 백로그도 끝까지 소화되므로 유실 없음
            (max_attempts, limit),
        ).fetchall()
    return [_hydrate(r) for r in rows]


def bump_analysis_attempts(post_ids: list[str]) -> None:
    if not post_ids:
        return
    with _connect() as conn:
        conn.executemany(
            "UPDATE posts SET analysis_attempts = analysis_attempts + 1 WHERE id = ?",
            [(pid,) for pid in post_ids],
        )


def save_analyses(rows: list[dict]) -> None:
    """rows: [{post_id, sentiment, major, topic_label}]"""
    if not rows:
        return
    now = _now()
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO post_analysis (post_id, sentiment, major, topic_label, analyzed_at) "
            "VALUES (:post_id, :sentiment, :major, :topic_label, :analyzed_at)",
            [{**r, "analyzed_at": now} for r in rows],
        )


def majority_topic_label(post_ids: list[str]) -> str | None:
    """멤버 글들의 adapters4 주제문구 다수결 — judge 입력 라벨용 (없으면 None)."""
    if not post_ids:
        return None
    ph = ",".join("?" * len(post_ids))
    with _connect() as conn:
        row = conn.execute(
            f"SELECT topic_label, COUNT(*) AS n FROM post_analysis "
            f"WHERE post_id IN ({ph}) AND topic_label IS NOT NULL "
            f"GROUP BY topic_label ORDER BY n DESC LIMIT 1",
            post_ids,
        ).fetchone()
    return row["topic_label"] if row else None


def analysis_queue_stats(max_attempts: int = 3) -> dict:
    with _connect() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM posts p LEFT JOIN post_analysis a ON a.post_id = p.id "
            "WHERE a.post_id IS NULL AND p.text IS NOT NULL AND p.analysis_attempts < ?",
            (max_attempts,),
        ).fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM post_analysis").fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM posts p LEFT JOIN post_analysis a ON a.post_id = p.id "
            "WHERE a.post_id IS NULL AND p.analysis_attempts >= ?",
            (max_attempts,),
        ).fetchone()[0]
    return {"pending": pending, "done": done, "failed": failed}


def load_analyses(hours: int = 24, limit: int = 200) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.title, p.url, p.created_at, p.category_id, c.name AS category_name, "
            "       a.sentiment, a.major, a.topic_label, a.gist, a.analyzed_at "
            "FROM post_analysis a JOIN posts p ON p.id = a.post_id "
            "LEFT JOIN categories c ON c.id = p.category_id "
            "WHERE p.created_at > ? "
            "ORDER BY p.created_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 알림 ────────────────────────────────────────────────────────────

def save_alert(category_id: int | None, label: str, size: int, heat: float,
               summary: str, decision: str, judge_input: str | None = None,
               topic_id: int | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO alerts (created_at, category_id, topic_id, label, size, heat, summary, decision, judge_input) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), category_id, topic_id, label, size, heat, summary, decision, judge_input),
        )


def has_recent_judgment(label: str, decision: str, minutes: int) -> bool:
    """같은 주제·같은 판정이 최근 N분 내 기록됐는지 (X 판정 중복 기록 방지용).

    버스트는 지속되는 동안 매분 재판정되므로, 기록은 쿨다운 간격으로만 남긴다.
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE label = ? AND decision = ? AND created_at > ? LIMIT 1",
            (label, decision, since),
        ).fetchone()
    return row is not None


def set_alert_feedback(alert_id: int, feedback: str | None) -> bool:
    """담당자 피드백 기록. 'O'(알림 적절)/'X'(불필요)/None(취소)."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE alerts SET feedback = ? WHERE id = ?", (feedback, alert_id)
        )
        return cur.rowcount > 0


def export_feedback_rows() -> list[tuple[str, str]]:
    """피드백이 달린 알림 → 파인튜닝 CSV용 (input, output) 쌍.

    GreatestStep의 notify_raw.csv 형식과 동일: input = 판단 모델 입력 원문,
    output = 담당자가 평가한 정답 라벨 (좋아요→O, 나빠요→X).
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT COALESCE(judge_input, label || '___' || label || '___' || summary), feedback "
            "FROM alerts WHERE feedback IN ('O', 'X') AND decision IN ('O', 'X')"  # 소강 행 제외
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def load_alerts(limit: int = 100) -> list[dict]:
    # topic_id 도입(2026-07-10) 이전 행은 주제명이 그대로 남아 있으면 이름으로 보정
    with _connect() as conn:
        rows = conn.execute(
            "SELECT a.id, a.created_at, a.category_id, a.label, a.size, a.heat, "
            "       a.summary, a.decision, a.judge_input, a.feedback, "
            "       COALESCE(a.topic_id, t.id) AS topic_id "
            "FROM alerts a "
            "LEFT JOIN topics t ON a.topic_id IS NULL AND t.name = a.label "
            "ORDER BY a.id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def load_topic_posts(topic_id: int, q: str = "", sort: str = "created_at",
                     order: str = "desc", page: int = 1, per_page: int = 50) -> dict:
    """주제에 묶인 글 목록 — 서버측 검색·정렬·페이지네이션 (주제 상세 페이지용).

    창 제한 없음: posts.topic_id 는 영속이라 지난 알림에서 들어와도 전체가 보인다.
    """
    sort = sort if sort in ("created_at", "title") else "created_at"
    order = "ASC" if order.lower() == "asc" else "DESC"
    per_page = max(1, min(per_page, 200))
    where, params = "topic_id = ?", [topic_id]
    if q:
        where += " AND (title LIKE ? OR body LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM posts WHERE {where}", params).fetchone()[0]
        page = max(1, min(page, (total + per_page - 1) // per_page or 1))
        rows = conn.execute(
            f"SELECT id, title, body, url, created_at FROM posts WHERE {where} "
            f"ORDER BY {sort} {order} LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
    return {"total": total, "page": page, "per_page": per_page,
            "posts": [dict(r) for r in rows]}
