"""A stand-in Ollama server for UI tests of slow model responses and the Stop button.

This is a MOCK. It answers /api/tags and /api/chat after a delay with a claim
that copies the first retrieved passage verbatim (so it passes citation
validation). It exists only so the browser tests can exercise loading states,
streaming stages and Stop against a model that takes seconds to reply.

    python tests/ui/slow_ollama.py 8799 6     # port, delay in seconds
"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"models": [{"name": "qwen2.5:7b-instruct"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        time.sleep(DELAY)
        prompt = body["messages"][1]["content"]
        question = set(re.findall(r"[a-z]{4,}", prompt.rsplit("Question:", 1)[-1].lower()))
        sources = prompt.split("Sources:", 1)[-1].rsplit("Question:", 1)[0]   # skip the prompt's example
        passages = re.findall(r"\[(S\d+)\][^\n]*\n([^\n]+(?:\n[^\n\[]+)*)", sources)
        # copy the passage whose last line shares most words with the question
        best = max(passages, key=lambda p: len(question & set(re.findall(r"[a-z]{4,}", p[1].splitlines()[-1].lower()))),
                   default=None)
        claims = [{"text": best[1].splitlines()[-1].strip(), "sources": [best[0]], "kind": "direct"}] if best else []
        content = json.dumps({"status": "answered" if claims else "insufficient_evidence", "claims": claims})
        try:
            self._send({"message": {"role": "assistant", "content": content}})
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8799), Handler).serve_forever()
