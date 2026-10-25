"""Local VELA (Ollama): the client talks Ollama's chat API, uses JSON mode for the planner and sends photos."""
import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local"))
from ollama_client import Ollama, OllamaError, wants_json  # noqa: E402


def _server(reply: str):
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"message": {"content": reply}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            body = json.dumps({"models": [{"name": "qwen2.5:14b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def test_chat_json_mode_images_and_thinking_removed():
    srv, seen = _server("<think>hmm</think>{\"type\": \"reply\", \"text\": \"hi\"}")
    try:
        o = Ollama(f"http://127.0.0.1:{srv.server_port}", num_ctx=8192)
        assert o.models() == ["qwen2.5:14b"]
        out = o.chat("qwen2.5:14b", [{"role": "system", "content": "reply with ONE JSON object"}, {"role": "user", "content": "hi"}],
                     300, json_mode=True, images=[b"jpeg"])
        assert out == '{"type": "reply", "text": "hi"}'
        req = seen[0]
        assert req["format"] == "json" and req["options"] == {"temperature": 0, "num_predict": 300, "num_ctx": 8192}
        assert req["messages"][-1]["images"] == [base64.b64encode(b"jpeg").decode()] and req["stream"] is False
    finally:
        srv.shutdown()


def test_json_mode_only_for_planner_and_sql_repair():
    assert wants_json([{"content": "... reply with ONE JSON object only ..."}, {"content": "q"}])
    assert wants_json([{"content": "system"}, {"content": 'Error: x\nReply with JSON only: {"sql": "..."}'}])
    assert not wants_json([{"content": "You are VELA AI, writing the answer"}, {"content": "Question"}])


def test_unreachable_ollama_gives_a_clear_message():
    try:
        Ollama("http://127.0.0.1:9").models()
    except OllamaError as exc:
        assert "Ollama" in str(exc)
    else:
        raise AssertionError("expected OllamaError")
