"""수집원(DB1)에 새로 나타난 게임을 찾아 자동 등록한다.

크롤러가 새 갤러리를 긁기 시작해도 `settings.games` 에 없으면 TrendSys 는 그 글을
한 건도 가져오지 않는다(`collect_step` 이 등록된 게임만 순회한다). 사람이 매번
config 를 고치지 않도록, DB1 을 훑어 문턱을 넘은 게임을 런타임에 등록한다.

가드레일 셋 — 이게 없으면 자동 등록이 오히려 해가 된다:

1. **볼륨 문턱** (`game_discovery_min_posts` / `_days`)
   크롤러의 오타·테스트 항목이나 몇 건짜리 갤러리가 관제 대상이 되면 안 된다.

2. **기존 게임은 source_game 으로 식별**
   id 가 아니라 원본 게임명으로 대조한다. id 로 비교하면 손으로 지은 `snr` 과
   자동 생성될 `sevennightsrebirth` 가 다르다는 이유로 같은 게임이 두 번 등록되고,
   데이터가 두 파티션으로 갈라진다.

3. **도메인 컨텍스트는 이름만 자동**
   등록된 게임의 프롬프트는 `GameConfig.name` 으로 채워지므로 최소한 갤러리를
   오해하지는 않는다. 그 게임 고유 은어는 `domain_extra` 에 사람이 채운다
   (services/gemma_analyze.domain_context 참고).

카테고리 분류는 별도 조치가 필요 없다 — '일반' 세분화를 LLM 라벨 판정이 맡으므로
(services/label_category) 새 게임의 어휘를 미리 알 필요가 없다.
"""
import re
import sqlite3

from app.config import GameConfig, settings

_SLUG_OK = re.compile(r"[^a-z0-9_]+")


def _slugify(gallery: str | None, game_name: str, taken: set[str]) -> str:
    """갤러리 id 를 game_id 슬러그로. 비었거나 충돌하면 접미사를 붙인다."""
    base = _SLUG_OK.sub("_", (gallery or game_name).strip().lower()).strip("_")
    base = base[:24] or "game"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


def scan_source(db_path: str, min_posts: int, days: int) -> list[dict]:
    """DB1에서 (게임, 갤러리, 최근 글 수) 를 문턱 이상만 반환. 읽기 전용."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as e:
        print(f"[게임 발견] 수집원 열기 실패 ({db_path}): {e}")
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT game, gallery, COUNT(*) n FROM posts "
            "WHERE game IS NOT NULL AND TRIM(game) <> '' "
            "  AND post_time >= datetime('now', 'localtime', ?) "
            "GROUP BY game, gallery HAVING n >= ? ORDER BY n DESC",
            (f"-{int(days)} days", int(min_posts)),
        ).fetchall()
    except sqlite3.Error as e:
        print(f"[게임 발견] 수집원 조회 실패: {e}")
        return []
    finally:
        conn.close()
    return [{"game": r["game"], "gallery": r["gallery"], "n": r["n"]} for r in rows]


def discover_new_games() -> list[GameConfig]:
    """아직 등록되지 않은 게임의 GameConfig 목록 (settings 는 건드리지 않는다)."""
    found = scan_source(settings.dcinside_db_path,
                        settings.game_discovery_min_posts,
                        settings.game_discovery_days)
    if not found:
        return []

    # ⚠️ id 가 아니라 source_game 으로 대조 — 같은 게임이 두 파티션으로 갈라지는 것을 막는다
    known = {g.source_game for g in settings.games}
    taken = {g.id for g in settings.games}

    new: list[GameConfig] = []
    for row in found:
        if row["game"] in known:
            continue
        slug = _slugify(row["gallery"], row["game"], taken)
        taken.add(slug)
        known.add(row["game"])
        new.append(GameConfig(
            id=slug,
            name=row["game"],
            source_db_path=settings.dcinside_db_path,
            source_game=row["game"],
            source_gallery=row["gallery"],
        ))
    return new


def register_new_games() -> dict:
    """새 게임을 settings.games 에 붙이고 games 테이블에 반영한다.

    다음 collect_step 부터 그 게임의 수집이 시작된다(커서 0 → 백필 창만큼 소급).
    """
    if not settings.game_discovery_enabled:
        return {"enabled": False, "added": []}

    new = discover_new_games()
    if not new:
        return {"enabled": True, "added": []}

    from app import db

    settings.games.extend(new)
    db.seed_games()                      # 커서(last_post_no)는 보존되는 upsert
    for g in new:
        print(f"[게임 발견] 등록: {g.name} (id={g.id}, gallery={g.source_gallery})")
    return {"enabled": True,
            "added": [{"id": g.id, "name": g.name, "gallery": g.source_gallery} for g in new]}
