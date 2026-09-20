import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import struct
import subprocess
import sys
import threading

import pytest


TOKEN = "nk_synthetic_proxy_secret"
PROTOCOL = "nodus-workspace-ssh-v1"
PAYLOAD = b"SSH-2.0-local\r\n\x00\xff\x80\x01"
ANSWER = b"SSH-2.0-remote\r\n\x00\xfe\x80\x02"


def exact(stream, count):
    output = b""
    while len(output) < count:
        data = stream.read(count - len(output))
        if not data:
            raise EOFError("binary stream ended")
        output += data
    return output


@pytest.fixture
def gateway():
    requests, incoming, failures = [], [], []
    configuration = {"redirect": "", "text": False, "wait": False}
    connected = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_):
            pass
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            if configuration["redirect"]:
                self.send_response(302)
                self.send_header("Location", configuration["redirect"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.send_header("Sec-WebSocket-Protocol", PROTOCOL)
            self.end_headers()
            self.connection.settimeout(4)
            connected.set()
            try:
                if configuration["wait"]:
                    while self.rfile.read(1):
                        pass
                    return
                first, length = exact(self.rfile, 2)
                assert first == 0x82
                assert length & 0x80
                length &= 0x7f
                if length == 126:
                    length = struct.unpack("!H", exact(self.rfile, 2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", exact(self.rfile, 8))[0]
                mask = exact(self.rfile, 4)
                data = exact(self.rfile, length)
                incoming.append(bytes(value ^ mask[i % 4] for i, value in enumerate(data)))
                self.wfile.write(bytes([0x81 if configuration["text"] else 0x82, len(ANSWER)]) + ANSWER)
                self.wfile.write(b"\x88\x02\x03\xe8")
                self.wfile.flush()
            except Exception as error:
                failures.append(error)
            finally:
                self.close_connection = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, incoming, failures, configuration, connected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def process(url, extra=()):
    environment = {**os.environ, "NODUS_API_KEY": TOKEN, "NODUS_BASE_URL": url, "NO_PROXY": "127.0.0.1,localhost"}
    return subprocess.Popen([sys.executable, "-m", "nodus.cli", "workspaces", "ssh-proxy", "ws_lab", "--session", "sb_original", "--generation", "7", *extra], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)


def stop(child):
    if child.poll() is None:
        child.kill()
    child.communicate(timeout=3)


def test_ssh_proxy_relays_binary_bytes_with_current_auth_and_exact_generation(gateway):
    url, requests, incoming, failures, _, connected = gateway
    child = process(url)
    try:
        child.stdin.write(PAYLOAD)
        child.stdin.flush()
        child.wait(timeout=5)
        assert child.returncode == 0, child.stderr.read().decode()
        assert child.stdout.read() == ANSWER
        assert incoming == [PAYLOAD]
        assert not failures
        path, headers = requests[0]
        assert path == "/v1/research-workspaces/ws_lab/ssh-stream"
        assert headers["Authorization"] == "Bearer " + TOKEN
        assert headers["X-Nodus-Workspace-Session"] == "sb_original"
        assert headers["X-Nodus-Workspace-Generation"] == "7"
        assert headers["Sec-WebSocket-Protocol"] == PROTOCOL
        assert "Origin" not in headers and "Cookie" not in headers
        assert len(requests) == 1
    finally:
        stop(child)


def test_ssh_proxy_refuses_redirect_without_sending_auth_to_another_origin(gateway):
    url, requests, _, _, configuration, _ = gateway
    received = []
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(403)
            self.end_headers()
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    worker = threading.Thread(target=receiver.serve_forever, daemon=True)
    worker.start()
    configuration["redirect"] = f"http://127.0.0.1:{receiver.server_port}/stolen"
    child = process(url, ("--debug",))
    try:
        child.wait(timeout=5)
        output, error = child.communicate(timeout=2)
        assert child.returncode == 2
        assert output == b""
        assert len(requests) == 1
        assert received == []
        assert TOKEN.encode() not in error
        assert b"stolen" not in error
        assert b"SSH" in error
    finally:
        stop(child)
        receiver.shutdown()
        receiver.server_close()
        worker.join(timeout=2)


def test_ssh_proxy_text_frame_never_reaches_ssh_stdout(gateway):
    url, _, _, _, configuration, _ = gateway
    configuration["text"] = True
    child = process(url, ("--debug",))
    try:
        child.stdin.write(PAYLOAD)
        child.stdin.flush()
        child.wait(timeout=5)
        assert child.returncode == 2
        assert child.stdout.read() == b""
        assert TOKEN.encode() not in child.stderr.read()
    finally:
        stop(child)


def test_ssh_proxy_input_eof_closes_the_tunnel_promptly(gateway):
    url, _, _, _, configuration, connected = gateway
    configuration["wait"] = True
    child = process(url)
    try:
        assert connected.wait(3)
        child.stdin.close()
        child.stdin = None
        child.wait(timeout=3)
        assert child.returncode == 0
        assert child.stdout.read() == b""
    finally:
        stop(child)


@pytest.mark.parametrize("args", [("--generation", "0"), ("--session", "../secret"), ("--generation", "nope")])
def test_ssh_proxy_invalid_generation_or_session_never_connects(gateway, args):
    url, requests, _, _, _, _ = gateway
    child = process(url, args)
    try:
        output, error = child.communicate(timeout=5)
        assert child.returncode == 2
        assert requests == []
        assert output == b""
        assert TOKEN.encode() not in error
        assert b"invalid choice" not in error
    finally:
        stop(child)
