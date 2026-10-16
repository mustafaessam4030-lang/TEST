"""
python -m intelligence.autoconfig --bench, against a stand-in Ollama: only
pulled models are tried, each reply is checked (JSON, fact guard), a model
that adds a fact is not recommended, and gpt-oss gets a reasoning level.
No real model, no network.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="bench_")
from intelligence import autoconfig as AC                    # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


CHATS = []


class Ollama(BaseHTTPRequestHandler):
    models = ["qwen3.5:4b", "qwen3:30b", "gpt-oss:20b"]

    def log_message(self, *a):
        pass

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"models": [{"name": m} for m in self.models]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/api/generate"):
            return self._send({})
        CHATS.append(body)
        system = body["messages"][0]["content"]
        if system.startswith("You route"):
            reply = {"chat": False, "question_en": "Why did 8842001173 fail?", "needs_web": False}
        elif body["model"] == "qwen3:30b":
            # This one invents a number the records do not hold.
            reply = {"answer": "8842001173 failed 3 times at 14:05.", "verified": []}
        else:
            reply = {"answer": "8842001173 stopped at the Hub write: the Save/Update button "
                               "was not found. Nothing was written.", "verified": []}
        self._send({"message": {"content": json.dumps(reply)}})


server = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["ATLAS_LLM_URL"] = "http://127.0.0.1:{0}".format(server.server_address[1])
os.environ["ATLAS_LLM_THREADS"] = "2"
AC._machine = lambda: (4, 34.3, 20.0, 100.0)

out = []
code = AC.bench(log=out.append)
text = "\n".join(out)
print(text)
check("The benchmark ran on the pulled models", code == 0 and "qwen3.5:4b" in text
      and "gpt-oss:20b" in text, text[-300:])
check("A model that adds a fact the records lack is marked FAIL by the fact guard",
      "guard:" in text, text)
check("...and is not the one recommended",
      "Recommended here: gpt-oss:20b" in text, [x for x in out if "Recommended" in x])
check("gpt-oss is asked with the lowest reasoning level, the others with thinking off",
      all(c["think"] == "low" for c in CHATS if c["model"].startswith("gpt-oss")) and
      all(c["think"] is False for c in CHATS if not c["model"].startswith("gpt-oss")))
Ollama.models = ["llama3.1:8b"]
out = []
check("Nothing to compare pulled: says so, returns an error", AC.bench(log=out.append) == 1
      and "is pulled" in "\n".join(out), out)
os.environ["ATLAS_LLM_MODEL"] = "gpt-oss:20b"
check("gpt-oss named by the operator: pictures still go to the 4B",
      AC.choose_model(os.environ["ATLAS_LLM_URL"], {"gpt-oss:20b", "qwen3.5:4b"}, 34)
      == ("gpt-oss:20b", "qwen3.5:4b"))
os.environ.pop("ATLAS_LLM_MODEL")

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
