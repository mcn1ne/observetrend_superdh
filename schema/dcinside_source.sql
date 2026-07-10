-- 수집 원본 DB(dcinside.db)의 스키마 — 구조만, 데이터 없음.
--
-- 이 DB는 저장소에 포함되지 않는다. 별도로 준비해 DCINSIDE_DB_PATH 로 연결한다.
-- 파이프라인은 이 DB를 읽기 전용(mode=ro)으로만 열며, 쓰지 않는다.
-- 실제로 읽는 컬럼은 posts 의 post_no · title · content · post_time · url 다섯 개다
-- (services/collect.py 의 DcinsideCollector). post_time 은 KST 기준
-- "YYYY-MM-DD HH:MM:SS" 문자열이어야 하며, 수집 시 UTC ISO-8601 로 변환된다.
--
-- 빈 DB 만들기:  sqlite3 dcinside.db < schema/dcinside_source.sql

CREATE TABLE posts (
    post_no       INTEGER PRIMARY KEY,   -- 글 번호 (증분 수집의 커서)
    gallery       TEXT    NOT NULL,
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
    embedded      INTEGER NOT NULL DEFAULT 0,
    topic_checked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE topic_matches (
    post_no    INTEGER NOT NULL,
    topic      TEXT    NOT NULL,
    score      REAL,                     -- 코사인 유사도 (키워드 전용 매칭이면 NULL 가능)
    method     TEXT,                     -- vec / kw / vec+kw
    matched_at TEXT    NOT NULL,
    PRIMARY KEY (post_no, topic)
);

CREATE INDEX idx_posts_crawled  ON posts (crawled_at);
CREATE INDEX idx_posts_embedded ON posts (embedded);
CREATE INDEX idx_matches_at     ON topic_matches (matched_at);
CREATE INDEX idx_matches_topic  ON topic_matches (topic);
