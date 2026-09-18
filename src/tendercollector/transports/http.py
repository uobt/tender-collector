"""HTTP-транспорт: GET и POST JSON с лимитом, задержкой, ретраями, allowlist."""
from __future__ import annotations

import gzip
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from ..models import RawDocument, TransportConfig, TransportError, request_id

RETRYABLE = {429, 500, 502, 503, 504}


class HttpTransport:
    def __init__(self, config: TransportConfig | None = None,
                 allowed_hosts: set[str] | None = None):
        self.cfg = config or TransportConfig()
        self.allowed_hosts = allowed_hosts
        self._last_request_at = 0.0
        self.requests_made = 0

    # ------------------------------------------------------------- public
    def get(self, url: str, extra_headers: dict | None = None) -> RawDocument:
        return self._request("GET", url, None, extra_headers)

    def post_json(self, url: str, payload: dict,
                  extra_headers: dict | None = None) -> RawDocument:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        headers.update(extra_headers or {})
        return self._request("POST", url, body, headers)

    # ------------------------------------------------------------- inner
    def _check_host(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https":
            raise TransportError(f"только https разрешён: {url}")
        if self.allowed_hosts is not None:
            host = (parsed.hostname or "").lower()
            if host not in self.allowed_hosts:
                raise TransportError(f"хост вне allowlist: {host}")

    def _throttle(self) -> None:
        delta = time.monotonic() - self._last_request_at
        wait = self.cfg.request_delay_seconds - delta
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _request(self, method: str, url: str, body: bytes | None,
                 headers: dict | None) -> RawDocument:
        self._check_host(url)
        req_id = request_id()
        last_error: str | None = None
        last_status: int | None = None

        # бюджет сна на весь цикл ретраев, а не на попытку
        total_sleep_budget = 60.0
        slept = 0.0
        for attempt in range(self.cfg.max_retries + 1):
            self._throttle()
            request = urllib.request.Request(
                url, data=body, method=method,
                headers={"User-Agent": "tender-collector/0.1", **(headers or {})})
            try:
                with urllib.request.urlopen(
                        request, timeout=self.cfg.timeout_seconds) as response:
                    raw_bytes = response.read()
                    self.requests_made += 1
                    return RawDocument(
                        source="", url=url, status=response.status,
                        content=self._decode(raw_bytes, response),
                        mime=response.headers.get("Content-Type", ""),
                        req_id=req_id)
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                raw_bytes = exc.read()
                last_error = f"HTTP {exc.code}: " + self._decode(
                    raw_bytes, None)[:200]
                if exc.code not in RETRYABLE:
                    break
                retry_after = self._retry_after_seconds(exc.headers)
                if retry_after is not None:
                    if retry_after > total_sleep_budget - slept:
                        break  # слишком долго — отдаём ошибку, не сокращаем паузу
                    time.sleep(retry_after)
                    slept += retry_after
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"

        raise TransportError(
            last_error or "unknown transport error", status=last_status)

    @staticmethod
    def _retry_after_seconds(headers) -> float | None:
        if headers is None:
            return None
        value = headers.get("Retry-After")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None  # HTTP-date формат: отказываемся, отдаём ошибку выше

    @staticmethod
    def _decode(raw_bytes: bytes, response) -> str:
        encoding = None
        if response is not None:
            encoding = response.headers.get_content_charset()
        if not encoding:
            head = raw_bytes[:2]
            if head == b"\x1f\x8b":                     # gzip
                raw_bytes = gzip.decompress(raw_bytes)
            elif raw_bytes[:1] == b"\x78":              # zlib (deflate)
                try:
                    raw_bytes = zlib.decompress(raw_bytes)
                except zlib.error:
                    raw_bytes = zlib.decompress(
                        raw_bytes, -zlib.MAX_WBITS)
            encoding = "utf-8"
        return io.TextIOWrapper(io.BytesIO(raw_bytes), encoding=encoding,
                                errors="replace").read()
