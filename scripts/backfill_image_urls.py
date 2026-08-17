"""기존 글의 body에서 첨부 이미지 URL을 추출해 image_path를 소급 채운다.

캡션 워커는 image_path가 있는 글만 대기열로 보므로, 이 스크립트를 돌린
범위만큼 캡션이 소급된다. 기본은 분석 창(window_hours)만 — 옛글 전체를
채우면 캡션 백로그가 커질 뿐 분석 창 밖이라 효과가 없다.

    uv run python scripts/backfill_image_urls.py [hours]
"""
import json
import sys

from app import db
from app.config import settings
from app.services.preprocess import extract_image_urls

hours = float(sys.argv[1]) if len(sys.argv) > 1 else settings.window_hours
for g in settings.games:
    posts = db.load_recent_posts(hours, g.id)
    filled = 0
    with db._connect() as conn:
        for p in posts:
            if p.get("image_path"):
                continue
            urls = extract_image_urls(p.get("body") or "")
            if urls:
                conn.execute(
                    "UPDATE posts SET image_path = ? WHERE game_id = ? AND id = ?",
                    (json.dumps(urls), g.id, p["id"]),
                )
                filled += 1
    print(f"{g.id}: 최근 {hours}h {len(posts)}건 중 {filled}건 image_path 채움")
