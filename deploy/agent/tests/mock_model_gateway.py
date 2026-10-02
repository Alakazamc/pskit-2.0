"""Deterministic OpenAI-compatible SSE model substitute for local Docker tests."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def response_kind(payload: dict) -> str:
    """Choose a response from the conversation's observable text."""
    conversation = json.dumps(payload.get("messages", []), ensure_ascii=False).lower()
    if "background task result" in conversation or "pskit.task_completed" in conversation:
        return "resumed"
    if "af3" in conversation or "alphafold" in conversation:
        return "af3"
    return "normal"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 2 * 1024 * 1024:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return
        kind = response_kind(payload)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def event(delta: dict, finish_reason=None) -> None:
            body = {"id": "chatcmpl-local", "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": payload.get("model", "local-stub"),
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
            self.wfile.write(b"data: " + json.dumps(body).encode() + b"\n\n")
            self.wfile.flush()

        event({"role": "assistant"})
        if kind == "af3":
            event({"tool_calls": [{"index": 0, "id": "call_local_af3", "type": "function",
                                   "function": {"name": "submit_af3",
                                                "arguments": '{"estimated_gpu_minutes":1}'}}]})
            event({}, "tool_calls")
        else:
            answer = ("AF3 mock result reviewed and ready." if kind == "resumed"
                      else "Local model response ready.")
            event({"content": answer})
            event({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
