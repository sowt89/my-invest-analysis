"""NAMUH PLUG 국내 시세 조회. 계좌/주문 API는 사용하지 않는다.

명세: https://www.nhplug.com/llms-full.txt
인증정보/원본 응답/토큰은 결과 JSON이나 로그에 기록하지 않는다.
Actions 간 토큰 재사용을 위한 캐시는 앱 시크릿으로 암호화한다.
"""
import base64
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://api.nhplug.com:8443"


class NamuhError(Exception):
    """민감한 요청 URL·응답 본문을 포함하지 않는 오류."""


def number(value):
    try:
        result = float(str(value).replace(",", "").strip())
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def parse_quote(data, code):
    row = data.get("Output_0") if isinstance(data, dict) else None
    if not isinstance(row, dict) or str(row.get("iem_cd", "")).strip() != code:
        raise NamuhError("시세 응답 없음 또는 종목 불일치")
    price = number(row.get("stck_prpr"))
    if price is None or price <= 0:
        raise NamuhError("유효한 현재가 없음")
    volume = number(row.get("acml_vol"))
    return {
        "source": "NAMUH PLUG", "market": "KRX", "price": price,
        "day_change_pct": number(row.get("prdy_ctrt")),
        "volume": int(volume) if volume is not None and volume >= 0 else None,
        # 응답에는 체결 날짜가 없으므로 조회 시각을 체결 시각으로 표시하지 않는다.
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class NamuhClient:
    def __init__(self, app_key, app_secret, cache_path=None, session=None):
        if not app_key or not app_secret:
            raise NamuhError("NAMUH_APP_KEY / NAMUH_APP_SECRET 설정 필요")
        self.app_key, self.app_secret = app_key, app_secret
        self.session = session or requests.Session()
        self.token, self.expires = None, 0
        self.last_call = 0
        self.cache_path = Path(cache_path) if cache_path else None
        self.cipher = None
        if self.cache_path:
            from cryptography.fernet import Fernet, InvalidToken
            key = hashlib.sha256((app_key + "\0" + app_secret).encode()).digest()
            self.cipher = Fernet(base64.urlsafe_b64encode(key))
            try:
                saved = json.loads(self.cipher.decrypt(self.cache_path.read_bytes()))
                self.token, self.expires = saved["token"], float(saved["expires"])
            except (OSError, InvalidToken, ValueError, KeyError, TypeError):
                pass

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("NAMUH_APP_KEY", "").strip(),
                   os.environ.get("NAMUH_APP_SECRET", "").strip(),
                   os.environ.get("NAMUH_TOKEN_CACHE"))

    def _post(self, path, **kwargs):
        time.sleep(max(0, 0.3 - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        try:
            return self.session.post(BASE_URL + path, timeout=15,
                                     allow_redirects=False, **kwargs)
        except requests.RequestException:
            # requests 예외에는 인증 쿼리 문자열이 포함될 수 있다.
            raise NamuhError("나무 연결 실패 또는 시간 초과") from None

    def _token(self, force=False):
        if not force and self.token and self.expires > time.time() + 60:
            return self.token
        response = self._post("/oauth2/token", params={
            "appkey": self.app_key, "appsecretkey": self.app_secret,
            "grant_type": "client_credentials", "scope": "oob",
        }, headers={"content-type": "application/x-www-form-urlencoded"})
        data = self._json(response, "인증")
        token = data.get("access_token") if isinstance(data, dict) else None
        expires = number(data.get("expires_in")) if isinstance(data, dict) else None
        if not isinstance(token, str) or not token or not expires or expires <= 60:
            raise NamuhError("인증 응답 오류 — 앱 키·시크릿 확인 필요")
        self.token, self.expires = token, time.time() + expires
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            encrypted = self.cipher.encrypt(json.dumps({
                "token": self.token, "expires": self.expires,
            }).encode())
            self.cache_path.write_bytes(encrypted)
            self.cache_path.chmod(0o600)
        return self.token

    @staticmethod
    def _json(response, stage):
        if response.status_code != 200:
            raise NamuhError(f"{stage} HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise NamuhError(f"{stage} 응답 형식 오류") from None

    def quote(self, ticker, is_etf=False):
        code = ticker.split(".")[0]
        if len(code) != 6 or not code.isdigit():
            raise NamuhError("국내 종목코드 형식 오류")
        path = "/krstock/quote/v1/etfCurrent" if is_etf else "/krstock/quote/v1/currentPrice"
        inputs = {"iem_cd": code}
        if not is_etf:
            inputs["market_cd"] = "KRX"
        for attempt in range(2):
            token = self._token(force=attempt == 1)
            response = self._post(path, json={"Input_0": inputs}, headers={
                "x-client-id": self.app_key, "x-client-secret": self.app_secret,
                "authorization": "Bearer " + token,
                "content-type": "application/json; charset=UTF-8",
            })
            if attempt == 0 and (response.status_code == 401 or
                                  "IGW40043" in response.text):
                continue
            return parse_quote(self._json(response, "시세"), code)
        raise NamuhError("나무 인증 실패")
