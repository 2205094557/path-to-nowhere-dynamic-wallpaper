from __future__ import annotations

import email.utils
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .io_utils import atomic_write_bytes

ALLOWED_HOSTS = frozenset({"wiki.biligame.com", "patchwiki.biligame.com"})
ALLOWED_ACTIONS = frozenset({"parse", "query"})
API_URL = "https://wiki.biligame.com/wqmt/api.php"


class PolicyError(ValueError):
    pass


class ResponseTooLarge(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def validate_url(url: str, allowed_hosts: frozenset[str] = ALLOWED_HOSTS) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https":
        raise PolicyError(f"only HTTPS is allowed: {url}")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PolicyError(f"credentials and non-default ports are forbidden: {url}")
    if host not in allowed_hosts:
        raise PolicyError(f"host is not allowed: {host or '<missing>'}")
    return url


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            date = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (date - current).total_seconds())


def read_limited(stream, limit: int) -> bytes:  # type: ignore[no-untyped-def]
    chunks: list[bytes] = []
    total = 0
    while True:
        block = stream.read(min(64 * 1024, limit - total + 1))
        if not block:
            return b"".join(chunks)
        total += len(block)
        if total > limit:
            raise ResponseTooLarge(f"response exceeds {limit} bytes")
        chunks.append(block)


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status: int
    content_type: str
    data: bytes
    fetched_at: str
    attempts: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class WikiClient:
    def __init__(
        self,
        responses_dir: Path,
        *,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        retries: int = 2,
        max_retry_after: float = 60.0,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) wqmt-bwiki-poc/0.1 read-only",
        opener=None,  # type: ignore[no-untyped-def]
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.responses_dir = Path(responses_dir)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.retries = retries
        self.max_retry_after = max_retry_after
        self.user_agent = user_agent
        self.opener = opener or urllib.request.build_opener(NoRedirectHandler())
        self.sleep = sleep
        self.clock = clock
        self.last_started: float | None = None
        self.requests: list[dict[str, object]] = []
        self._sequence = 0

    def _wait_rate_limit(self) -> None:
        if self.last_started is not None:
            wait = self.min_interval - (self.clock() - self.last_started)
            if wait > 0:
                self.sleep(wait)
        self.last_started = self.clock()

    @staticmethod
    def _location(base_url: str, headers: Mapping[str, str]) -> str | None:
        location = headers.get("Location") or headers.get("location")
        if not location:
            return None
        return urllib.parse.urljoin(base_url, location)

    def fetch(
        self,
        url: str,
        *,
        response_name: str | None = None,
        max_bytes: int | None = None,
        record_body: bool = True,
    ) -> FetchResult:
        requested_url = validate_url(url)
        body_limit = self.max_response_bytes if max_bytes is None else max_bytes
        current_url = requested_url
        attempt = 0
        redirects = 0
        while True:
            self._wait_rate_limit()
            attempt += 1
            request = urllib.request.Request(
                current_url,
                method="GET",
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"},
            )
            started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            try:
                response = self.opener.open(request, timeout=self.timeout)
                status = int(getattr(response, "status", response.getcode()))
                headers = response.headers
                final_url = validate_url(response.geturl())
                location = self._location(final_url, headers)
                if 300 <= status < 400 and location:
                    response.close()
                    redirects += 1
                    if redirects > 5:
                        raise PolicyError("too many redirects")
                    current_url = validate_url(location)
                    continue
                data = read_limited(response, body_limit)
                content_type = headers.get_content_type() if hasattr(headers, "get_content_type") else str(headers.get("Content-Type", "")).split(";", 1)[0]
                response.close()
                result = FetchResult(requested_url, final_url, status, content_type, data, started, attempt)
                self._record(result, response_name, record_body=record_body)
                return result
            except urllib.error.HTTPError as exc:
                location = self._location(current_url, exc.headers)
                if exc.code in {301, 302, 303, 307, 308} and location:
                    redirects += 1
                    if redirects > 5:
                        raise PolicyError("too many redirects") from exc
                    current_url = validate_url(location)
                    continue
                if exc.code == 429 or exc.code == 567 or 500 <= exc.code <= 599:
                    if attempt <= self.retries:
                        delay = parse_retry_after(exc.headers.get("Retry-After"))
                        if delay is None:
                            delay = min(2 ** (attempt - 1), self.max_retry_after)
                        self.sleep(min(delay, self.max_retry_after))
                        continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt <= self.retries:
                    self.sleep(min(2 ** (attempt - 1), self.max_retry_after))
                    continue
                raise

    def _record(self, result: FetchResult, response_name: str | None, *, record_body: bool = True) -> None:
        self._sequence += 1
        suffix = response_name or "response"
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in suffix)[:60] or "response"
        filename = f"{self._sequence:03d}-{safe}.bin"
        response_file = None
        if record_body:
            atomic_write_bytes(self.responses_dir / filename, result.data)
            response_file = f"responses/{filename}"
        self.requests.append(
            {
                "requested_url": result.requested_url,
                "final_url": result.final_url,
                "fetched_at": result.fetched_at,
                "status": result.status,
                "content_type": result.content_type,
                "bytes": len(result.data),
                "sha256": result.sha256,
                "response_file": response_file,
                "attempts": result.attempts,
            }
        )

    def api_get(self, params: Mapping[str, object], *, response_name: str) -> dict:
        action = str(params.get("action", ""))
        if action not in ALLOWED_ACTIONS:
            raise PolicyError(f"MediaWiki action is not allowed: {action!r}")
        query = {key: str(value) for key, value in params.items()}
        query.setdefault("format", "json")
        query.setdefault("formatversion", "2")
        url = API_URL + "?" + urllib.parse.urlencode(query)
        result = self.fetch(url, response_name=response_name)
        if result.content_type not in {"application/json", "text/json", "text/plain"}:
            raise RuntimeError(f"unexpected API content type: {result.content_type}")
        try:
            payload = json.loads(result.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("invalid MediaWiki JSON response") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("MediaWiki response is not an object")
        if "error" in payload:
            raise RuntimeError(f"MediaWiki error: {payload['error']}")
        return payload

    def parse_page(self, title: str, *, response_name: str) -> dict:
        payload = self.api_get(
            {"action": "parse", "page": title, "prop": "text|links|images", "redirects": 1},
            response_name=response_name,
        )
        parsed = payload.get("parse")
        if not isinstance(parsed, dict):
            raise RuntimeError(f"missing parse result for {title}")
        return parsed

    def imageinfo(self, titles: list[str], *, response_name: str, max_continuations: int = 4) -> dict[str, dict]:
        if not titles:
            return {}
        params: dict[str, object] = {
            "action": "query",
            "prop": "imageinfo",
            "titles": "|".join(titles),
            "iiprop": "url|size|mime|sha1",
            "redirects": 1,
        }
        output: dict[str, dict] = {}
        continuation_count = 0
        while True:
            payload = self.api_get(params, response_name=f"{response_name}-{continuation_count + 1}")
            pages = payload.get("query", {}).get("pages", []) if isinstance(payload.get("query"), dict) else []
            if isinstance(pages, dict):
                pages = list(pages.values())
            for page in pages if isinstance(pages, list) else []:
                if not isinstance(page, dict):
                    continue
                title = page.get("title")
                infos = page.get("imageinfo")
                output[str(title)] = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else {"missing": True}
            continuation = payload.get("continue")
            if not isinstance(continuation, dict):
                return output
            continuation_count += 1
            if continuation_count >= max_continuations:
                raise RuntimeError("imageinfo continuation limit exceeded")
            for key, value in continuation.items():
                params[key] = value
