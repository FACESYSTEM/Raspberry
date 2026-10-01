"""サーバとのやりとり。Android 版 F-SERVICE の Api.kt と同じ口・同じ流儀。

- 名乗り（登録）   POST /v1/announce   管理画面で店に割り当てられたらトークンが返る
- 心拍・設定       GET  /v1/config     テレメトリをクエリで送り、営業時間・face_params を受け取る
- 生コマ           POST /v1/detframes  application/zip
- 申告             POST /v1/detlog     application/x-ndjson

店はトークンからサーバが決める（クエリで store_id を送らない）。

成否の読み方（Api.kt の readResponse と同じ）:
- 200 でも本文が JSON でなければ送れていない（店の Wi-Fi の同意ページが 200 で HTML を返す）
- 4xx は送り直しても同じ。ただし 401/403 はトークンの問題で中身の問題ではないので、
  生コマは捨てずに送り直す側に置く
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass

import requests

from . import VERSION, VERSION_CODE
from .config import ServerConfig

log = logging.getLogger(__name__)

APP_ID = "jp.facesystem.fservice.pi"


class Sent(enum.Enum):
    OK = "ok"
    RETRY = "retry"  # 回線・サーバ都合・トークンの問題。あとで送り直す
    REJECT = "reject"  # 中身を拒否された。送り直しても通らない


@dataclass
class Assigned:
    token: str
    store_id: str
    store_name: str
    business_hours: str


def _json_of(r: requests.Response) -> dict | None:
    try:
        v = r.json()
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _error_of(r: requests.Response, body: dict | None) -> str:
    if body:
        msg = body.get("error_jp") or body.get("error")
        if msg:
            return f"HTTP {r.status_code} {msg}"
    return f"HTTP {r.status_code} {r.text[:80]}"


class ServerApi:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"fservice-pi/{VERSION}"
        self.token = cfg.token

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.base_url)

    @property
    def ready(self) -> bool:
        """送ってよい状態か（サーバがあり、トークンを持っている）。"""
        return self.enabled and bool(self.token)

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    # --- 名乗り ---

    def announce(self, terminal_instance_id: str, device_model: str, os_version: str) -> Assigned | None:
        """割り当て済みなら Assigned。未割り当て・失敗なら None。"""
        body = {
            "terminal_instance_id": terminal_instance_id,
            "device_model": device_model,
            "os_version": os_version,
            "app": APP_ID,
            "version_code": VERSION_CODE,
        }
        try:
            r = self.session.post(self._url("/v1/announce"), json=body, timeout=self.cfg.timeout_s)
        except requests.RequestException as e:
            log.warning("名乗りに失敗: %s", e)
            return None
        data = _json_of(r)
        if r.status_code != 200 or data is None:
            log.warning("名乗りに失敗: %s", _error_of(r, data))
            return None
        if not data.get("assigned") or not data.get("token"):
            return None
        return Assigned(str(data["token"]), str(data.get("store_id", "")),
                        str(data.get("store_name", "")), str(data.get("business_hours") or ""))

    # --- 心拍 ---

    def heartbeat(self, query: dict) -> tuple[Sent, dict | str]:
        """(OK, 設定の JSON) か (RETRY/REJECT, 理由)。"""
        params = dict(self.cfg.query)
        params.update({k: str(v) for k, v in query.items()})
        try:
            r = self.session.get(self._url("/v1/config"), params=params, headers=self._auth(),
                                 timeout=self.cfg.timeout_s)
        except requests.RequestException as e:
            return Sent.RETRY, str(e)
        data = _json_of(r)
        if 200 <= r.status_code < 300:
            if data is None:
                return Sent.RETRY, f"HTTP {r.status_code} 本文が JSON ではない: {r.text[:40]}"
            return Sent.OK, data
        if 400 <= r.status_code < 500:
            return Sent.REJECT, _error_of(r, data)
        return Sent.RETRY, _error_of(r, data)

    # --- 送る ---

    def post_detframes(self, body: bytes) -> tuple[Sent, str]:
        # ok の無い応答を成功にしない（Api.kt uploadDetframes と同じ）
        return self._post("/v1/detframes", body, "application/zip", require_ok=True)

    def post_detlog(self, lines: list[dict]) -> tuple[Sent, str]:
        body = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines).encode()
        return self._post("/v1/detlog", body, "application/x-ndjson", require_ok=False)

    def _post(self, path: str, body: bytes, ctype: str, require_ok: bool) -> tuple[Sent, str]:
        headers = self._auth()
        headers["Content-Type"] = ctype
        try:
            r = self.session.post(self._url(path), params=self.cfg.query or None, data=body,
                                  headers=headers, timeout=self.cfg.timeout_s)
        except requests.RequestException as e:
            return Sent.RETRY, str(e)
        data = _json_of(r)
        if 200 <= r.status_code < 300:
            if data is None:
                return Sent.RETRY, f"HTTP {r.status_code} 本文が JSON ではない: {r.text[:40]}"
            if require_ok and data.get("ok") is not True:
                return Sent.RETRY, f"HTTP {r.status_code} ok が無い: {str(data)[:80]}"
            return Sent.OK, ""
        if r.status_code in (401, 403):
            return Sent.RETRY, _error_of(r, data)  # トークンの問題。中身は悪くないので捨てない
        if 400 <= r.status_code < 500:
            return Sent.REJECT, _error_of(r, data)
        return Sent.RETRY, _error_of(r, data)
