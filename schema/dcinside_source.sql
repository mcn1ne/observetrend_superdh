-- 수집 원본 DB(dcinside.db)의 스키마 — 구조만, 데이터 없음.
--
-- 이 DB는 저장소에 포함되지 않는다. 별도로 준비해 DCINSIDE_DB_PATH 로 연결한다.
-- 파이프라인은 이 DB를 읽기 전용(mode=ro)으로만 열며, 쓰지 않는다.
-- 실제로 읽는 컬럼은 posts 의 gallery · post_no · game · title · content ·
-- post_time · url 이다. game은 필수 필터, gallery는 선택 필터다.
-- (services/collect.py 의 DcinsideCollector). post_time 은 KST 기준
-- "YYYY-MM-DD HH:MM:SS" 문자열이어야 하며, 수집 시 UTC ISO-8601 로 변환된다.
--
-- 빈 DB 만들기:  sqlite3 dcinside.db < schema/dcinside_source.sql

CREATE TABLE posts (
    gallery       TEXT    NOT NULL,
    post_no       INTEGER NOT NULL,      -- 글 번호 (갤러리 내에서만 유일)
    game          TEXT,                  -- 게임명 (TrendSys source_game 필터)
    category      TEXT,                  -- 말머리
    title         TEXT,                  -- 제목
    content       TEXT,                  -- 본문
    author        TEXT,                  -- 글쓴이
    post_time     TEXT,                  -- 게시물 등록 시간 (KST)
    view_count    INTEGER,               -- 조회수 (최초 수집 시점)
    comment_count INTEGER,               -- 댓글 수 (최초 수집 시점)
    recommend     INTEGER,               -- 추천 수 (최초 수집 시점)
    url           TEXT,
    crawled_at    TEXT    NOT NULL,      -- 수집 시각
    PRIMARY KEY (gallery, post_no)
);

CREATE INDEX idx_posts_crawled  ON posts (crawled_at);
CREATE INDEX idx_posts_game_gallery_no ON posts (game, gallery, post_no);
