"""Read public article bodies using authorization kept inside a local WeRSS container.

The bridge reads, but never changes or exports, the container's saved credential.
It supports the current latest-article endpoint, not historical account crawling.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

from .models import AtlasError, ParseError
from .wechat import MAX_BODY, VerificationRequired, article_url

# Run in the existing WeRSS Python environment (requests and PyYAML are required).
# Request parameters arrive over stdin, never through shell interpolation.
REMOTE = r"""
import json, sys
from pathlib import Path

def main():
    import requests, yaml
    request = json.load(sys.stdin)
    saved = (yaml.safe_load(Path('/app/data/wx.lic').read_text()) or {}).get('weread_data', {})
    if isinstance(saved, str):
        saved = json.loads(saved)
    cookie = saved.get('cookie')
    if not cookie:
        return {'error': 'authorization'}
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        'Cookie': cookie, 'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://weread.qq.com/', 'Origin': 'https://weread.qq.com',
    })
    if request['op'] == 'latest':
        path, params = '/api/mp/cover', {'bookId': request['book_id']}
    else:
        path, params = '/web/mp/content', {'reviewId': request['review_id']}
    with session.get('https://weread.qq.com' + path, params=params, timeout=(10, 25),
                     allow_redirects=False, stream=True) as response:
        if response.status_code in (401, 403, 429):
            return {'error': 'authorization'}
        if response.status_code != 200:
            return {'error': 'upstream'}
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 12 * 1024 * 1024:
                return {'error': 'oversized'}
        raw = data.decode('utf-8', errors='replace')
    if request['op'] == 'latest' or raw.lstrip().startswith('{'):
        value = json.loads(raw)
        code = value.get('errcode', value.get('errorCode', value.get('errCode', 0)))
        if code:
            return {'error': 'authorization' if str(code) in ('-2012', '-2010') else 'upstream'}
        if request['op'] == 'latest':
            return {'review_id': value.get('reviewId', '')}
        return {'error': 'body_missing'}
    return {'html': raw}

try:
    result = main()
except FileNotFoundError:
    result = {'error': 'authorization'}
except Exception:
    # Neither exception text nor response headers may leave the authorized process.
    result = {'error': 'bridge_failed'}
print(json.dumps(result, ensure_ascii=False))
"""


def validate_container(container):
    if not isinstance(container, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", container
    ):
        raise AtlasError("WeRSS 容器名称无效。")


def book_id(biz):
    try:
        value = base64.b64decode(biz, validate=True).decode("ascii")
    except (ValueError, UnicodeError) as exc:
        raise ParseError("微信读书需要有效的公众号 __biz。") from exc
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise ParseError("微信读书需要有效的公众号 __biz。")
    return "MP_WXS_" + value


class WeReadBridge:
    def __init__(self, container):
        validate_container(container)
        self.container = container

    def _call(self, request):
        # Fixed shell expression selects the container architecture. User input is
        # passed as separate arguments/stdin. stderr is discarded, never logged.
        command = [
            "docker",
            "exec",
            "-i",
            self.container,
            "sh",
            "-c",
            'exec "/app/env_$(uname -m)/bin/python" -c "$1"',
            "atlas-weread",
            REMOTE,
        ]
        try:
            with tempfile.TemporaryFile() as output:
                completed = subprocess.run(
                    command,
                    input=json.dumps(request).encode(),
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=75,
                    check=False,
                )
                if completed.returncode:
                    raise AtlasError(
                        "微信读书桥接不可用，请检查 Docker、WeRSS 容器及其 Python 环境。"
                    )
                if output.tell() > MAX_BODY * 2:
                    raise ParseError("微信读书响应过大，未收录。")
                output.seek(0)
                value = json.load(output)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise AtlasError("微信读书桥接失败或超时；本轮保留待重试。") from exc
        if not isinstance(value, dict):
            raise ParseError("微信读书桥接响应结构无效。")
        if value.get("error") == "authorization":
            raise VerificationRequired(
                "微信读书授权失效或请求受限；请在 WeRSS 页面重新授权后重试。"
            )
        if value.get("error"):
            raise AtlasError("微信读书未返回有效文章，保留待重试。")
        return value

    def latest(self, biz):
        prefix = book_id(biz) + "_"
        value = self._call({"op": "latest", "book_id": book_id(biz)}).get("review_id")
        if not isinstance(value, str) or not value.startswith(prefix):
            raise ParseError("微信读书未返回该公众号的最新文章。")
        token = value[len(prefix) :]
        if not re.fullmatch(r"[A-Za-z0-9_~-]{1,200}", token):
            raise ParseError("微信读书文章标识无效。")
        return article_url("https://mp.weixin.qq.com/s/" + token)

    def content(self, biz, url):
        path = urlsplit(article_url(url)).path
        if not path.startswith("/s/"):
            raise ParseError("微信读书桥接需要文章短链接；长链接请使用 wechat import。")
        value = self._call({"op": "content", "review_id": book_id(biz) + "_" + path[3:]})
        source = value.get("html")
        if not isinstance(source, str) or len(source.encode()) > MAX_BODY:
            raise ParseError("微信读书未返回有效正文。")
        return source
