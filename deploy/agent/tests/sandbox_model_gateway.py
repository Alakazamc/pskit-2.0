"""Isolated smoke substitute at the private Run-scoped model gateway path."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path != "/internal/model/v1/chat/completions":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "sandbox_wait_probe" in json.dumps(body):
            time.sleep(3)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, reason in [
            ({"role": "assistant", "content": "Sandbox local model ready."}, None),
            ({}, "stop"),
        ]:
            data = {
                "id": "smoke",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "sandbox-stub",
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
            }
            self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
