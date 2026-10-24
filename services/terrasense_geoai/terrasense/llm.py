from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from terrasense.config import settings

logger = logging.getLogger(__name__)

INTENT_SCHEMA_HINT = """
Return ONLY JSON with keys:
activity (one of: running,cycling,golf,cricket,football,tennis,hiking,picnic,outdoor_event,photography or null),
place_name (string or null),
latitude (number or null),
longitude (number or null),
radius_km (number),
date_text (string or null),
time_window_text (string or null),
duration_minutes (int),
constraints (array from: asthma,children,older_adults,dog,heat_sensitive),
preferences (array of strings),
unsupported (bool),
missing_location (bool),
confidence (0-1)
Never invent coordinates. If no place is named, missing_location=true and place_name=null.
"""


class SmallLanguageModel:
    """Qwen2.5-1.5B-Instruct loaded once. CPU by default. Optional 8-bit quant."""

    def __init__(self) -> None:
        self.model_id = settings.model_id
        self.revision = settings.model_revision
        self.ready = False
        self.error: str | None = None
        self.param_count_claimed = "1.54B"
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None

    def status(self) -> dict[str, Any]:
        return {
            "id": self.model_id,
            "revision": self.revision,
            "ready": self.ready,
            "error": self.error,
            "parameter_count": self.param_count_claimed,
            "licence": "Apache-2.0",
            "device": settings.model_device,
            "quantized": settings.quantize,
            "load_requested": settings.load_model,
        }

    def load(self) -> None:
        if not settings.load_model:
            self.error = "Model load skipped (TERRASENSE_LOAD_MODEL=false). Heuristic parser active."
            logger.warning(self.error)
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            self.error = f"transformers/torch not installed: {exc}"
            logger.warning(self.error)
            return
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id, revision=self.revision, trust_remote_code=True
            )
            kwargs: dict[str, Any] = {
                "revision": self.revision,
                "trust_remote_code": True,
                "torch_dtype": torch.float32 if settings.model_device == "cpu" else torch.float16,
            }
            if settings.quantize:
                try:
                    kwargs["load_in_8bit"] = True
                except Exception:
                    pass
            self._model = AutoModelForCausalLM.from_pretrained(self.model_id, **kwargs)
            self._model.eval()
            if settings.model_device == "cpu":
                self._model.to("cpu")
            self.ready = True
            self.error = None
            logger.info("Loaded %s revision %s", self.model_id, self.revision)
        except Exception as exc:  # noqa: BLE001 — load failures must not crash API
            self.error = f"Model load failed: {type(exc).__name__}"
            logger.exception("Model load failed")

    def generate(self, system: str, user: str, max_new_tokens: int | None = None) -> str | None:
        if not self.ready or self._model is None or self._tokenizer is None:
            return None
        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            text = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = self._tokenizer(text, return_tensors="pt")
            if settings.model_device == "cpu":
                inputs = {k: v.cpu() for k, v in inputs.items()}
            with self._lock:
                import torch

                with torch.no_grad():
                    out = self._model.generate(
                        **inputs,
                        max_new_tokens=max_new_tokens or settings.model_max_new_tokens,
                        do_sample=False,
                        pad_token_id=self._tokenizer.eos_token_id,
                    )
            gen = out[0][inputs["input_ids"].shape[-1] :]
            return self._tokenizer.decode(gen, skip_special_tokens=True)
        except Exception:
            logger.exception("Inference failed")
            return None


MODEL = SmallLanguageModel()


def extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    blob = match.group(0)
    try:
        data = json.loads(blob)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        blob2 = re.sub(r",\s*}", "}", blob)
        blob2 = re.sub(r",\s*]", "]", blob2)
        try:
            data = json.loads(blob2)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
