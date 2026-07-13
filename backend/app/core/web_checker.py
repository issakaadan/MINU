from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import socket
import ssl
from typing import Any

from app.core.tls_checker import inspect_tls_connection

SECURITY_HEADERS = (
    "content-security-policy",
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
)
TECHNOLOGY_HINT_HEADERS = (
    "server",
    "x-powered-by",
    "x-aspnet-version",
    "x-generator",
    "via",
    "x-runtime",
    "x-backend-server",
    "x-drupal-cache",
    "x-redirect-by",
    "x-elastic-product",
)
ADMIN_HINTS = (
    "admin",
    "dashboard",
    "management",
    "control panel",
    "console",
)
DEFAULT_PAGE_HINTS = (
    "apache2 ubuntu default page",
    "welcome to nginx",
    "test page for",
    "iis windows server",
    "default web site",
    "it works!",
)
DIRECTORY_LISTING_HINTS = (
    "index of /",
    "directory listing for /",
    "parent directory",
)
LOGIN_HINTS = (
    "login",
    "log in",
    "sign in",
    "username",
    "password",
    "authenticate",
)
TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
STATUS_PATTERN = re.compile(r"^HTTP/\d+(?:\.\d+)?\s+(\d{3})(?:\s+(.*))?$", re.IGNORECASE)
COOKIE_NAME_PATTERN = re.compile(r"^\s*([^=;,\s]+)")
SAMESITE_PATTERN = re.compile(r"samesite\s*=\s*([^;]+)", re.IGNORECASE)


@dataclass(frozen=True)
class CookieObservation:
    name: str
    secure: bool
    http_only: bool
    same_site: str
    missing_flags: list[str]


@dataclass(frozen=True)
class WebCheckResult:
    scheme: str
    url: str
    status_code: int | None
    reason_phrase: str
    page_title: str
    server_header: str
    technology_hints: list[str]
    headers: dict[str, str]
    missing_security_headers: list[str]
    cookies: list[CookieObservation]
    risky_cookies: list[CookieObservation]
    robots_txt_detected: bool
    sitemap_xml_detected: bool
    directory_listing_detected: bool
    default_page_detected: bool
    login_page_detected: bool
    is_admin_page: bool
    https_in_use: bool
    https_warnings: list[str]
    redirect_location: str
    body_snippet: str
    tls: dict[str, Any]

    @property
    def summary_banner(self) -> str:
        parts = [
            f"{self.status_code}" if self.status_code is not None else "",
            self.reason_phrase,
            self.server_header,
            self.page_title,
        ]
        summary = " | ".join(part for part in parts if part)
        return summary[:500]

    def to_observation_payload(self) -> dict[str, Any]:
        return {
            "web": {
                "scheme": self.scheme,
                "url": self.url,
                "status_code": self.status_code,
                "reason_phrase": self.reason_phrase,
                "page_title": self.page_title,
                "server_header": self.server_header,
                "technology_hints": self.technology_hints,
                "headers": self.headers,
                "missing_security_headers": self.missing_security_headers,
                "cookies": [asdict(cookie) for cookie in self.cookies],
                "risky_cookies": [asdict(cookie) for cookie in self.risky_cookies],
                "robots_txt_detected": self.robots_txt_detected,
                "sitemap_xml_detected": self.sitemap_xml_detected,
                "directory_listing_detected": self.directory_listing_detected,
                "default_page_detected": self.default_page_detected,
                "login_page_detected": self.login_page_detected,
                "is_admin_page": self.is_admin_page,
                "https_in_use": self.https_in_use,
                "https_warnings": self.https_warnings,
                "redirect_location": self.redirect_location,
                "body_snippet": self.body_snippet,
            },
            "http": {
                "status_code": self.status_code,
                "reason_phrase": self.reason_phrase,
                "headers": self.headers,
                "page_title": self.page_title,
                "body_snippet": self.body_snippet,
                "is_admin_page": self.is_admin_page,
                "login_page_detected": self.login_page_detected,
                "directory_listing_detected": self.directory_listing_detected,
                "default_page_detected": self.default_page_detected,
                "missing_security_headers": self.missing_security_headers,
                "redirect_location": self.redirect_location,
                "server": self.server_header,
                "technology_hints": self.technology_hints,
                "robots_txt_detected": self.robots_txt_detected,
                "sitemap_xml_detected": self.sitemap_xml_detected,
                "cookies": [asdict(cookie) for cookie in self.cookies],
                "risky_cookies": [asdict(cookie) for cookie in self.risky_cookies],
                "https_warnings": self.https_warnings,
            },
            "tls": self.tls,
        }


@dataclass(frozen=True)
class ParsedHttpResponse:
    status_code: int | None
    reason_phrase: str
    headers: dict[str, str]
    header_items: list[tuple[str, str]]
    body: str


def decode_bytes(payload: bytes) -> str:
    return payload.decode("utf-8", errors="ignore")


def read_socket_payload(connection: socket.socket, limit: int = 16384) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = connection.recv(min(2048, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
        if len(chunk) < 2048:
            break
    return b"".join(chunks)


def parse_http_response(response_text: str) -> ParsedHttpResponse | None:
    if not response_text:
        return None

    header_text, separator, body = response_text.partition("\r\n\r\n")
    if not separator:
        header_text, separator, body = response_text.partition("\n\n")
    if not separator:
        return None

    lines = [line for line in header_text.splitlines() if line.strip()]
    if not lines:
        return None

    status_match = STATUS_PATTERN.match(lines[0].strip())
    if not status_match:
        return None

    status_code = int(status_match.group(1))
    reason_phrase = (status_match.group(2) or "").strip()
    header_items: list[tuple[str, str]] = []
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        header_items.append((normalized_key, normalized_value))
        if normalized_key == "set-cookie":
            continue
        if normalized_key in headers:
            headers[normalized_key] = f"{headers[normalized_key]}, {normalized_value}"
        else:
            headers[normalized_key] = normalized_value

    return ParsedHttpResponse(
        status_code=status_code,
        reason_phrase=reason_phrase,
        headers=headers,
        header_items=header_items,
        body=body,
    )


def perform_http_request(
    address: str,
    port: int,
    *,
    use_https: bool,
    timeout_seconds: float,
    path: str,
) -> tuple[ParsedHttpResponse | None, dict[str, Any]]:
    request = (
        f"GET {path} HTTP/1.0\r\n"
        f"Host: {address}\r\n"
        "User-Agent: AuthorizedNetworkAssessment\r\n"
        "Accept: text/html,application/xhtml+xml,application/xml,text/plain,*/*\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii", errors="ignore")

    tls_details: dict[str, Any] = {}
    try:
        with socket.create_connection((address, port), timeout=timeout_seconds) as raw_connection:
            raw_connection.settimeout(timeout_seconds)
            if use_https:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                with context.wrap_socket(raw_connection, server_hostname=address) as tls_connection:
                    tls_connection.settimeout(timeout_seconds)
                    tls_connection.sendall(request)
                    response_text = decode_bytes(read_socket_payload(tls_connection))
                    tls_details = inspect_tls_connection(
                        tls_connection,
                        target=address,
                    )
            else:
                raw_connection.sendall(request)
                response_text = decode_bytes(read_socket_payload(raw_connection))
    except OSError:
        return None, {}

    return parse_http_response(response_text), tls_details


def extract_page_title(body: str) -> str:
    title_match = TITLE_PATTERN.search(body)
    if not title_match:
        return ""
    return " ".join(title_match.group(1).split())[:180]


def technology_hints_from_headers(headers: dict[str, str]) -> list[str]:
    hints: list[str] = []
    for header in TECHNOLOGY_HINT_HEADERS:
        value = headers.get(header, "").strip()
        if value:
            hints.append(f"{header}: {value}")
    return hints


def parse_cookie_observations(
    header_items: list[tuple[str, str]],
    *,
    https_in_use: bool,
) -> list[CookieObservation]:
    observations: list[CookieObservation] = []
    for key, value in header_items:
        if key != "set-cookie":
            continue
        name_match = COOKIE_NAME_PATTERN.match(value)
        cookie_name = name_match.group(1) if name_match else "unnamed"
        lowered = value.lower()
        secure = "; secure" in lowered or lowered.endswith(" secure")
        http_only = "; httponly" in lowered or lowered.endswith(" httponly")
        same_site_match = SAMESITE_PATTERN.search(value)
        same_site = same_site_match.group(1).strip() if same_site_match else "Unset"

        missing_flags: list[str] = []
        if https_in_use and not secure:
            missing_flags.append("Secure")
        if not http_only:
            missing_flags.append("HttpOnly")
        if same_site.lower() == "unset":
            missing_flags.append("SameSite")

        observations.append(
            CookieObservation(
                name=cookie_name,
                secure=secure,
                http_only=http_only,
                same_site=same_site,
                missing_flags=missing_flags,
            )
        )
    return observations


def body_fingerprint(title: str, body: str) -> str:
    return f"{title} {body[:1200]}".lower()


def run_web_checks(
    address: str,
    port: int,
    *,
    use_https: bool,
    timeout_seconds: float,
) -> WebCheckResult | None:
    root_response, tls_details = perform_http_request(
        address,
        port,
        use_https=use_https,
        timeout_seconds=timeout_seconds,
        path="/",
    )
    if root_response is None or root_response.status_code is None:
        return None

    title = extract_page_title(root_response.body)
    fingerprint = body_fingerprint(title, root_response.body)
    server_header = root_response.headers.get("server", "")
    technology_hints = technology_hints_from_headers(root_response.headers)
    missing_headers = [
        header
        for header in SECURITY_HEADERS
        if header not in root_response.headers and (use_https or header != "strict-transport-security")
    ]
    cookies = parse_cookie_observations(
        root_response.header_items,
        https_in_use=use_https,
    )
    risky_cookies = [cookie for cookie in cookies if cookie.missing_flags]

    robots_response, _ = perform_http_request(
        address,
        port,
        use_https=use_https,
        timeout_seconds=timeout_seconds,
        path="/robots.txt",
    )
    sitemap_response, _ = perform_http_request(
        address,
        port,
        use_https=use_https,
        timeout_seconds=timeout_seconds,
        path="/sitemap.xml",
    )

    robots_detected = bool(
        robots_response and robots_response.status_code == 200
    )
    sitemap_detected = bool(
        sitemap_response
        and sitemap_response.status_code == 200
        and (
            "<urlset" in sitemap_response.body.lower()
            or "<sitemapindex" in sitemap_response.body.lower()
            or sitemap_response.headers.get("content-type", "").lower().startswith(("application/xml", "text/xml"))
        )
    )

    directory_listing_detected = any(
        hint in fingerprint for hint in DIRECTORY_LISTING_HINTS
    )
    default_page_detected = any(hint in fingerprint for hint in DEFAULT_PAGE_HINTS)
    login_page_detected = (
        any(hint in fingerprint for hint in LOGIN_HINTS)
        or 'type="password"' in root_response.body.lower()
        or "name=\"password\"" in root_response.body.lower()
    )
    is_admin_page = login_page_detected or any(
        hint in fingerprint for hint in ADMIN_HINTS
    )

    https_warnings: list[str] = []
    redirect_location = root_response.headers.get("location", "")
    if not use_https:
        https_warnings.append("Service responded over HTTP instead of HTTPS.")
        if not redirect_location.lower().startswith("https://"):
            https_warnings.append("No HTTP-to-HTTPS redirect was observed on the root request.")
    else:
        if tls_details.get("expired"):
            https_warnings.append("TLS certificate appears expired.")
        if tls_details.get("expiring_soon"):
            days_until_expiry = tls_details.get("days_until_expiry")
            if isinstance(days_until_expiry, int):
                https_warnings.append(
                    f"TLS certificate is expiring soon ({days_until_expiry} days remaining)."
                )
            else:
                https_warnings.append("TLS certificate is expiring soon.")
        if tls_details.get("self_signed"):
            https_warnings.append("TLS certificate appears self-signed.")
        if (
            tls_details.get("hostname_mismatch_detectable")
            and tls_details.get("hostname_mismatch")
        ):
            https_warnings.append("TLS certificate hostname does not match the scanned service name.")
        if "strict-transport-security" not in root_response.headers:
            https_warnings.append("Strict-Transport-Security header is missing on HTTPS.")

    return WebCheckResult(
        scheme="https" if use_https else "http",
        url=f"{'https' if use_https else 'http'}://{address}:{port}/",
        status_code=root_response.status_code,
        reason_phrase=root_response.reason_phrase,
        page_title=title,
        server_header=server_header,
        technology_hints=technology_hints,
        headers=root_response.headers,
        missing_security_headers=missing_headers,
        cookies=cookies,
        risky_cookies=risky_cookies,
        robots_txt_detected=robots_detected,
        sitemap_xml_detected=sitemap_detected,
        directory_listing_detected=directory_listing_detected,
        default_page_detected=default_page_detected,
        login_page_detected=login_page_detected,
        is_admin_page=is_admin_page,
        https_in_use=use_https,
        https_warnings=https_warnings,
        redirect_location=redirect_location,
        body_snippet=root_response.body[:800],
        tls=tls_details,
    )
