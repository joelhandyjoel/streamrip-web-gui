import asyncio
import base64
import hashlib
import logging
import re
import time
from collections import OrderedDict
from typing import List, Optional

import aiohttp

from ..config import Config
from ..exceptions import (
    AuthenticationError,
    IneligibleError,
    InvalidAppIdError,
    InvalidAppSecretError,
    MissingCredentialsError,
    NonStreamableError,
)
from streamrip.client.client import Client
from streamrip.client.downloadable import BasicDownloadable, Downloadable

logger = logging.getLogger("streamrip")

QOBUZ_BASE_URL = "https://www.qobuz.com/api.json/0.2"

QOBUZ_FEATURED_KEYS = {
    "most-streamed",
    "recent-releases",
    "best-sellers",
    "press-awards",
    "ideal-discography",
    "editor-picks",
    "most-featured",
    "qobuzissims",
    "new-releases",
    "new-releases-full",
    "harmonia-mundi",
    "universal-classic",
    "universal-jazz",
    "universal-jeunesse",
    "universal-chanson",
}


class QobuzSpoofer:
    """Spoofs the information required to stream tracks from Qobuz."""

    def __init__(self, verify_ssl: bool = True):
        self.seed_timezone_regex = (
            r'[a-z]\.initialSeed\("(?P<seed>[\w=]+)",window\.ut'
            r"imezone\.(?P<timezone>[a-z]+)\)"
        )
        self.info_extras_regex = (
            r'name:"\w+/(?P<timezone>{timezones})",info:"'
            r'(?P<info>[\w=]+)",extras:"(?P<extras>[\w=]+)"'
        )
        self.app_id_regex = (
            r'production:{api:{appId:"(?P<app_id>\d{9})",appSecret:"(\w{32})'
        )
        self.session = None
        self.verify_ssl = verify_ssl

    async def get_app_id_and_secrets(self) -> tuple[str, list[str]]:
        assert self.session is not None

        async with self.session.get("https://play.qobuz.com/login") as req:
            login_page = await req.text()

        bundle_url_match = re.search(
            r'<script src="(/resources/\d+\.\d+\.\d+-[a-z]\d{3}/bundle\.js)"></script>',
            login_page,
        )
        assert bundle_url_match is not None
        bundle_url = bundle_url_match.group(1)

        async with self.session.get("https://play.qobuz.com" + bundle_url) as req:
            bundle = await req.text()

        match = re.search(self.app_id_regex, bundle)
        if match is None:
            raise Exception("Could not find app id.")

        app_id = str(match.group("app_id"))

        seed_matches = re.finditer(self.seed_timezone_regex, bundle)
        secrets = OrderedDict()
        for match in seed_matches:
            seed, timezone = match.group("seed", "timezone")
            secrets[timezone] = [seed]

        keypairs = list(secrets.items())
        secrets.move_to_end(keypairs[1][0], last=False)

        info_extras_regex = self.info_extras_regex.format(
            timezones="|".join(timezone.capitalize() for timezone in secrets),
        )

        for match in re.finditer(info_extras_regex, bundle):
            timezone, info, extras = match.group("timezone", "info", "extras")
            secrets[timezone.lower()] += [info, extras]

        for k in secrets:
            secrets[k] = base64.standard_b64decode(
                "".join(secrets[k])[:-44]
            ).decode("utf-8")

        vals = list(secrets.values())
        if "" in vals:
            vals.remove("")

        return app_id, vals

    async def __aenter__(self):
        from ..utils.ssl_utils import get_aiohttp_connector_kwargs

        connector = aiohttp.TCPConnector(
            **get_aiohttp_connector_kwargs(verify_ssl=True)
        )
        self.session = aiohttp.ClientSession(connector=connector)
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()


class QobuzClient(Client):
    source = "qobuz"
    max_quality = 4

    def __init__(self, config: Config):
        self.logged_in = False
        self.config = config
        self.rate_limiter = self.get_rate_limiter(
            config.session.downloads.requests_per_minute
        )
        self.secret: Optional[str] = None

    async def login(self):
        self.session = await self.get_session(
            verify_ssl=self.config.session.downloads.verify_ssl
        )

        c = self.config.session.qobuz
        if not c.email_or_userid or not c.password_or_token:
            raise MissingCredentialsError

        if not c.app_id or not c.secrets:
            logger.info("Fetching Qobuz app id and secrets")
            c.app_id, c.secrets = await self._get_app_id_and_secrets()
            f = self.config.file
            f.qobuz.app_id = c.app_id
            f.qobuz.secrets = c.secrets
            f.set_modified()

        self.session.headers.update({"X-App-Id": str(c.app_id)})

        params = (
            {
                "user_id": c.email_or_userid,
                "user_auth_token": c.password_or_token,
                "app_id": str(c.app_id),
            }
            if c.use_auth_token
            else {
                "email": c.email_or_userid,
                "password": c.password_or_token,
                "app_id": str(c.app_id),
            }
        )

        status, resp = await self._api_request("user/login", params)

        if status == 401:
            raise AuthenticationError("Invalid Qobuz credentials")
        if status == 400:
            raise InvalidAppIdError("Invalid Qobuz app id")

        if not resp["user"]["credential"]["parameters"]:
            raise IneligibleError("Account not eligible for downloads")

        self.session.headers.update(
            {"X-User-Auth-Token": resp["user_auth_token"]}
        )

        self.secret = await self._get_valid_secret(c.secrets)
        self.logged_in = True

    async def get_downloadable(self, item: str, quality: int) -> Downloadable:
        status, resp = await self._request_file_url(item, quality, self.secret)
        if status != 200 or "url" not in resp:
            raise NonStreamableError

        return BasicDownloadable(
            self.session,
            resp["url"],
            "flac" if quality > 1 else "mp3",
            source="qobuz",
        )

    async def inspect_track_quality(self, track_id: str, max_quality: int) -> dict:
        assert self.secret and self.logged_in

        max_quality = max(1, min(max_quality, self.max_quality))
        results = []

        for q in range(max_quality, 0, -1):
            status, resp = await self._request_file_url(track_id, q, self.secret)

            if status == 400 and "Invalid Request Signature" in str(resp):
                status, resp = await self._request_file_url(track_id, q, self.secret)

            if status == 200 and "url" in resp:
                results.append(
                    {
                        "quality_level": q,
                        "format_id": self.get_quality(q),
                        "bit_depth": resp.get("bit_depth"),
                        "sampling_rate": resp.get("sampling_rate"),
                        "available": True,
                    }
                )
            else:
                results.append(
                    {
                        "quality_level": q,
                        "format_id": self.get_quality(q),
                        "available": False,
                        "error": resp.get("message") or resp.get("error"),
                    }
                )

        return {"track_id": track_id, "results": results}

    async def _request_file_url(
        self, track_id: str, quality: int, secret: str
    ) -> tuple[int, dict]:
        quality = self.get_quality(quality)
        ts = time.time()
        sig = hashlib.md5(
            f"trackgetFileUrlformat_id{quality}intentstreamtrack_id{track_id}{ts}{secret}".encode()
        ).hexdigest()

        params = {
            "request_ts": ts,
            "request_sig": sig,
            "track_id": track_id,
            "format_id": quality,
            "intent": "stream",
        }

        return await self._api_request("track/getFileUrl", params)

    async def _api_request(self, epoint: str, params: dict) -> tuple[int, dict]:
        url = f"{QOBUZ_BASE_URL}/{epoint}"
        async with self.rate_limiter:
            async with self.session.get(url, params=params) as r:
                return r.status, await r.json()

    @staticmethod
    def get_quality(quality: int) -> int:
        return (5, 6, 7, 27)[quality - 1]
