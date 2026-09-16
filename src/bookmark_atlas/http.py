from __future__ import annotations

import contextlib
import os
import time
from email.utils import parsedate_to_datetime
from urllib.request import getproxies

import httpx

from .models import AuthError, ParseError, RateLimitError, UnavailableError


def network_client(**kwargs) -> httpx.Client:
    # Honor an existing macOS system proxy as well as standard environment settings.
    proxy = os.getenv("ATLAS_PROXY") or getproxies().get("https")
    # GraphQL callers check redirects themselves; media fetches follow them.
    kwargs.setdefault("follow_redirects", False)
    return httpx.Client(timeout=30, proxy=proxy, **kwargs)


def retry_time(headers: httpx.Headers, timestamp: float) -> float:
    values = [timestamp + 60]
    if value := headers.get("x-rate-limit-reset"):
        with contextlib.suppress(ValueError):
            values.append(float(value))
    if value := headers.get("retry-after"):
        try:
            values.append(timestamp + float(value))
        except ValueError:
            with contextlib.suppress(ValueError, TypeError):
                values.append(parsedate_to_datetime(value).timestamp())
    return max(values)


def request(
    client: httpx.Client, method: str, url: str, *, sleep=time.sleep, **kwargs
) -> httpx.Response:
    for attempt in range(3):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt < 2:
                sleep(2**attempt)
                continue
            raise UnavailableError("网络连接失败；请检查代理和网络后重试。") from exc
        if response.status_code == 429:
            raise RateLimitError(retry_time(response.headers, time.time()))
        if response.status_code in (401, 403):
            raise AuthError(
                f"入口拒绝访问（HTTP {response.status_code}），请检查登录、授权或账号验证。"
            )
        if response.status_code == 402:
            raise UnavailableError("官方 API 额度或计费未就绪（HTTP 402）。")
        if response.status_code >= 500 and attempt < 2:
            sleep(2**attempt)
            continue
        if response.status_code >= 400 or 300 <= response.status_code < 400:
            raise UnavailableError(f"入口暂不可用（HTTP {response.status_code}）。")
        return response
    raise UnavailableError("请求重试失败。")


def json_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise ParseError("入口未返回 JSON，可能需要重新登录或接口已变化。") from exc
    if not isinstance(body, dict):
        raise ParseError("入口返回了无法识别的数据结构。")
    return body
