"""06-alert-judgment.md — 알림 판단 (단계 ④). ✅ 보유 파인튜닝 4B 사용.

GreatestStep 프로젝트에서 파인튜닝한 모델(adapters3)을 MLX-LM으로 구동한다.

현재 구성 (어댑터 미머지 상태):
    베이스 mlx-community/gemma-4-e4b-it-qat-4bit + LoRA 어댑터 adapters3

adapters3 를 머지(mlx_lm.fuse)한 뒤에는:
    .env 에 JUDGE_FUSED_PATH=<머지된 모델 경로> 한 줄만 추가하면
    베이스+어댑터 대신 머지 모델을 단독 로드한다. (코드 수정 불필요)

입력 형식은 adapters3 학습 데이터(data3)와 동일하게 맞춘다:
    "{주제 라벨}___{대표 제목}___{요약}"
출력: 'O' (알림 발송) / 'X' (미발송)

체크포인트(06 문서): 판단 결과를 JSONL 로그로 남겨 오탐/미탐 모니터링.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.config import settings


def build_judge_input(label: str, title: str, summary: str) -> str:
    """adapters3 학습 입력 형식으로 조립."""
    return f"{label}___{title}___{summary}"


def _log_decision(text: str, decision: str, raw: str) -> None:
    path = Path(settings.judge_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "input": text,
            "decision": decision,
            "raw_output": raw,
        }, ensure_ascii=False) + "\n")


def _parse_decision(out: str) -> str:
    """모델 출력에서 O/X 판정 추출.

    머지 모델은 '<|turn>model\\nO' 처럼 턴 토큰 파편이 섞여 나올 수 있으므로
    특수 토큰을 걷어낸 뒤 처음 등장하는 단독 O/X를 찾는다. 없으면 X(보수적).
    """
    from app.services.mlx_runtime import extract_final_channel

    cleaned = extract_final_channel(out)
    for token in cleaned.split():
        if token in ("O", "X"):
            return token
    return "X"


class Judge(Protocol):
    ready: bool
    name: str

    def judge(self, text: str) -> str:
        """→ 'O' | 'X'"""
        ...


class MLXJudge:
    """파인튜닝 4B 판단기. 최초 사용 시 1회만 로딩 (요청마다 로딩 금지)."""

    ready = True

    def __init__(self) -> None:
        if settings.judge_fused_path:
            self.name = f"mlx fused ({settings.judge_fused_path})"
        else:
            self.name = f"mlx ({settings.judge_model} + adapters3)"
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from mlx_lm import load

        if settings.judge_fused_path:
            # 머지 완료 후: 머지된 모델 단독 로드
            self._model, self._tokenizer = load(settings.judge_fused_path)
        else:
            # 머지 전: 베이스 + adapters3 LoRA
            self._model, self._tokenizer = load(
                settings.judge_model, adapter_path=settings.judge_adapter_path
            )

    def judge(self, text: str) -> str:
        # MLX 스트림은 스레드에 묶이므로 로딩·추론을 전용 스레드에서 실행
        from app.services.mlx_runtime import run_on_mlx_thread

        return run_on_mlx_thread(self._judge_impl, text)

    def _judge_impl(self, text: str) -> str:
        from mlx_lm import generate

        self._ensure_loaded()
        msgs = [
            {"role": "system", "content": settings.judge_system_prompt},
            {"role": "user", "content": text},
        ]
        prompt = self._tokenizer.apply_chat_template(msgs, add_generation_prompt=True)
        out = generate(self._model, self._tokenizer, prompt=prompt,
                       max_tokens=settings.judge_max_tokens, verbose=False).strip()
        decision = _parse_decision(out)
        _log_decision(text, decision, out)
        return decision


class StubJudge:
    """판단 모델 없이 돌릴 때의 대체물 (키워드 규칙). JUDGE_BACKEND=stub 로 활성화."""

    ready = False
    name = "stub (키워드 규칙 — 실제 모델 아님)"
    _KEYWORDS = ("결제", "환불", "서버", "접속", "크래시", "튕김", "버그", "핵", "도용", "장애")

    def judge(self, text: str) -> str:
        decision = "O" if any(k in text for k in self._KEYWORDS) else "X"
        _log_decision(text, decision, "(stub)")
        return decision


_judge: Judge | None = None


def get_judge() -> Judge:
    global _judge
    if _judge is None:
        if settings.judge_backend == "mlx":
            _judge = MLXJudge()
        else:
            _judge = StubJudge()
    return _judge
