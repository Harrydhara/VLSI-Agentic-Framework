import dataclasses
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vlsi_agent.config import load_config
from vlsi_agent.llm import (Completion, FakeLLM, LLMError, Message, OllamaLLM, estimate_tokens,
                            extract_verilog)

LLM_CONFIG = load_config().llm

OK_REPLY = {
    "model": "gemma4:26b",
    "message": {"role": "assistant", "content": "endmodule", "thinking": "It ends a module."},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 28,
    "eval_count": 3,
}


class StubOllama:
    """A tiny HTTP server that pretends to be Ollama's /api/chat."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.body: bytes = json.dumps(OK_REPLY).encode()
        self.delay_s = 0.0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                stub.requests.append({"path": self.path, "json": json.loads(self.rfile.read(length))})
                time.sleep(stub.delay_s)
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(stub.body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    server = StubOllama()
    yield server
    server.close()


def client(url: str, **overrides) -> OllamaLLM:
    return OllamaLLM(dataclasses.replace(LLM_CONFIG, base_url=url, **overrides))


MESSAGES = [Message("system", "You write Verilog."), Message("user", "Which keyword ends a module?")]


# --------------------------------------------------------------- Ollama client (stub server)


def test_request_carries_model_settings_and_messages(stub):
    client(stub.url, temperature=0.3, seed=7, think=True, max_output_tokens=123).complete(MESSAGES)
    [request] = stub.requests
    assert request["path"] == "/api/chat"
    sent = request["json"]
    assert sent["model"] == LLM_CONFIG.model and sent["stream"] is False
    assert sent["think"] is True and sent["keep_alive"] == LLM_CONFIG.keep_alive
    assert sent["options"] == {"temperature": 0.3, "seed": 7, "num_predict": 123}
    assert sent["messages"] == [{"role": "system", "content": "You write Verilog."},
                                {"role": "user", "content": "Which keyword ends a module?"}]


def test_reply_is_parsed(stub):
    result = client(stub.url).complete(MESSAGES)
    assert result.text == "endmodule"
    assert (result.prompt_tokens, result.output_tokens, result.total_tokens) == (28, 3, 31)
    assert result.thinking == "It ends a module."
    assert not result.truncated


def test_reply_cut_off_by_the_token_limit_is_marked_truncated(stub):
    stub.body = json.dumps({**OK_REPLY, "done_reason": "length"}).encode()
    assert client(stub.url).complete(MESSAGES).truncated


def test_unknown_model_error_is_reported(stub):
    stub.status = 404
    stub.body = b'{"error":"model \'nosuch:1b\' not found"}'
    with pytest.raises(LLMError, match=r"HTTP 404: model 'nosuch:1b' not found"):
        client(stub.url).complete(MESSAGES)


def test_error_inside_a_200_reply_is_reported(stub):
    stub.body = b'{"error":"out of memory"}'
    with pytest.raises(LLMError, match="Ollama error: out of memory"):
        client(stub.url).complete(MESSAGES)


def test_non_json_reply_is_reported(stub):
    stub.body = b"<html>proxy error</html>"
    with pytest.raises(LLMError, match="not valid JSON"):
        client(stub.url).complete(MESSAGES)


def test_slow_server_times_out(stub):
    stub.delay_s = 2
    with pytest.raises(LLMError, match=r"no answer from Ollama within 0.5 s"):
        client(stub.url, request_timeout_s=0.5).complete(MESSAGES)


def test_server_not_running_is_reported():
    with socket.socket() as s:  # find a port with nothing listening on it
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(LLMError, match="cannot reach Ollama"):
        client(f"http://127.0.0.1:{port}").complete(MESSAGES)


def test_empty_message_list_is_rejected():
    with pytest.raises(ValueError, match="no messages"):
        client("http://127.0.0.1:9").complete([])


def test_message_role_is_checked():
    with pytest.raises(ValueError, match="role must be one of"):
        Message("robot", "hi")


# --------------------------------------------------------------- FakeLLM


def test_fake_replays_responses_in_order_and_records_calls():
    fake = FakeLLM(["first", "second"])
    assert fake.complete([Message("user", "a")]).text == "first"
    assert fake.complete([Message("user", "b")]).text == "second"
    assert [call[-1].content for call in fake.calls] == ["a", "b"]


def test_fake_fails_loudly_on_an_unexpected_extra_call():
    fake = FakeLLM(["only one"])
    fake.complete(MESSAGES)
    with pytest.raises(LLMError, match="no scripted response for call 2"):
        fake.complete(MESSAGES)
    assert len(fake.calls) == 1


def test_fake_token_counts_are_deterministic():
    result = FakeLLM(["x" * 10]).complete([Message("user", "y" * 9)])
    assert (result.prompt_tokens, result.output_tokens) == (3, 3)
    assert estimate_tokens("") == 0


def test_fake_can_return_a_scripted_completion():
    scripted = Completion("module m(", "fake", 100, 4096, 0.0, truncated=True)
    assert FakeLLM([scripted]).complete(MESSAGES) is scripted


# --------------------------------------------------------------- extract_verilog

MODULE = "module m(input a, output y);\n  assign y = a;\nendmodule"


def test_extracts_a_fenced_block():
    assert extract_verilog(f"Here you go:\n```verilog\n{MODULE}\n```\nDone.") == MODULE + "\n"


def test_joins_module_blocks_and_skips_other_blocks():
    helper = "module h(input a, output y);\n  assign y = ~a;\nendmodule"
    reply = f"```text\nsome notes\n```\n```verilog\n{helper}\n```\nand\n```systemverilog\n{MODULE}\n```"
    assert extract_verilog(reply) == f"{helper}\n\n{MODULE}\n"


def test_unfenced_module_is_found():
    assert extract_verilog(f"Sure. {MODULE} Hope that helps.") == MODULE + "\n"


def test_cut_off_code_block_returns_the_partial_code():
    reply = "```text\nnotes\n```\n```verilog\nmodule m(input a, output y);\n  assign y ="
    assert extract_verilog(reply) == "module m(input a, output y);\n  assign y =\n"


def test_no_module_gives_none():
    assert extract_verilog("I cannot help with that. A submodule is ...") is None
    assert extract_verilog("```python\nprint('hi')\n```") is None


# --------------------------------------------------------------- real model


@pytest.mark.integration
def test_real_model_writes_a_module():
    reply = OllamaLLM(LLM_CONFIG).complete([
        Message("user", "Write a Verilog module with exactly this header, implementing y = a & b. "
                        "Reply with one ```verilog code block.\nmodule and2(input a, input b, output y);"),
    ])
    code = extract_verilog(reply.text)
    assert code is not None and "module and2" in code, reply.text
    assert reply.prompt_tokens > 0 and reply.output_tokens > 0
    assert not reply.truncated
