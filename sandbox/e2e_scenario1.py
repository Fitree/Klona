#!/usr/bin/env python3
import difflib
import getpass
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOME = Path(os.environ.get("HOME", ""))
OPENCODE_CONFIG_DIR = HOME / ".config" / "opencode"
OPENCODE_CONFIG = OPENCODE_CONFIG_DIR / "opencode.json"
TMP_DIR = Path("/tmp/klona-e2e-scenario1")
CAPTURE_FILE = TMP_DIR / "fake-provider-capture.jsonl"
FAKE_PROVIDER_PORT = 4545


def phase(name):
    print(f"\n==> {name}", flush=True)


def run(args, **kwargs):
    print("+ " + " ".join(str(arg) for arg in args), flush=True)
    return subprocess.run(args, check=True, **kwargs)


def require_file(path):
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"Expected file is missing: {path}")


def assert_file_matches(expected, actual):
    expected = Path(expected)
    actual = Path(actual)
    expected_bytes = expected.read_bytes()
    actual_bytes = actual.read_bytes()
    if expected_bytes == actual_bytes:
        return

    expected_text = expected_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
    actual_text = actual_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            expected_text,
            actual_text,
            fromfile=str(expected),
            tofile=str(actual),
        )
    )
    raise SystemExit(f"Installed file does not match repository asset:\n{diff}")


def merge_fake_provider_config():
    config = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
    mcp = config.get("mcp")
    if not isinstance(mcp, dict) or "klona_memory_server" not in mcp:
        raise SystemExit("installer did not preserve mcp.klona_memory_server before provider merge")

    provider = config.setdefault("provider", {})
    provider["fake"] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "KLONA E2E fake provider",
        "options": {
            "baseURL": f"http://127.0.0.1:{FAKE_PROVIDER_PORT}/v1",
            "apiKey": "klona-e2e-fake-key",
        },
        "models": {
            "e2e-model": {
                "name": "KLONA E2E model",
                "limit": {"context": 128000, "output": 4096},
            }
        },
    }
    config["model"] = "fake/e2e-model"
    config["small_model"] = "fake/e2e-model"
    config["share"] = "disabled"
    config["autoupdate"] = False
    config["snapshot"] = False

    OPENCODE_CONFIG.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def start_fake_provider():
    CAPTURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    capture_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            print("fake-provider: " + fmt % args, flush=True)

        def write_json(self, status, payload):
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self.write_json(200, {"ok": True})
                return
            if self.path == "/v1/models":
                self.write_json(200, {"object": "list", "data": [{"id": "e2e-model", "object": "model"}]})
                return
            self.write_json(404, {"error": f"unsupported path: {self.path}"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            with capture_lock:
                with CAPTURE_FILE.open("ab") as fh:
                    fh.write(
                        json.dumps(
                            {"path": self.path, "body": body.decode("utf-8", errors="replace")}
                        ).encode("utf-8")
                    )
                    fh.write(b"\n")

            if self.path != "/v1/chat/completions":
                self.write_json(404, {"error": f"unsupported path: {self.path}"})
                return

            try:
                request = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.write_json(400, {"error": "invalid JSON"})
                return

            created = int(time.time())
            model = request.get("model", "e2e-model")
            if request.get("stream"):
                chunks = [
                    {
                        "id": "chatcmpl-klona-e2e",
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "KLONA E2E fake response"},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-klona-e2e",
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    },
                ]
                raw = b"".join(
                    f"data: {json.dumps(chunk)}\n\n".encode("utf-8") for chunk in chunks
                ) + b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            self.write_json(
                200,
                {
                    "id": "chatcmpl-klona-e2e",
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "KLONA E2E fake response"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

    server = ThreadingHTTPServer(("127.0.0.1", FAKE_PROVIDER_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, name="fake-provider", daemon=True)
    thread.start()

    deadline = time.time() + 10
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PROVIDER_PORT}/health", timeout=1) as response:
                if response.status == 200:
                    break
        except Exception:
            if time.time() > deadline:
                server.shutdown()
                server.server_close()
                raise SystemExit("fake provider did not become healthy")
            time.sleep(0.1)

    return server, thread


def verify_capture():
    if not CAPTURE_FILE.is_file():
        raise SystemExit(f"fake provider did not capture any requests at {CAPTURE_FILE}")

    text = CAPTURE_FILE.read_text(encoding="utf-8")
    for needle in [
        "/v1/chat/completions",
        "<Mental_model>",
        "# Dummy E2E Mental Model",
        "Unique marker: KLONA_E2E_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62",
        "</Mental_model>",
        "Hello from scenario 1",
    ]:
        if needle not in text:
            raise SystemExit(f"missing expected fake provider capture content: {needle}")


def verify_uninstall():
    agents_file = OPENCODE_CONFIG_DIR / "AGENTS.md"
    if agents_file.exists():
        content = agents_file.read_text(encoding="utf-8")
        for marker in ["<!-- KLONA:BEGIN -->", "<!-- KLONA:END -->"]:
            if marker in content:
                raise SystemExit(f"unexpected marker remains in {agents_file}: {marker}")

    for path in [
        OPENCODE_CONFIG_DIR / "agents" / "klona-memory.md",
        OPENCODE_CONFIG_DIR / "plugins" / "klona-memory-session.js",
    ]:
        if path.exists():
            raise SystemExit(f"KLONA artifact remains after uninstall: {path}")

    if OPENCODE_CONFIG.exists():
        config = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
        mcp = config.get("mcp")
        if isinstance(mcp, dict) and "klona_memory_server" in mcp:
            raise SystemExit("mcp.klona_memory_server remains after uninstall")


def reset_fake_provider_temp():
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def main():
    server = None
    thread = None
    try:
        phase("Verify sandbox user")
        if getpass.getuser() != "test_user":
            raise SystemExit(f"Expected to run as test_user, got {getpass.getuser()}")
        if str(HOME) != "/home/test_user":
            raise SystemExit(f"Expected HOME=/home/test_user, got {HOME}")

        phase("Install KLONA OpenCode integration")
        run(["python3", "install_agent.py", "--platform", "opencode", "--klona-memory-server-url", os.environ["KLONA_E2E_MCP_URL"], "--klona-memory-server-token", os.environ["KLONA_E2E_TOKEN"]])

        phase("Verify installed files")
        require_file(OPENCODE_CONFIG_DIR / "AGENTS.md")
        require_file(OPENCODE_CONFIG)
        require_file(OPENCODE_CONFIG_DIR / "agents" / "klona-memory.md")
        require_file(OPENCODE_CONFIG_DIR / "plugins" / "klona-memory-session.js")
        assert_file_matches(
            "klona_agent/opencode/assets/agents/klona-memory.md",
            OPENCODE_CONFIG_DIR / "agents" / "klona-memory.md",
        )
        assert_file_matches(
            "klona_agent/opencode/assets/plugins/klona-memory-session.js",
            OPENCODE_CONFIG_DIR / "plugins" / "klona-memory-session.js",
        )

        phase("Configure fake OpenAI-compatible provider")
        merge_fake_provider_config()

        phase("Reset fake provider temp capture")
        reset_fake_provider_temp()

        phase("Start fake OpenAI-compatible provider")
        server, thread = start_fake_provider()

        phase("Run real OpenCode against fake provider")
        run(
            [
                "opencode",
                "run",
                "--print-logs",
                "--log-level",
                "DEBUG",
                "--model",
                "fake/e2e-model",
                "--title",
                "klona-e2e-scenario1",
                "Hello from scenario 1",
            ],
            timeout=90,
        )

        phase("Verify fake provider captured injected mental model")
        verify_capture()

        phase("Uninstall KLONA OpenCode integration")
        run(["python3", "install_agent.py", "--uninstall", "--platform", "opencode"])

        phase("Verify KLONA artifacts were removed")
        verify_uninstall()

        print("\nE2E PASS", flush=True)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
