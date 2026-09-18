"""База коннекторов: доступ, транспорт с allowlist, сохранение raw."""
from __future__ import annotations

from ..models import RawDocument, SearchPage, TenderSpec
from ..transports.http import HttpTransport


class Connector:
    code = ""
    label = ""
    live_allowed = True
    policy_reason = ""
    hosts: set[str] = set()
    page_size = 20
    # источник с жёстким rate limit может переопределить задержку
    delay_override: float | None = None

    def __init__(self, raw_saver=None):
        self.raw_saver = raw_saver      # callable(root, doc) -> raw_ref

    def make_transport(self, spec: TenderSpec) -> HttpTransport:
        from ..models import TransportConfig
        cfg = spec.transport
        if self.delay_override is not None and \
                cfg.request_delay_seconds < self.delay_override:
            cfg = TransportConfig(
                request_delay_seconds=self.delay_override,
                timeout_seconds=cfg.timeout_seconds,
                max_retries=cfg.max_retries)
        return HttpTransport(cfg, allowed_hosts=self.hosts)

    def search(self, spec: TenderSpec, cursor: str | None) -> SearchPage:
        raise NotImplementedError

    def parse_search(self, doc: RawDocument) -> SearchPage:
        raise NotImplementedError

    def keep_raw(self, doc: RawDocument, page: SearchPage) -> None:
        if self.raw_saver is not None:
            page.raw_ref = self.raw_saver(doc)
