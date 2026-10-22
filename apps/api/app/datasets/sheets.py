"""Importing a Google Sheet that is shared by link, as CSV, into the same path as an uploaded file.

Only Google's own hosts are ever contacted: the export URL is built here from the sheet's id (never fetched as
given), and redirects are followed only to docs.google.com and *.googleusercontent.com over HTTPS. That keeps a
pasted link from making the server fetch anything else (no SSRF into the host's network). The download is capped
at the upload size limit while it streams, and a sheet that isn't public is refused with what to change.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx

from app.datasets.csv_upload import UploadRejected

SHEET_URL = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,100})(?:[/?#].*)?$")
ALLOWED_HOSTS = re.compile(r"^(docs\.google\.com|[a-z0-9-]+\.googleusercontent\.com)$")
MAX_REDIRECTS = 4


def not_a_sheet() -> UploadRejected:
    return UploadRejected("not_a_sheet", "This isn't a Google Sheets link",
                          "Paste the link from the address bar of the sheet, starting with "
                          "https://docs.google.com/spreadsheets/d/.")


def not_public() -> UploadRejected:
    return UploadRejected("sheet_not_public", "This sheet isn't shared by link",
                          "In Google Sheets, choose Share, set General access to \"Anyone with the link\", "
                          "then try again.")


@dataclass(frozen=True)
class Sheet:
    id: str
    gid: str | None  # which tab; None is the first

    @property
    def url(self) -> str:
        """The link to the sheet, as kept with the dataset so it can be imported again."""
        return f"https://docs.google.com/spreadsheets/d/{self.id}/edit" + (f"#gid={self.gid}" if self.gid else "")

    @property
    def export_url(self) -> str:
        return (f"https://docs.google.com/spreadsheets/d/{self.id}/export?format=csv"
                + (f"&gid={self.gid}" if self.gid else ""))


def parse(link: str) -> Sheet:
    """The sheet (and tab) a Google Sheets link points to. Raises UploadRejected for anything else."""
    link = link.strip()
    match = SHEET_URL.match(link)
    if not match:
        raise not_a_sheet()
    parts = urlsplit(link)
    gids = parse_qs(parts.query).get("gid", []) + parse_qs(parts.fragment).get("gid", [])
    gid = next((g for g in gids if g.isdigit()), None)
    return Sheet(match.group(1), gid)


def _file_name(disposition: str | None) -> str:
    """The sheet's title from the download's Content-Disposition, as a .csv file name."""
    if disposition:
        if m := re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE):
            return unquote(m.group(1).strip())
        if m := re.search(r'filename="([^"]+)"', disposition):
            return m.group(1)
    return "Google Sheet.csv"


class SheetsClient:
    def __init__(self, transport: httpx.BaseTransport | None = None, timeout_s: float = 15):
        self._transport = transport
        self._timeout = timeout_s

    def fetch(self, sheet: Sheet, max_bytes: int) -> tuple[bytes, str]:
        """The sheet's tab as CSV, and a file name for it. Raises UploadRejected when it can't be had."""
        url = sheet.export_url
        with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                try:
                    with client.stream("GET", url) as r:
                        if r.is_redirect:
                            url = urljoin(url, r.headers.get("location", ""))
                            target = urlsplit(url)
                            if (target.scheme != "https" or target.port not in (None, 443)
                                    or not ALLOWED_HOSTS.match(target.hostname or "")):
                                raise not_public()  # Google sends private sheets to its sign-in page
                            continue
                        return self._read(r, max_bytes)
                except httpx.HTTPError as e:
                    raise UploadRejected("sheet_unavailable", "Google Sheets didn't answer",
                                         "Try again in a minute.", status=502) from e
        raise UploadRejected("sheet_unavailable", "Google Sheets didn't answer", "Try again in a minute.", status=502)

    @staticmethod
    def _read(r: httpx.Response, max_bytes: int) -> tuple[bytes, str]:
        if r.status_code in (401, 403):
            raise not_public()
        if r.status_code in (400, 404):
            raise UploadRejected("sheet_not_found", "This sheet or tab wasn't found",
                                 "Check the link, and that the tab still exists.", status=404)
        if r.status_code != 200:
            raise UploadRejected("sheet_unavailable", "Google Sheets didn't answer", "Try again in a minute.",
                                 status=502)
        if "text/html" in r.headers.get("content-type", ""):
            raise not_public()  # a sign-in page instead of the data
        data = bytearray()
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > max_bytes:
                raise UploadRejected("file_too_large", "This sheet is too large",
                                     f"Sheets can be up to {max_bytes // (1024 * 1024)} MB as CSV.", status=413)
        return bytes(data), _file_name(r.headers.get("content-disposition"))
