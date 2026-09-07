from __future__ import annotations

import base64
import hashlib
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from .adapters.x_api import API
from .config import save_credentials
from .http import json_body, network_client, request
from .models import AuthError


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def login(
    home: Path,
    client_id: str,
    port: int = 8765,
    client_secret: str | None = None,
    timeout: int = 300,
):
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(32)
    redirect = f"http://127.0.0.1:{port}/callback"
    outcome = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            valid = urlparse(self.path).path == "/callback" and secrets.compare_digest(
                query.get("state", [""])[0], state
            )
            if not valid:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid OAuth callback.")
                return
            outcome.update({"code": query.get("code", [""])[0], "error": bool(query.get("error"))})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization received. Return to Bookmark Atlas.")

        def log_message(self, *_args):
            pass  # Callback URLs contain one-time authorization codes.

    server = HTTPServer(("127.0.0.1", port), Callback)
    server.timeout = 1
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "scope": "tweet.read users.read bookmark.read follows.read offline.access",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    print(f"在 X Developer App 配置回调地址：{redirect}")
    print(
        f"请在浏览器打开以下地址授权（{timeout} 秒内）：\nhttps://x.com/i/oauth2/authorize?{query}",
        flush=True,
    )
    deadline = time.monotonic() + timeout
    try:
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not outcome.get("code") or outcome.get("error"):
        raise AuthError("OAuth 授权未完成、被拒绝或等待超时。")
    with network_client() as client:
        body = json_body(
            request(
                client,
                "POST",
                f"{API}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": outcome["code"],
                    "redirect_uri": redirect,
                    "code_verifier": verifier,
                },
                auth=(client_id, client_secret) if client_secret else None,
            )
        )
    if not body.get("access_token"):
        raise AuthError("OAuth 未返回访问令牌。")
    save_credentials(
        home,
        {
            "client_id": client_id,
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_at": time.time() + body.get("expires_in", 7200),
        },
    )
    print("OAuth 授权已保存到本地私有目录。")
