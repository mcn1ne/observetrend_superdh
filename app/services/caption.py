"""이미지 캡셔닝 슬롯 — 첨부 이미지를 텍스트로 바꿔 임베딩에 포함.

EmbeddingGemma는 텍스트 전용이므로, 이미지는 Gemma 4 멀티모달로
"한 문장 묘사(캡션)"를 만들어 제목+본문 뒤에 붙인 뒤 텍스트로 임베딩한다.

- StubCaptioner: 이미지 무시 (기본값 — mock 수집기는 이미지가 없음)
- MLXVLMCaptioner: mlx-vlm + Gemma 4 E4B. 활성화:
    1) uv sync --extra vision
    2) .env 에 CAPTIONER_BACKEND=mlx-vlm
"""
from typing import Protocol

from app.config import settings


class Captioner(Protocol):
    ready: bool
    name: str

    def caption(self, image_path: str) -> str | None:
        """이미지 → 한 문장 묘사. 실패 시 None."""
        ...


class StubCaptioner:
    ready = False
    name = "stub (이미지 무시)"

    def caption(self, image_path: str) -> str | None:
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

            self._loaded = load(settings.categorizer_model)
        return self._loaded

    def caption(self, image_path: str) -> str | None:
        from app.services.mlx_runtime import run_on_mlx_thread

        def _impl() -> str | None:
            from mlx_vlm import generate
            from mlx_vlm.prompt_utils import apply_chat_template

            model, processor = self._ensure_loaded()
            prompt = apply_chat_template(
                processor, model.config,
                "이 이미지가 무엇을 보여주는지 한 문장으로 묘사하라.", num_images=1,
            )
            out = generate(model, processor, prompt, image=[image_path],
                           max_tokens=80, verbose=False)
            text = out.text if hasattr(out, "text") else str(out)
            return text.strip() or None

        try:
            return run_on_mlx_thread(_impl)
        except Exception as e:
            print(f"[캡셔닝 실패] {image_path}: {e}")
            return None


_captioner: Captioner | None = None


def get_captioner() -> Captioner:
    global _captioner
    if _captioner is None:
        if settings.captioner_backend == "mlx-vlm":
            _captioner = MLXVLMCaptioner()
        else:
            _captioner = StubCaptioner()
    return _captioner
