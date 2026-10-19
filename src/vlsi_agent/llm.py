"""Talk to the language model: a real Ollama client and a scripted fake for tests.

Both implement the same small interface, `complete(messages) -> Completion`, so agent
code never needs to know which one it has. FakeLLM makes agent logic (attempt limits,
token budgets, feedback handling) testable without a GPU, with exact, repeatable
answers.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, Sequence

from vlsi_agent.config import LLMConfig

ROLES = ("system", "user", "assistant")


class LLMError(RuntimeError):
    """The model produced no answer: server unreachable, unknown model, timeout, bad reply."""


@dataclass(frozen=True)
class Message:
    role: str  # "system", "user" or "assistant"
    content: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    output_tokens: int  # includes the thinking tokens when thinking is on
    duration_s: float
    truncated: bool  # stopped at max_output_tokens before the model finished
    thinking: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


class LLM(Protocol):
    name: str

    def complete(self, messages: Sequence[Message]) -> Completion: ...


def estimate_tokens(text: str) -> int:
    """Rough, deterministic token count (about 4 characters per token)."""
    return math.ceil(len(text) / 4)


# --------------------------------------------------------------------------- Ollama


def _http_error_text(e: urllib.error.HTTPError) -> str:
    body = e.read().decode(errors="replace")
    try:
        return str(json.loads(body)["error"])
    except (ValueError, KeyError, TypeError):
        return body.strip()[:200] or e.reason


class OllamaLLM:
    """Client for Ollama's /api/chat endpoint (non-streaming)."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.name = f"ollama:{config.model}"

    def payload(self, messages: Sequence[Message]) -> dict:
        c = self.config
        return {
            "model": c.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "think": c.think,
            "keep_alive": c.keep_alive,
            "options": {"temperature": c.temperature, "seed": c.seed, "num_predict": c.max_output_tokens},
        }

    def complete(self, messages: Sequence[Message]) -> Completion:
        if not messages:
            raise ValueError("no messages to send")
        c = self.config
        base_url = c.base_url.rstrip("/")
        request = urllib.request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(self.payload(messages)).encode(),
            headers={"Content-Type": "application/json"},
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=c.request_timeout_s) as response:
                data = json.load(response)
        except urllib.error.HTTPError as e:
            raise LLMError(f"Ollama returned HTTP {e.code}: {_http_error_text(e)}") from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise LLMError(f"no answer from Ollama within {c.request_timeout_s:g} s") from None
            raise LLMError(f"cannot reach Ollama at {base_url} ({e.reason}); "
                           "is it running? Try: systemctl status ollama") from None
        except TimeoutError:
            raise LLMError(f"no answer from Ollama within {c.request_timeout_s:g} s") from None
        except ValueError:
            raise LLMError("Ollama sent a reply that is not valid JSON") from None

        if "error" in data:
            raise LLMError(f"Ollama error: {data['error']}")
        message = data.get("message") or {}
        return Completion(
            text=message.get("content") or "",
            model=data.get("model", c.model),
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            duration_s=time.monotonic() - start,
            truncated=data.get("done_reason") == "length",
            thinking=message.get("thinking") or "",
        )


# --------------------------------------------------------------------------- fake


class FakeLLM:
    """Scripted model for tests: returns the given responses in order, one per call.

    Each call's messages are recorded in `calls`, so a test can check exactly what the
    agent sent. A call with no scripted response left raises LLMError, so an
    unexpected extra call fails loudly. A response can be a plain string (token counts
    are estimated) or a full Completion (to script truncation or exact token counts).
    """

    name = "fake"

    def __init__(self, responses: Sequence[str | Completion]):
        self.responses = list(responses)
        self.calls: list[tuple[Message, ...]] = []

    def complete(self, messages: Sequence[Message]) -> Completion:
        if not messages:
            raise ValueError("no messages to send")
        if len(self.calls) >= len(self.responses):
            raise LLMError(f"FakeLLM has no scripted response for call {len(self.calls) + 1} "
                           f"(only {len(self.responses)} scripted)")
        self.calls.append(tuple(messages))
        item = self.responses[len(self.calls) - 1]
        if isinstance(item, Completion):
            return item
        prompt = "".join(m.content for m in messages)
        return Completion(item, "fake", estimate_tokens(prompt), estimate_tokens(item), 0.0, False)


# --------------------------------------------------------------------------- parsing

_FENCED = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_HAS_MODULE = re.compile(r"\bmodule\b")
_MODULE_SPAN = re.compile(r"\bmodule\b.*\bendmodule\b", re.DOTALL)


def extract_verilog(text: str) -> str | None:
    """Pull Verilog source out of a model reply, or return None if it has no module.

    Every fenced code block that contains a module is kept and they are joined, since
    models often put helper modules in separate blocks. Without fences, the text from
    the first `module` to the last `endmodule` is used. If the reply was cut off inside
    a code block, the partial code is returned so compiling it gives a useful error.
    """
    blocks = [b.strip() for b in _FENCED.findall(text) if _HAS_MODULE.search(b)]
    if blocks:
        return "\n\n".join(blocks) + "\n"
    if text.count("```") % 2 == 1:  # odd number of fences: the last code block was cut off
        tail = text.rsplit("```", 1)[1].partition("\n")[2]
        if _HAS_MODULE.search(tail):
            return tail.strip() + "\n"
    span = _MODULE_SPAN.search(text)
    return span.group(0).strip() + "\n" if span else None
