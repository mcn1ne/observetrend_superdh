"""02-preprocessing.md — 데이터 전처리 (단계 0).

묶기 정확도의 8할이 여기서 결정된다. 은어 사전은 운영하며 성장시키는 것.
"""
import html
import json
import re

from app.config import settings

# 크롤러가 본문 끝에 남기는 "[첨부] URL..." 중 디시 이미지 서버 URL만.
# (유튜브 썸네일·nstatic 기본이미지 등 노이즈는 이 패턴에 안 걸린다)
_IMG_URL = re.compile(
    r"https?://dcimg\d*\.dcinside\.(?:com|co\.kr)/viewimage\.php\?[^\s\"'<>]+"
)


def extract_image_urls(body: str, limit: int | None = None) -> list[str]:
    """본문 원문에서 첨부 이미지 URL을 순서대로 추출 (중복 제거, 최대 limit장).

    크롤러가 같은 이미지를 본문/첨부에 두 번 남기는 경우가 있어 dedupe 필수
    (실측: 같은 캡션 문장이 3번 반복되는 사고).
    """
    urls = list(dict.fromkeys(_IMG_URL.findall(body or "")))
    return urls[: limit if limit is not None else settings.caption_max_images]

# 은어/오타 정규화 사전 — 클러스터링 결과에서 "같은 주제인데 안 묶인 글"을
# 발견할 때마다 원인이 된 은어를 계속 추가한다 (한 번에 완성 X)
SLANG_MAP = {
    "ㄹㅇ": "진짜",
    "갠소": "개인소장",
    "섭종": "서비스 종료",
    "현질": "결제",
    "핵쟁이": "핵 사용자",
    "튕김": "강제 종료",
}

MIN_TEXT_LEN = 10       # "ㅇㅇ", "ㅋㅋ" 등 필터 기준 — 데이터 보며 조절
MAX_TEXT_LEN = 2000     # 임베딩 입력 길이 제한 고려 (03 문서: 2048토큰)


def clean_text(text: str) -> str:
    text = html.unescape(text)                            # &nbsp; &amp; 등 엔티티 → 일반 문자
    text = re.sub(r"\[첨부\]", " ", text)                 # 크롤러 이미지 플레이스홀더 제거
    text = re.sub(r"-\s*dc official App", " ", text, flags=re.I)  # 디씨 앱 푸터 제거
    text = re.sub(r"https?://\S+", " ", text)             # URL 제거
    text = re.sub(r"<[^>]+>", " ", text)                  # 이미지/HTML 태그 제거
    text = re.sub(r"[^\w\s가-힣ㄱ-ㅎㅏ-ㅣ]", " ", text)   # 특수문자 정리
    for slang, std in SLANG_MAP.items():                  # 은어 → 표준어
        text = text.replace(slang, std)
    return re.sub(r"\s+", " ", text).strip()              # 연속 공백 정리


def preprocess(posts: list[dict]) -> list[dict]:
    """posts: [{id, title, body, created_at}] → text 필드 추가 + 필터링.

    1) 제목+본문 합치기 (제목에 핵심 주제가 많음)
    2) 너무 짧은 글 제거
    3) 완전 중복 제거 (첫 글만)
    4) 너무 긴 글은 앞부분만
    """
    seen: set[str] = set()
    out: list[dict] = []
    for p in posts:
        text = clean_text(f"{p.get('title') or ''} {p.get('body') or ''}")
        if len(text) < MIN_TEXT_LEN:
            continue
        if text in seen:
            continue
        seen.add(text)
        # 첨부 이미지 URL은 캡션 워커의 대기열 표식 — JSON 배열로 image_path에.
        # (텍스트에서는 위 clean_text가 URL을 지우므로 임베딩엔 안 섞인다)
        urls = extract_image_urls(p.get("body") or "")
        out.append({**p, "text": text[:MAX_TEXT_LEN],
                    "image_path": json.dumps(urls) if urls else None})
    return out
