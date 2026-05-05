#!/usr/bin/env python3
import difflib
import getpass
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOME = Path(os.environ.get("HOME", ""))
OPENCODE_CONFIG_DIR = HOME / ".config" / "opencode"
OPENCODE_DATA_DIR = HOME / ".local" / "share" / "opencode"
OPENCODE_CONFIG = OPENCODE_CONFIG_DIR / "opencode.json"
TMP_DIR = Path("/tmp/klona-e2e-scenario1")
CAPTURE_FILE = TMP_DIR / "fake-provider-capture.jsonl"
FAKE_PROVIDER_PORT = 4545
OPENCODE_SERVE_PORT = 4546
KLONA_MEMORY_MENTAL_MODEL_FILE = Path(__file__).resolve().parent / "test_vault" / "KLONA_MEMORY_MENTAL_MODEL.md"


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


def _content_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None

    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for key in ("text", "content"):
                value = item.get(key)
                if isinstance(value, str):
                    parts.append(value)
                    break
    if not parts:
        return None
    return "".join(parts)


def _captured_user_message_contents():
    saw_chat_completion = False
    saw_user_message = False

    with CAPTURE_FILE.open(encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid fake provider capture JSONL at line {line_number}: {exc}") from exc

            if record.get("path") != "/v1/chat/completions":
                continue

            saw_chat_completion = True
            body = record.get("body")
            if not isinstance(body, str):
                raise SystemExit(f"fake provider capture line {line_number} has non-string body")
            try:
                request = json.loads(body)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid chat completion JSON body at line {line_number}: {exc}") from exc

            messages = request.get("messages")
            if not isinstance(messages, list):
                raise SystemExit(f"chat completion request at line {line_number} has no messages array")

            for message in messages:
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                content = _content_text(message.get("content"))
                if content is not None:
                    saw_user_message = True
                    yield content

    if not saw_chat_completion:
        raise SystemExit("fake provider did not capture any /v1/chat/completions requests")
    if not saw_user_message:
        raise SystemExit("fake provider chat completion capture did not include a user message")


def check_klona_memory_mental_model_injection_at_user_message(message_text: str):
    if not CAPTURE_FILE.is_file():
        raise SystemExit(f"fake provider did not capture any requests at {CAPTURE_FILE}")

    expected_klona_memory_mental_model = KLONA_MEMORY_MENTAL_MODEL_FILE.read_text(encoding="utf-8")
    expected_block = f"<Klona_memory_mental_model>\n{expected_klona_memory_mental_model}</Klona_memory_mental_model>"
    matching_messages = []
    for content in _captured_user_message_contents():
        if message_text not in content:
            continue
        matching_messages.append(content)
        if expected_block in content:
            return

    if matching_messages:
        raise SystemExit(
            f"fake provider captured {len(matching_messages)} user message(s) containing {message_text!r}, but none "
            "did not include the exact Klona memory mental model block"
        )

    raise SystemExit(
        f"fake provider chat completion capture did not include a user message containing {message_text!r}"
    )


def opencode_session_id_for_title(title: str):
    result = run(
        ["opencode", "session", "list", "--format", "json"],
        stdout=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    sessions = json.loads(result.stdout)
    for session in sessions:
        if session.get("title") == title:
            session_id = session.get("id")
            if not isinstance(session_id, str) or not session_id:
                raise SystemExit(f"OpenCode session titled {title!r} did not include a string id")
            return session_id

    raise SystemExit(f"OpenCode session list did not include title {title!r}")


def wait_for_opencode_serve(serve_process):
    deadline = time.time() + 10
    url = f"http://127.0.0.1:{OPENCODE_SERVE_PORT}"
    while True:
        if serve_process.poll() is not None:
            raise SystemExit(f"opencode serve exited before becoming reachable: {serve_process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
        except Exception:
            pass

        if time.time() > deadline:
            raise SystemExit("opencode serve did not become reachable")
        time.sleep(0.1)


def summarize_opencode_session(session_id: str):
    raw = json.dumps({"providerID": "fake", "modelID": "e2e-model"}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{OPENCODE_SERVE_PORT}" + f"/session/{session_id}/summarize",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise SystemExit(f"opencode summarize failed with HTTP {response.status}")


def check_klona_memory_mental_model_injector_state_dir():
    state_dir = OPENCODE_DATA_DIR / "plugin-state" / "klona-memory-mental-model-injector"
    if not state_dir.is_dir():
        raise SystemExit(f"Klona memory mental model injector state dir is missing: {state_dir}")


def verify_uninstall():
    agents_file = OPENCODE_CONFIG_DIR / "AGENTS.md"
    if agents_file.exists():
        content = agents_file.read_text(encoding="utf-8")
        for marker in ["<Klona_Memory>", "</Klona_Memory>"]:
            if marker in content:
                raise SystemExit(f"unexpected marker remains in {agents_file}: {marker}")

    for path in [
        OPENCODE_CONFIG_DIR / "agents" / "klona-memory.md",
        OPENCODE_CONFIG_DIR / "plugins" / "klona-memory-mental-model-injector.js",
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
    serve_process = None
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
        require_file(OPENCODE_CONFIG_DIR / "plugins" / "klona-memory-mental-model-injector.js")
        assert_file_matches(
            "klona_agent/opencode/assets/agents/klona-memory.md",
            OPENCODE_CONFIG_DIR / "agents" / "klona-memory.md",
        )
        assert_file_matches(
            "klona_agent/opencode/assets/plugins/klona-memory-mental-model-injector.js",
            OPENCODE_CONFIG_DIR / "plugins" / "klona-memory-mental-model-injector.js",
        )

        phase("Configure fake OpenAI-compatible provider")
        merge_fake_provider_config()

        phase("Reset fake provider temp capture")
        reset_fake_provider_temp()

        phase("Start fake OpenAI-compatible provider")
        server, thread = start_fake_provider()

        session_title = f"klona-e2e-scenario1-{os.getpid()}-{int(time.time())}"

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
                session_title,
                "Verify initial Klona memory mental model injection",
            ],
            timeout=90,
        )

        phase("Verify fake provider captured injected Klona memory mental model")
        check_klona_memory_mental_model_injection_at_user_message("Verify initial Klona memory mental model injection")
        check_klona_memory_mental_model_injector_state_dir()

        phase("Find OpenCode session created by first message")
        session_id = opencode_session_id_for_title(session_title)

        phase("Start OpenCode HTTP API")
        serve_process = subprocess.Popen(
            [
                "opencode",
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(OPENCODE_SERVE_PORT),
            ]
        )
        wait_for_opencode_serve(serve_process)

        phase("Trigger OpenCode session compaction")
        summarize_opencode_session(session_id)

        phase("Run real OpenCode against compacted session")
        run(
            [
                "opencode",
                "run",
                "--print-logs",
                "--log-level",
                "DEBUG",
                "--model",
                "fake/e2e-model",
                "--session",
                session_id,
                "Verify Klona memory mental model injection after compaction",
            ],
            timeout=90,
        )

        phase("Verify fake provider captured post-compaction injected Klona memory mental model")
        check_klona_memory_mental_model_injection_at_user_message("Verify Klona memory mental model injection after compaction")

        phase("Uninstall KLONA OpenCode integration")
        run(["python3", "install_agent.py", "--uninstall", "--platform", "opencode"])

        phase("Verify KLONA artifacts were removed")
        verify_uninstall()

        print("\nE2E PASS", flush=True)
    finally:
        if serve_process is not None:
            serve_process.terminate()
            try:
                serve_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                serve_process.kill()
                serve_process.wait(timeout=5)
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
