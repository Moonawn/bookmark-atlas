from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from ..config import read_credentials, save_credentials
from ..http import json_body, network_client, request
from ..models import AuthError, Identity, Item, Page, ParseError

API = "https://api.x.com/2"


def parse_page(body: dict) -> Page:
    if body.get("errors"):
        raise ParseError("官方 API 返回部分错误；本页未提交，以免将不完整结果误认为同步成功。")
    if "data" not in body and "meta" not in body:
        raise ParseError("官方 API 收藏响应缺少 data/meta。")
    users = {u["id"]: u for u in body.get("includes", {}).get("users", [])}
    media = {m["media_key"]: m for m in body.get("includes", {}).get("media", [])}
    result = []
    for post in body.get("data", []):
        if not post.get("id") or "text" not in post:
            raise ParseError("官方 API 推文缺少必要字段。")
        user = users.get(post.get("author_id"), {})
        note = post.get("note_tweet") or {}
        text = note.get("text") or post["text"]
        entities = note.get("entities") or post.get("entities", {})
        result.append(
            Item(
                site="x",
                item_id=post["id"],
                text=text,
                url=f"https://x.com/i/status/{post['id']}",
                author_id=post.get("author_id", ""),
                author=user.get("username", ""),
                published_at=post.get("created_at", ""),
                links=[
                    u.get("expanded_url") or u["url"]
                    for u in entities.get("urls", [])
                    if u.get("expanded_url") or u.get("url")
                ],
                media=[
                    media[k]
                    for k in post.get("attachments", {}).get("media_keys", [])
                    if k in media
                ],
            )
        )
    return Page(result, body.get("meta", {}).get("next_token"), body)


class XAPI:
    name = "api"

    def __init__(self, home: Path, client: httpx.Client | None = None):
        self.home = home
        self.client = client or network_client()
        credentials = read_credentials(home)
        self.token = os.getenv("ATLAS_X_ACCESS_TOKEN") or credentials.get("access_token")
        if not self.token:
            raise AuthError("未配置官方 API，请运行 auth login 或设置 ATLAS_X_ACCESS_TOKEN。")
        if (
            not os.getenv("ATLAS_X_ACCESS_TOKEN")
            and credentials.get("expires_at", float("inf")) <= time.time() + 30
        ):
            self._refresh(credentials)
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        self._identity = None

    def _refresh(self, credentials: dict):
        if not credentials.get("refresh_token") or not credentials.get("client_id"):
            raise AuthError("OAuth 已过期，请重新运行 auth login。")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": credentials["refresh_token"],
            "client_id": credentials["client_id"],
        }
        secret = os.getenv("ATLAS_X_CLIENT_SECRET")
        auth = (credentials["client_id"], secret) if secret else None
        body = json_body(request(self.client, "POST", f"{API}/oauth2/token", data=data, auth=auth))
        if not body.get("access_token"):
            raise AuthError("OAuth 刷新失败，请重新登录。")
        self.token = body["access_token"]
        save_credentials(
            self.home,
            {
                "access_token": self.token,
                "refresh_token": body.get("refresh_token", credentials["refresh_token"]),
                "expires_at": time.time() + body.get("expires_in", 7200),
            },
        )

    def identity(self) -> Identity:
        if not self._identity:
            body = json_body(request(self.client, "GET", f"{API}/users/me"))
            user = body.get("data", {})
            if not user.get("id"):
                raise AuthError("官方 API 无法确认当前账号。")
            self._identity = Identity("x", user["id"], user.get("username", ""))
        return self._identity

    def page(self, cursor=None):
        identity = self.identity()
        params = {
            "max_results": "100",
            "tweet.fields": "created_at,author_id,entities,attachments,note_tweet",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "username",
            "media.fields": "type,url,preview_image_url,variants",
        }
        if cursor:
            params["pagination_token"] = cursor
        body = json_body(
            request(
                self.client, "GET", f"{API}/users/{identity.account_id}/bookmarks", params=params
            )
        )
        return parse_page(body)

    def close(self):
        self.client.close()
