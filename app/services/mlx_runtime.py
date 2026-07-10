"""MLX 전용 실행 스레드 + 텍스트 모델 공유 캐시.

MLX의 GPU 스트림은 생성된 스레드에 묶인다. FastAPI는 분석을 스레드풀
(asyncio.to_thread)에서 돌리므로, 모델 로딩과 generate가 서로 다른 스레드에서
실행되면 `RuntimeError: There is no Stream(gpu, N) in current thread`가 난다.

해결: MLX를 쓰는 모든 작업(판단·요약·분류·캡셔닝)을 max_workers=1 전용
스레드로 보내 로딩·추론이 항상 같은 스레드에서 실행되게 한다. (부수 효과로
MLX 추론이 직렬화되어 동시 generate로 인한 메모리 경합도 방지된다.)

같은 베이스 모델(gemma-4-e4b)을 요약·분류가 공유하도록 로딩 캐시를 둔다.
캐시 키는 (모델 경로, 어댑터 경로) — 어댑터가 붙으면 별도 인스턴스가 되므로
(글 분석 adapters4, 판단 adapters3) 어댑터 하나당 메모리 ~4GB가 추가된다.
"""
import re
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")


def extract_final_channel(text: str) -> str:
    """Gemma 4 출력에서 채널/특수 토큰을 정리해 최종 답만 남긴다.

    Gemma 4는 사고(thought)·최종답(final) 채널을 쓰는데, 디토크나이즈에 따라
    마커가 <|channel|> / <|channel> / <channel|> 등 여러 변형으로 나오고,
    최종답 조각에 'final' 채널명이 아예 안 붙기도 한다. 규칙:
    - 채널 마커 이후 조각 중 'thought'로 시작하는 것은 사고 → 버림
    - 'final'로 시작하면 그 접두어를 떼고 답으로 사용
    - 채널명이 없는 조각은 내용 그대로 답 후보 → 마지막 것을 채택
    - 답 후보가 하나도 없으면(사고만 있음/잘림) 빈 문자열 = 폴백 신호
    """
    if re.search(r"<\|?channel\|?>", text):
        segments = re.split(r"<\|?channel\|?>", text)
        final = None
        for seg in segments[1:]:            # [0]은 마커 이전 접두부
            seg = seg.strip()
            if seg.startswith("thought"):
                continue                     # 사고 채널 → 버림
            if seg.startswith("final"):
                seg = seg[len("final"):].strip()
            if seg:
                final = seg                  # 마지막 비-사고 조각 채택
        if final is None:
            return ""
        text = final
    text = re.sub(r"<\|[^|>]*\|?>\s*(model|user|system)?\n?", " ", text)  # 턴/특수 토큰 제거
    return text.strip()

_model_cache: dict[tuple, tuple] = {}  # (모델 경로, 어댑터 경로) → (model, tokenizer)


def run_on_mlx_thread(fn, *args, **kwargs):
    """fn을 MLX 전용 스레드에서 실행하고 결과를 동기 반환."""
    return _executor.submit(fn, *args, **kwargs).result()


def get_text_model(model_path: str, adapter_path: str | None = None):
    """텍스트 모델을 1회만 로딩해 공유. ⚠️ MLX 스레드 안에서만 호출할 것."""
    key = (model_path, adapter_path)
    if key not in _model_cache:
        from mlx_lm import load

        _model_cache[key] = load(model_path, adapter_path=adapter_path)
    return _model_cache[key]


def generate_text(model_path: str, messages: list[dict], max_tokens: int = 256,
                  adapter_path: str | None = None) -> str:
    """공유 모델(베이스 or 베이스+LoRA)로 채팅 생성 — MLX 전용 스레드에서 실행됨."""

    def _impl() -> str:
        from mlx_lm import generate

        model, tokenizer = get_text_model(model_path, adapter_path)
        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        return generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)

    return run_on_mlx_thread(_impl)
