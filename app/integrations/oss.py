"""Aliyun OSS object bytes. Clients never receive these credentials.

Images land in ``kelin/images/`` and voice in ``kelin/voice/``.
The API still serves signed ``/object/...`` URLs; this module is only the byte store.
"""

from __future__ import annotations

import hashlib
import hmac
from base64 import b64encode
from email.utils import formatdate
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from app.core.config import Settings
from app.integrations.storage import StorageObjectMissing, StorageUnavailable

_LIST_PAGE = "1000"
_MAX_LIST_PAGES = 50
_DEFAULT_IMAGE_PREFIX = "kelin/images"
_DEFAULT_VOICE_PREFIX = "kelin/voice"
_VOICE_SUFFIXES = (".m4a", ".mp3", ".wav", ".aac")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def string_to_sign(
    *,
    method: str,
    content_type: str,
    date: str,
    canonical: str,
) -> str:
    return f"{method}\n\n{content_type}\n{date}\n{canonical}"


def authorization_header(
    *,
    method: str,
    content_type: str,
    date: str,
    canonical: str,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    signed = string_to_sign(
        method=method,
        content_type=content_type,
        date=date,
        canonical=canonical,
    )
    digest = hmac.new(
        access_key_secret.encode("utf-8"),
        signed.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return f"OSS {access_key_id}:{b64encode(digest).decode('ascii')}"


def _endpoint_host(endpoint: str) -> str:
    raw = endpoint.strip().rstrip("/")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    host = raw.split("/", 1)[0]
    if not host or "@" in host or any(char in host for char in " \t\r\n"):
        raise StorageUnavailable("object storage endpoint is invalid")
    return host


def _is_voice(object_path: str) -> bool:
    return object_path.lower().endswith(_VOICE_SUFFIXES)


def _object_key(bucket: str, object_path: str) -> str:
    if (
        not bucket
        or "/" in bucket
        or "\\" in bucket
        or object_path.startswith("/")
        or "\\" in object_path
        or ".." in object_path.split("/")
        or any(char in object_path for char in "\r\n")
    ):
        raise StorageUnavailable("object key is invalid")
    return f"{bucket}/{object_path}"


def _base_folder(value: str) -> str:
    raw = value.strip().strip("/")
    if not raw:
        return ""
    parts = raw.split("/")
    if any(part in {"", ".", ".."} or "\\" in part for part in parts):
        raise StorageUnavailable("object storage folder is invalid")
    return "/".join(parts)


def _oss_error_code(payload: bytes) -> str:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ""
    for element in root.iter():
        if _local(element.tag) != "Code" or not element.text:
            continue
        code = element.text.strip()
        if code.isascii() and code.replace("-", "").isalnum() and len(code) <= 64:
            return code
        return ""
    return ""


class AliyunOssBackend:
    """One physical bucket. `bucket` arguments are logical prefixes."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        access_key_secret: str,
        image_prefix: str = _DEFAULT_IMAGE_PREFIX,
        voice_prefix: str = _DEFAULT_VOICE_PREFIX,
        client: httpx.Client | None = None,
    ) -> None:
        host = _endpoint_host(endpoint)
        self._bucket = bucket.strip()
        if not self._bucket or "/" in self._bucket:
            raise StorageUnavailable("object storage bucket is invalid")
        self._image = _base_folder(image_prefix or _DEFAULT_IMAGE_PREFIX)
        self._voice = _base_folder(voice_prefix or _DEFAULT_VOICE_PREFIX)
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._origin = f"https://{self._bucket}.{host}"
        self._client = client or httpx.Client(timeout=10.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _key(self, bucket: str, object_path: str) -> str:
        _object_key(bucket, object_path)
        folder = self._voice if _is_voice(object_path) else self._image
        return f"{folder}/{object_path}"

    def put(self, bucket: str, object_path: str, body: bytes, *, content_type: str) -> None:
        self._request("PUT", self._key(bucket, object_path), content=body, content_type=content_type)

    def get(self, bucket: str, object_path: str) -> bytes:
        key = self._key(bucket, object_path)
        try:
            response = self._request("GET", key, content_type="")
        except StorageObjectMissing:
            raise
        return response.content

    def delete(self, bucket: str, object_path: str) -> bool:
        if not self.contains(bucket, object_path):
            return False
        self._request("DELETE", self._key(bucket, object_path), content_type="")
        return True

    def contains(self, bucket: str, object_path: str) -> bool:
        key = self._key(bucket, object_path)
        try:
            self._request("HEAD", key, content_type="")
        except StorageObjectMissing:
            return False
        return True

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        _object_key(bucket, prefix)
        found: list[str] = []
        for folder in (self._image, self._voice):
            found.extend(self._list_folder(f"{folder}/{prefix}", lead=f"{folder}/"))
        return sorted(set(found))

    def _list_folder(self, oss_prefix: str, *, lead: str) -> list[str]:
        found: list[str] = []
        marker = ""
        for _ in range(_MAX_LIST_PAGES):
            params = {"max-keys": _LIST_PAGE, "prefix": oss_prefix}
            if marker:
                params["marker"] = marker
            response = self._request("GET", "", params=params, content_type="")
            keys, truncated, next_marker = _parse_list(response.content)
            for key in keys:
                if key.startswith(lead):
                    found.append(key[len(lead) :])
            if not truncated:
                break
            marker = next_marker or (keys[-1] if keys else "")
            if not marker:
                break
        return found

    def _canonical(self, key: str, params: dict[str, str] | None) -> str:
        resource = f"/{self._bucket}/{key}"
        if not params:
            return resource
        parts = [
            name if params[name] == "" else f"{name}={params[name]}"
            for name in sorted(params)
        ]
        return resource + "?" + "&".join(parts)

    def _request(
        self,
        method: str,
        key: str,
        *,
        content: bytes | None = None,
        content_type: str,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        date = formatdate(timeval=None, localtime=False, usegmt=True)
        canonical = self._canonical(key, params)
        headers = {
            "Date": date,
            "Authorization": authorization_header(
                method=method,
                content_type=content_type,
                date=date,
                canonical=canonical,
                access_key_id=self._access_key_id,
                access_key_secret=self._access_key_secret,
            ),
        }
        if content_type:
            headers["Content-Type"] = content_type
        path = "/" if not key else "/" + quote(key, safe="/")
        try:
            response = self._client.request(
                method,
                f"{self._origin}{path}",
                headers=headers,
                content=content,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise StorageUnavailable("object storage is unavailable") from exc
        if response.status_code == 404:
            raise StorageObjectMissing("object is missing")
        if response.status_code >= 400:
            code = _oss_error_code(response.content)
            detail = f"{response.status_code} {code}".strip()
            raise StorageUnavailable(f"object storage is unavailable ({detail})")
        return response


def _parse_list(payload: bytes) -> tuple[list[str], bool, str | None]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise StorageUnavailable("object storage is unavailable") from exc
    keys = [
        element.text
        for element in root.iter()
        if _local(element.tag) == "Key" and element.text
    ]
    truncated = any(
        _local(element.tag) == "IsTruncated" and (element.text or "").strip().lower() == "true"
        for element in root.iter()
    )
    markers = [
        element.text
        for element in root.iter()
        if _local(element.tag) == "NextMarker" and element.text
    ]
    return keys, truncated, markers[0] if markers else None


def backend_from_settings(settings: Settings) -> AliyunOssBackend | None:
    """Tests stay on process memory even when the developer machine has OSS env."""
    if settings.app_env == "test":
        return None
    endpoint = settings.oss_endpoint
    bucket = settings.oss_bucket
    key_id = settings.oss_access_key_id
    secret = settings.oss_access_key_secret
    if endpoint is None or bucket is None or key_id is None or secret is None:
        return None
    return AliyunOssBackend(
        endpoint=endpoint,
        bucket=bucket,
        access_key_id=key_id.get_secret_value(),
        access_key_secret=secret.get_secret_value(),
        image_prefix=settings.oss_image_prefix or _DEFAULT_IMAGE_PREFIX,
        voice_prefix=settings.oss_voice_prefix or _DEFAULT_VOICE_PREFIX,
    )
