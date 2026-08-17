"""02-preprocessing.md — 수집 슬롯 (크롤링 + 글 저장/조회).

- DcinsideCollector: etl_dcinside 프로젝트의 dcinside.db(DB1)에서 증분 수집.
  크롤러(etl_dcinside/crawler.py)가 DB1에 쌓아둔 글을 post_no 기준으로
  새 글만 읽어온다 (읽기 전용 접속 — 크롤러와 잠금 충돌 방지).
- MockCollector: 데모용 가상 게시글 생성기.

⚠️ 실제 크롤링 시 주의 (08 §8): 요청 간격·User-Agent·robots.txt·이용약관.
가능한 한 빨리 정식 API로 전환할 것.
"""
import random
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from app import db
from app.config import settings

_KST = ZoneInfo("Asia/Seoul")


class Collector(Protocol):
    def fetch_new_posts(self) -> list[dict]:
        """새 글 목록 반환: [{id, title, body, created_at}]"""
        ...


# ── 데모용 가상 게시판 ──────────────────────────────────────────────
# 주제별 글 템플릿. burst=True 주제는 확률적으로 "확산"을 일으켜
# 08 문서의 급증 탐지 로직을 눈으로 확인할 수 있게 한다.
_TOPICS = [
    {
        "tag": "결제 오류",
        "burst": True,
        "titles": [
            "결제했는데 다이아 안 들어옴",
            "현질 먹튀 당한 사람 나만 아니지?",
            "결제 오류 환불 어떻게 함?",
            "구글 결제 됐는데 아이템이 없다",
            "이중 결제 됐는데 고객센터 답이 없네",
        ],
        "bodies": [
            "방금 패키지 질렀는데 재화가 안 들어옵니다. 영수증은 찍혔어요.",
            "결제 완료 문자는 왔는데 게임에는 반영이 안 됨. 이거 큰 문제 아님?",
            "환불 신청하려는데 절차 아는 사람? 고객센터 응답 없음.",
        ],
    },
    {
        "tag": "서버 접속 장애",
        "burst": True,
        "titles": [
            "지금 서버 터졌냐",
            "로그인이 안 되는데 나만 그럼?",
            "점검도 아닌데 왜 접속이 안 되지",
            "게임 켜면 무한 로딩 걸림",
            "서버 튕김 현상 심각하네",
        ],
        "bodies": [
            "10분째 로그인 화면에서 멈춰 있음. 다들 접속 됨?",
            "레이드 도중에 튕겨서 보상 날아감. 보상 복구 해주냐?",
            "와이파이 문제인 줄 알았는데 데이터로 해도 안 됨.",
        ],
    },
    {
        "tag": "밸런스 불만",
        "burst": False,
        "titles": [
            "신캐 성능 이게 맞냐",
            "이번 패치로 내 캐릭 관짝 갔다",
            "PVP 밸런스 답 없네",
            "메타 고착화 심각한 듯",
        ],
        "bodies": [
            "신규 캐릭터가 기존 캐릭터 상위호환이면 어쩌라는 거임.",
            "너프할 걸 너프해야지 왜 멀쩡한 캐릭을 건드림?",
            "랭킹전 상위권 죄다 같은 조합이다. 재미없음.",
        ],
    },
    {
        "tag": "공략/덱 공유",
        "burst": False,
        "titles": [
            "신규 던전 공략 정리해봄",
            "무과금 덱 추천 좀",
            "이번 이벤트 효율 계산해봤다",
            "보스전 꿀팁 공유",
        ],
        "bodies": [
            "2페이즈에서 광역기 조심하고 힐러 뒤로 빼면 됨.",
            "무과금 기준 가성비 조합 정리. 참고하셈.",
            "이벤트 재화 환산하면 하루 30분 투자가 최적임.",
        ],
    },
    {
        "tag": "잡담",
        "burst": False,
        "titles": [
            "오늘 뽑기 운 미쳤다",
            "출석 보상 뭐 나옴?",
            "다들 몇 년차임?",
            "이 게임 스토리 은근 재밌네",
        ],
        "bodies": [
            "10연차에 픽업 떴다. 오늘 로또 사야 하나.",
            "그냥 저냥 하는 얘기. 심심해서 씀.",
            "스토리 스킵 안 하고 보는 사람 있음?",
        ],
    },
]


_FILLERS = [
    "", "아 진짜 답답하다.", "다들 어떰?", "운영자 보고 있냐.", "정보 공유 차원에서 올림.",
    "혹시 나만 그런가 싶어서 씀.", "빠른 답변 부탁.", "참고하셈.", "한숨만 나온다.",
]


def _make_post(topic: dict, created_at: datetime) -> dict:
    # 같은 템플릿이라도 문구를 조금씩 바꿔 실제 게시판처럼 중복을 피한다
    # (완전 동일 텍스트는 전처리(02)의 중복 제거에 걸러지므로)
    body = f"{random.choice(topic['bodies'])} {random.choice(_FILLERS)} ({uuid.uuid4().hex[:4]})"
    return {
        "id": uuid.uuid4().hex[:12],
        "title": random.choice(topic["titles"]),
        "body": body,
        "created_at": created_at.isoformat(),
    }


class MockCollector:
    """분당 1~2건 수준의 평상시 글 + 확률적 버스트를 만드는 가상 수집기."""

    def __init__(self) -> None:
        self._burst_topic: dict | None = None
        self._burst_until: datetime | None = None
        self._backfilled = False

    def _backfill(self, now: datetime) -> list[dict]:
        """최초 1회: 최근 6시간치 평상시 글을 채워 기준선을 만든다.

        밀도는 문서 기준(일 2천 건 ≈ 분당 1.4건)에 맞춘다 (08 §7).
        """
        posts = []
        for minutes_ago in range(360, 0, -1):
            for _ in range(random.choices([0, 1, 2, 3], weights=[35, 35, 20, 10])[0]):
                topic = random.choice(_TOPICS)
                posts.append(_make_post(topic, now - timedelta(minutes=minutes_ago)))
        return posts

    def fetch_new_posts(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        posts: list[dict] = []

        if not self._backfilled:
            self._backfilled = True
            posts.extend(self._backfill(now))

        # 평상시: 0~2건
        for _ in range(random.choices([0, 1, 2], weights=[35, 45, 20])[0]):
            posts.append(_make_post(random.choice(_TOPICS), now))

        # 버스트 시작/유지: 5% 확률로 10분간 특정 주제가 급증
        if self._burst_topic is None and random.random() < 0.05:
            self._burst_topic = random.choice([t for t in _TOPICS if t["burst"]])
            self._burst_until = now + timedelta(minutes=10)
        if self._burst_topic is not None:
            if self._burst_until and now < self._burst_until:
                for _ in range(random.randint(2, 4)):
                    posts.append(_make_post(self._burst_topic, now))
            else:
                self._burst_topic = None
                self._burst_until = None

        return posts


# ── 디씨인사이드 수집기 (DB1 = etl_dcinside/dcinside.db) ─────────────

class DcinsideCollector:
    """DB1에서 원본 게임명(+선택 갤러리)으로 격리해 새 글을 읽는다.

    - 크롤러가 DB1에 계속 적재 중이므로 읽기 전용(URI mode=ro)으로 연다.
    - post_time 은 KST 로컬 시각 문자열("YYYY-MM-DD HH:MM:SS")
      → 우리 DB2는 UTC ISO 이므로 변환한다.
    - 최초 실행 시에는 dcinside_backfill_hours 이내의 글만 소급 수집
      (분석 창은 24h — 몇 달치 옛글에 임베딩·분류 비용을 쓰지 않기 위함).
    """

    def __init__(self, game_id: str, source_db_path: str, source_game: str,
                 source_gallery: str | None = None) -> None:
        self.game_id = game_id
        self.source_db_path = source_db_path
        self.source_game = source_game
        self.source_gallery = source_gallery
        # 증분 커서를 games 테이블에서 로드(영속) — 재시작 시 백필 폭주 방지.
        self._last_post_no = db.get_game_cursor(game_id)
        self._initialized = self._last_post_no > 0   # 이미 수집 이력 있으면 백필 생략

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.source_db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _to_utc_iso(post_time: str) -> str:
        dt = datetime.strptime(post_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST)
        return dt.astimezone(timezone.utc).isoformat()

    def fetch_new_posts(self) -> list[dict]:
        # game 필터는 필수: 같은 원본 DB에 여러 게임이 들어와도 다른 게임의 글을
        # 현재 TrendSys game_id로 잘못 태깅하지 않는다. gallery는 한 게임 안에서
        # 특정 디씨 게시판만 선택할 때 추가로 좁히는 선택 필터다.
        source_clause = " AND game = ?"
        source_params = [self.source_game]
        if self.source_gallery:
            source_clause += " AND gallery = ?"
            source_params.append(self.source_gallery)
        with self._connect() as conn:
            if not self._initialized:
                self._initialized = True
                since_kst = (
                    datetime.now(_KST) - timedelta(hours=settings.dcinside_backfill_hours)
                ).strftime("%Y-%m-%d %H:%M:%S")
                rows = conn.execute(
                    "SELECT post_no, title, content, post_time, url FROM posts "
                    f"WHERE post_time >= ?{source_clause} ORDER BY post_no",
                    [since_kst, *source_params],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT post_no, title, content, post_time, url FROM posts "
                    f"WHERE post_no > ?{source_clause} ORDER BY post_no",
                    [self._last_post_no, *source_params],
                ).fetchall()

        posts = []
        for r in rows:
            self._last_post_no = max(self._last_post_no, r["post_no"])
            if not r["post_time"]:
                continue
            posts.append({
                "game_id": self.game_id,
                "id": str(r["post_no"]),
                "title": r["title"] or "",
                "body": r["content"] or "",
                "created_at": self._to_utc_iso(r["post_time"]),
                "url": r["url"] or "",
            })
        return posts


def get_collector() -> Collector:
    # dcinside 는 게임별로 collect_step 에서 직접 생성(게임마다 소스/커서가 다름).
    # 여기서는 mock 만 반환한다.
    if settings.collector_backend == "mock":
        return MockCollector()
    raise ValueError(f"알 수 없는 수집 백엔드: {settings.collector_backend}")
