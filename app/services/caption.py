"""이미지 캡셔닝 — 첨부 이미지를 텍스트로 바꿔 임베딩에 반영 (비동기 큐 워커).

EmbeddingGemma는 텍스트 전용이므로, 이미지는 Gemma 4 멀티모달로
"장별 한 문장 묘사(캡션)"를 만들어 본문 뒤에 붙인 뒤 재임베딩한다.

흐름 (2026-07-12 비동기 전환 — 수집·임베딩 경로를 막지 않는다):
    수집 시 preprocess 가 본문의 [첨부] 이미지 URL(최대 caption_max_images)을
    posts.image_path 에 JSON 배열로 저장 → 글은 텍스트만으로 즉시 임베딩·분석
    → caption_worker_loop(main.py)가 한가할 때 다운로드→캡션→재임베딩
    → 다음 분석 사이클부터 이미지 내용이 주제 묶기에 반영

- StubCaptioner: 이미지 무시 (mock 수집기는 이미지가 없음)
- MLXVLMCaptioner: mlx-vlm + Gemma 4 E4B. 활성화:
    1) uv sync --extra ml --extra vision
    2) CAPTIONER_BACKEND=mlx-vlm (2026-07-12부터 기본값)
- 실측(M-시리즈): 다운로드 0.1s/장 · 캡션 0.5s/장 · 3장 일괄 1.7s (글당 ~2s)
- ⚠️ 캡션은 임베딩(주제 묶기)에만 반영된다. adapters4 분석 입력에는 넣지
  않는다 — 학습(data4) 입력 형식과 어긋나므로 표본 검증 전에는 금지.
"""
import json
import re
import tempfile
from pathlib import Path
from typing import Protocol

from app import db
from app.config import settings

# 디시 이미지 서버는 Referer 없이 받으면 차단한다 (실측)
_DL_HEADERS = {
    "Referer": "https://gall.dcinside.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
}


class Captioner(Protocol):
    ready: bool
    name: str

    def caption_images(self, image_paths: list[str],
                       title: str = "", text: str = "") -> str | None:
        """이미지 1~N장 + 글 맥락 → 장별 '번호: 묘사/무관' 원문. 실패 시 None."""
        ...


class StubCaptioner:
    ready = False
    name = "stub (이미지 무시)"

    def caption_images(self, image_paths: list[str],
                       title: str = "", text: str = "") -> str | None:
        return None


class MLXVLMCaptioner:
    """Gemma 4 멀티모달 캡셔닝 (mlx-vlm). 최초 사용 시 1회만 로딩."""

    ready = True

    def __init__(self) -> None:
        self.name = f"mlx-vlm ({settings.categorizer_model})"
        self._loaded = None

    def _ensure_loaded(self):
        if self._loaded is None:
            from mlx_vlm import load  # uv sync --extra vision

            # ⚠️ strict=False 필수: 이 리포의 audio_tower conv 가중치 레이아웃이
            # mlx-vlm(0.6.x) 기대와 달라 로드가 깨진다 (실측 2026-07-12,
            # (128,3,3,1) vs (128,3,1,3)). 캡션은 오디오를 쓰지 않으므로 무해.
            self._loaded = load(settings.categorizer_model, strict=False)
        return self._loaded

    def caption_images(self, image_paths: list[str],
                       title: str = "", text: str = "") -> str | None:
        from app.services.mlx_runtime import run_on_mlx_thread

        def _impl() -> str | None:
            from mlx_vlm import generate
            from mlx_vlm.prompt_utils import apply_chat_template

            model, processor = self._ensure_loaded()
            # 글 맥락을 함께 주고 관련성 판단을 시킨다 — 디씨 특성상 첨부의
            # 상당수(~1/3 실측)가 글과 무관한 반응짤이라, 그대로 임베딩에 붙이면
            # 짧은 글일수록 캡션이 지배해 주제 묶기를 오염시킨다.
            # ("한국어" 명시 없으면 다중 이미지에서 가끔 영어로 답한다 — 실측)
            # ⚠️ 판정 기준은 "게임과 관련"이 아니라 "이 글의 내용을 보여주는가"다.
            # 느슨하게 쓰면 게임 캐릭터 일러 반응짤을 전부 '관련'으로 판정한다
            # (실측 1차 프롬프트: 무관 판정 0/16건 — 기준 명시+기본값 무관으로 교정)
            # '인증' 유형(2026-07-13 추가): 점수·승패 결과창 인증샷은 관련은 맞지만
            # 캡션 문구가 정형화되어("...완료 화면으로 점수를 보여준다") 서로 다른
            # 인증글들이 임베딩에서 한 덩어리로 뭉친다 (실측: '오상' 단문 인증글이
            # 52건짜리 주제의 필러가 됨) → 캡션을 좌표에 붙이지 않는다.
            # 조건 순서 중요 (실측): '묘사'가 모델의 기본 동작이라 묘사 조건을
            # 먼저 쓰면 결과창까지 전부 묘사로 빠진다 (인증 판정 0/22) —
            # 인증을 첫 조건으로, 묘사 조건에 "결과 화면 제외"를 명시할 것.
            ask = (
                f"[게시글]\n제목: {title}\n내용: {(text or '')[:300]}\n\n"
                f"첨부 이미지 {len(image_paths)}장 각각이 위 게시글에서 어떤 역할인지 "
                "판단하고, 이미지마다 '번호: ...' 형식으로 한국어 한 줄씩 출력하라.\n"
                "- 점수·승패·순위·보상·클리어 같은 '결과'를 보여주는 화면"
                "(전투 완료창, 최종 결과, 랭킹·점수 도달, 뽑기 결과)이면: 내용을 묘사하지 "
                "말고 '인증' 이라는 단어만 출력\n"
                "- 결과 화면이 아닌 정보성 이미지(오류·버그 장면, 세팅·장비 화면, "
                "공지 캡처, 글이 직접 언급하는 대상)면: 무엇을 보여주는지 한 문장으로 묘사\n"
                "- 글 내용과 무관한 반응짤·캐릭터 일러스트·만화 컷·밈이면: '무관' 만 출력 — "
                "디씨 특성상 첨부의 상당수가 이것이다. 캐릭터 일러스트는 글이 그 캐릭터의 "
                "외형·모습 자체를 이야기할 때만 묘사한다. 애매하면 '무관'.\n"
                "다른 말은 하지 마라.\n"
                "[출력 예시]\n1: 인증\n2: 강림 입장 직후 화면이 멈춘 오류 장면을 보여준다\n3: 무관"
            )
            prompt = apply_chat_template(
                processor, model.config, ask, num_images=len(image_paths),
            )
            out = generate(model, processor, prompt, image=image_paths,
                           max_tokens=settings.caption_max_tokens, verbose=False)
            raw = out.text if hasattr(out, "text") else str(out)
            return raw.strip() or None

        try:
            return run_on_mlx_thread(_impl)
        except Exception as e:
            print(f"[캡셔닝 실패] {image_paths}: {e}")
            return None


def _fetch_images(urls: list[str], dest_dir: str) -> list[str]:
    """이미지 URL들을 임시 디렉토리에 다운로드. 실패한 장은 건너뛴다."""
    import requests  # mlx-vlm 의존성으로 함께 설치됨

    paths = []
    for i, url in enumerate(urls):
        try:
            r = requests.get(url, headers=_DL_HEADERS, timeout=10)
            r.raise_for_status()
            # 움짤(mp4/webm)도 viewimage로 서빙된다 — 이미지가 아니면 스킵
            # (VLM에 넣으면 "Failed to load image"로 헛 재시도만 소모, 실측 ~2.5%)
            # ⚠️ Content-Type 으로 거르면 안 됨: 디시는 정상 이미지도
            # application/octet-stream 으로 준다 (실측 — 전량 오차단 사고).
            # 파일 매직 바이트로 판별한다: jpg/png/gif/webp만 통과.
            data = r.content
            if not data.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF")):
                continue
            if data[:4] == b"RIFF" and data[8:12] != b"WEBP":
                continue                      # RIFF 계열 중 webp 아닌 것(avi 등)
            p = Path(dest_dir) / f"img{i}.jpg"
            p.write_bytes(data)
            paths.append(str(p))
        except Exception as e:
            print(f"[이미지 다운로드 실패] {url[:80]}: {e}")
    return paths


_CAP_LINE = re.compile(r"^\s*(\d+)\s*[:.)]\s*(.+?)\s*$")


def _parse_relevant(raw: str, n_images: int) -> tuple[list[str], dict]:
    """'번호: 묘사/인증/무관' 원문 → (좌표에 붙일 묘사들, {'무관': n, '인증': n}).

    '인증'(정형 결과창)은 글과 관련은 있지만 캡션 문구가 정형화되어 임베딩을
    오염시키므로 묘사 리스트에 넣지 않는다. 형식 미준수 폴백: 전체 출력을
    캡션 하나로 취급 (관련성 판단 없던 구 동작).
    """
    rel, marks, parsed = [], {"무관": 0, "인증": 0}, False
    for line in raw.splitlines():
        m = _CAP_LINE.match(line)
        if not m:
            continue
        parsed = True
        desc = m.group(2).strip()
        if desc.startswith("무관"):
            marks["무관"] += 1
        elif desc.startswith("인증"):
            marks["인증"] += 1
        else:
            rel.append(desc)
    if not parsed:
        head = raw.strip()
        if head.startswith("무관"):
            return [], {"무관": n_images, "인증": 0}
        if head.startswith("인증"):
            return [], {"무관": 0, "인증": n_images}
        return ([head], marks) if head else ([], {"무관": n_images, "인증": 0})
    return rel, marks


def caption_batch(game_id: str | None = None) -> dict:
    """한 게임의 캡션 큐에서 글 N건 — 다운로드 → 관련성 판단+캡션 → 재임베딩.

    글과 관련 있다고 판정된 이미지의 묘사만 임베딩에 붙인다. 전부 무관이면
    캡션 없이 본문만으로 재임베딩 (구 캡션이 남긴 오염 벡터도 이때 정화된다).
    반환: {taken, captioned, irrelevant, failed}. 실패분은 caption_attempts 로
    최대 3회 재시도 후 포기 (다운로드 차단·파손 이미지 등). 이미지는 즉시 삭제.
    """
    game_id = game_id or settings.default_game_id
    captioner = get_captioner()
    if not captioner.ready:
        return {"taken": 0, "captioned": 0, "irrelevant": 0, "failed": 0}
    chunk = db.load_caption_queue(limit=settings.caption_batch_size, game_id=game_id)
    if not chunk:
        return {"taken": 0, "captioned": 0, "irrelevant": 0, "failed": 0}

    from app.services.embedding import get_embedder

    captioned, skipped, missed = 0, 0, []
    for p in chunk:
        # dedupe: 구버전 image_path에는 중복 URL이 남아 있다 (같은 캡션 반복 사고)
        urls = list(dict.fromkeys(json.loads(p["image_path"])))[: settings.caption_max_images]
        with tempfile.TemporaryDirectory(prefix="caption-") as tmp:
            paths = _fetch_images(urls, tmp)
            raw = captioner.caption_images(paths, p["title"] or "", p["text"] or "") \
                if paths else None
        if not raw:
            missed.append(p["id"])
            continue
        rel, marks = _parse_relevant(raw, len(paths))
        rel = list(dict.fromkeys(rel))     # 비슷한 이미지 3장 → 같은 문장 반복 방지
        if rel:
            # 캡션이 절단되지 않게 본문을 먼저 자른다 (임베딩 한도 2000자와 정합)
            cap = " ".join(rel)[:280]
            text = f"{(p['text'] or '')[:1700]} [이미지] {cap}"
            captioned += 1
        else:
            # 처리 완료 표식 — 재큐 방지. 인증/무관은 좌표에 반영하지 않는다.
            parts = [f"{k} {n}장" for k, n in marks.items() if n]
            cap = f"({' · '.join(parts) or '이미지 반영 안 함'})"
            text = p["text"] or ""                     # 본문만으로 임베딩 유지/정화
            skipped += 1
        vec = get_embedder().encode([text])[0]
        db.save_caption(game_id, p["id"], cap, vec)
    db.bump_caption_attempts(missed, game_id)
    return {"taken": len(chunk), "captioned": captioned,
            "irrelevant": skipped, "failed": len(missed)}


_captioner: Captioner | None = None


def get_captioner() -> Captioner:
    global _captioner
    if _captioner is None:
        if settings.captioner_backend == "mlx-vlm":
            _captioner = MLXVLMCaptioner()
        else:
            _captioner = StubCaptioner()
    return _captioner
