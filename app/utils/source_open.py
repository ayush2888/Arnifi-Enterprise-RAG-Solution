"""Sanitize RAG source URLs so the portal Open button is safe to click."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_ALLOWED_HOST_SUFFIXES = (
    "arnifi.com",
    "drive.google.com",
    "docs.google.com",
    "chat.whatsapp.com",
    "wa.me",
)


def _host_allowed(host: str) -> bool:
    h = (host or "").lower().removeprefix("www.")
    if not h:
        return False
    return any(h == s or h.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES)


def sanitize_source_url(url: str | None) -> str | None:
    """
    Return a clean absolute http(s) URL, or None if it is not safe to open.

    - Rejects relative / javascript: / data: URLs (those resolve against the
      portal origin and can surface as SPA / Next-style application errors).
    - Normalizes Drive file links toward the public viewer form.
    """
    if not url or not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw or raw.startswith(("javascript:", "data:", "vbscript:")):
        return None
    if raw.startswith("whatsapp://"):
        return raw
    if not raw.lower().startswith(("http://", "https://")):
        # Relative paths like /product-details/... open on the portal host and
        # hit StaticFiles(html=True) ΓåÆ SPA shell / broken client routes.
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not _host_allowed(parsed.netloc):
        return None

    host = parsed.netloc.lower()
    path = parsed.path or ""
    # Prefer the Drive viewer URL (works in a new tab when the user has access).
    if "drive.google.com" in host and "/file/d/" in path:
        parts = [p for p in path.split("/") if p]
        try:
            idx = parts.index("d")
            file_id = parts[idx + 1]
        except (ValueError, IndexError):
            file_id = ""
        if file_id:
            q = dict(parse_qsl(parsed.query, keep_blank_values=True))
            q.setdefault("usp", "sharing")
            return urlunparse(
                (
                    "https",
                    "drive.google.com",
                    f"/file/d/{file_id}/view",
                    "",
                    urlencode(q),
                    "",
                )
            )

    # Strip trailing slash noise on Arnifi product pages (308 redirects).
    if "arnifi.com" in host and path.endswith("/") and "/product-details/" in path:
        path = path.rstrip("/")
        return urlunparse(
            (parsed.scheme, parsed.netloc, path, "", parsed.query, "")
        )
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, "")
    )


def enrich_openable_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Attach sanitized open_url / can_open flags for the portal Open button.

    Blogs and catalog pages (country / service / package) are one website family:
    normalize source_type ``blog`` ΓåÆ ``website`` for the UI.
    """
    out: list[dict[str, Any]] = []
    for src in sources:
        row = dict(src)
        st = str(row.get("source_type") or "").strip().lower()
        if st == "blog":
            row["source_type"] = "website"
            st = "website"

        original = row.get("source_url")
        clean = sanitize_source_url(str(original) if original else None)
        if clean:
            row["source_url"] = clean
            row["open_url"] = clean
            row["can_open"] = True
            if st == "drive" or "drive.google.com" in clean:
                row["open_note"] = (
                    "Google Drive link ΓÇö you need access to the shared Pricing file."
                )
            elif "arnifi.com" in clean:
                # Prefer in-portal preview: live product-details pages often
                # crash in Arnifi's Next.js with Digest 2529575321.
                row["use_portal_preview"] = True
                row["open_note"] = (
                    "Public Arnifi page. Live product pages can show "
                    "Application error Digest 2529575321 (Arnifi.com Next.js) ΓÇö "
                    "use Open source for indexed text; Try live website is optional."
                )
        else:
            row["can_open"] = False
            row["open_url"] = None
            row["open_note"] = (
                "This source has no public web link. Use the snippet in the chat."
            )
        out.append(row)
    return out
