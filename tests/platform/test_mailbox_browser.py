"""독립 Chrome 프로필과 자체 HTTPS에서 사서함 callback의 Lax 쿠키 흐름만 확인한다."""

import os
import shutil
import ssl
import subprocess
import threading
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs
from uuid import UUID

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.platform.auth.mailbox_settings import MAILBOX_CALLBACK, MailboxAuthSettings
from backend.api.platform.auth.settings import CALLBACK_PATH, MicrosoftAuthSettings
from backend.api.platform.main import create_app
from backend.shared.settings import Settings

CHROME = shutil.which("google-chrome")


@pytest.mark.skipif(
    CHROME is None, reason="실제 브라우저 회귀는 로컬 Chrome 실행 파일이 필요합니다."
)
def test_mailbox_relay_restores_lax_cookies_and_same_origin_with_no_referrer():
    """교차 사이트 POST 뒤 실제 relay의 fetch가 Lax 쿠키·Origin을 보존하는지 확인한다.

    일반 form 제출은 no-referrer에서 Origin:null이 되어야 거부되므로, 보호를 완화하지
    않은 같은 출처 fetch와 고정 루트 이동을 실제 Chrome으로 회귀한다.

    Args:
        없음. 기존 사용자 프로필·공용 서비스·실제 Microsoft는 사용하지 않는다.

    Returns:
        None: 자체 HTTPS 두 사이트와 일회 Chrome 프로필을 정리하고 관찰 결과를 검증한다.
    """
    stages = []

    class LocalHandler(BaseHTTPRequestHandler):
        """자체 합성 공급자와 제품 HTTPS 경로만 처리한다."""

        def log_message(self, *_):
            """합성 인증 본문·쿠키를 로그에 쓰지 않는다.

            Args:
                *_: 기본 HTTP 로그 인자.

            Returns:
                None: 접근 로그를 생략한다.
            """
            return

        def html(self, value):
            """고정 검사 HTML을 전달한다.

            Args:
                value: 합성 브라우저 검사 문서.

            Returns:
                None: HTTP 응답을 마친다.
            """
            data = value.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            """제품 쿠키 설정과 별도 사이트의 합성 공급자 제출을 실행한다.

            Args:
                없음.

            Returns:
                None: 브라우저를 두 사이트 사이에서 이동시킨다.
            """
            if self.path == "/start":
                self.send_response(302)
                self.send_header(
                    "Set-Cookie",
                    "__Host-team4_session=synthetic-session; Secure; HttpOnly; SameSite=Lax; Path=/",
                )
                self.send_header(
                    "Set-Cookie",
                    "__Host-team4_mailbox_oauth=synthetic-binding; Secure; HttpOnly; SameSite=Lax; Path=/",
                )
                self.send_header(
                    "Location", f"https://127.0.0.1:{self.server.server_port}/provider"
                )
                self.end_headers()
            elif self.path == "/provider":
                self.html(
                    f'<form method="post" action="{origin}{MAILBOX_CALLBACK}"><input name="state" value="synthetic-state"><input name="code" value="synthetic-code"></form><script>document.forms[0].submit()</script>'
                )
            elif self.path == "/":
                good = (
                    bool(stages)
                    and stages[-1]["session_present"]
                    and stages[-1]["binding_present"]
                    and stages[-1]["origin_matches"]
                )
                self.html('<h1 id="result">' + ("PASS" if good else "FAIL") + "</h1>")
            else:
                self.html("<h1>unexpected</h1>")

        def do_POST(self):
            """실제 relay HTML을 전달하고 다음 같은 출처 요청의 쿠키·Origin을 관찰한다.

            Args:
                없음.

            Returns:
                None: 원문 쿠키 대신 존재 여부만 기록한다.
            """
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            cookies = self.headers.get("Cookie", "")
            stage = {
                "path": self.path,
                "session_present": "__Host-team4_session=" in cookies,
                "binding_present": "__Host-team4_mailbox_oauth=" in cookies,
                "origin_matches": self.headers.get("Origin") == origin,
            }
            stages.append(stage)
            if self.path == MAILBOX_CALLBACK:
                response = client.post(
                    self.path,
                    content=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                self.send_response(response.status_code)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(response.content)
            elif self.path == "/api/v1/platform/auth/mailbox/complete":
                good = (
                    stage["session_present"]
                    and stage["binding_present"]
                    and stage["origin_matches"]
                    and parse_qs(body.decode())
                    == {"state": ["synthetic-state"], "code": ["synthetic-code"]}
                )
                self.send_response(303 if good else 403)
                self.send_header("Location", origin + "/")
                self.end_headers()
            else:
                self.html("<h1>unexpected</h1>")

    with TemporaryDirectory(prefix="KAN81-browser-", dir="/tmp") as tmp:
        root = Path(tmp)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
            .sign(key, hashes.SHA256())
        )
        (root / "key.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        os.chmod(root / "key.pem", 0o600)
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "cert.pem", root / "key.pem")
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        origin = f"https://localhost:{server.server_port}"
        auth = MicrosoftAuthSettings(
            _env_file=None,
            enabled=True,
            client_id=UUID(int=1),
            client_secret=SecretStr("synthetic-secret"),
            app_origin=origin,
            redirect_uri=origin + CALLBACK_PATH,
        )
        mailbox = MailboxAuthSettings(
            _env_file=None,
            enabled=True,
            encryption_key=SecretStr(b64encode(token_bytes(32)).decode()),
            key_version="browser-test-v1",
            redirect_uri=origin + MAILBOX_CALLBACK,
        )
        app = create_app(
            Settings(_env_file=None, environment="test"), auth, mailbox_settings=mailbox
        )
        with TestClient(app) as client:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run(
                    [
                        CHROME,
                        "--headless=new",
                        "--no-sandbox",
                        "--no-proxy-server",
                        "--disable-background-networking",
                        "--disable-sync",
                        "--disable-component-update",
                        "--no-first-run",
                        "--ignore-certificate-errors",
                        "--user-data-dir=" + str(root / "profile"),
                        "--dump-dom",
                        "--virtual-time-budget=5000",
                        origin + "/start",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=40,
                )
                assert result.returncode == 0, "자체 Chrome 실행 실패"
                assert len(stages) == 2, {
                    "observed_stages": stages,
                    "final_pass": 'id="result">PASS' in result.stdout,
                }
                assert not stages[0]["session_present"] and not stages[0]["binding_present"], stages
                assert (
                    stages[1]["session_present"]
                    and stages[1]["binding_present"]
                    and stages[1]["origin_matches"]
                ), stages
                assert 'id="result">PASS' in result.stdout
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
