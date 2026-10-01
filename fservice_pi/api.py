"""サーバとのやりとり。口は既存の F-SERVICE（Android）と同じものを使う。

- 心拍・設定   GET  /v1/config
- 生コマ       POST /v1/detframes（application/zip・Bearer）
- 申告         POST /v1/detlog（JSON Lines・Bearer）

認証は Authorization: Bearer <token>。store_id などのクエリは設定の server.query で付ける。
"""

from __future__ import annotations

import enum
import json
import logging

import requests

from . import VERSION
from .config import ServerConfig

log = logging.getLogger(__name__)


class Sent(enum.Enum):
    OK = "ok"
    RETRY = "retry"  # 回線・サーバ側の一時的な失敗。あとで送り直す
    REJECT = "reject"  # 中身を拒否された（400 など）。送り直しても通らない


class ServerApi:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"fservice-pi/{VERSION}"
        if cfg.token:
            self.session.headers["Authorization"] = f"Bearer {cfg.token}"

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.base_url)

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def heartbeat(self, telemetry: dict) -> dict | None:
        """心拍を送り、返ってきた設定（JSON）を返す。失敗なら None。"""
        params = dict(self.cfg.query)
        params.update({k: str(v) for k, v in telemetry.items()})
        try:
            r = self.session.get(self._url("/v1/config"), params=params, timeout=self.cfg.timeout_s)
        except requests.RequestException as e:
            log.warning("心拍に失敗: %s", e)
            return None
        if r.status_code != 200:
            log.warning("心拍に失敗: HTTP %d %s", r.status_code, r.text[:200])
            return None
        try:
            return r.json()
        except ValueError:
            log.warning("心拍の返事が JSON でない: %s", r.text[:200])
            return None

    def post_detframes(self, body: bytes) -> tuple[Sent, str]:
        return self._post("/v1/detframes", body, "application/zip")

    def post_detlog(self, lines: list[dict]) -> tuple[Sent, str]:
        body = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines).encode()
        return self._post("/v1/detlog", body, "application/x-ndjson")

    def _post(self, path: str, body: bytes, ctype: str) -> tuple[Sent, str]:
        try:
            r = self.session.post(
                self._url(path), params=self.cfg.query, data=body,
                headers={"Content-Type": ctype}, timeout=self.cfg.timeout_s,
            )
        except requests.RequestException as e:
            return Sent.RETRY, str(e)
        if 200 <= r.status_code < 300:
            return Sent.OK, ""
        detail = f"HTTP {r.status_code} {r.text[:200]}"
        # 認証・回線・サーバ都合は送り直せば通りうる。中身の拒否（400/413/422）は通らない
        if r.status_code in (400, 413, 415, 422):
            return Sent.REJECT, detail
        return Sent.RETRY, detail
