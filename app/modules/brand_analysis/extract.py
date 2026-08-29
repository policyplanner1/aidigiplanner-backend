"""Best-effort signal extraction from raw HTML -- regex tag-stripping, not a
new HTML-parsing dependency (matches this codebase's lean-dependency style).
Nothing here is precise; it exists to give the Gemini call in
app.modules.brand_analysis.service something better than raw markup to read,
plus a couple of structured candidates (logo URL, social links) that are
cheap to find deterministically and not worth asking an LLM to guess."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from app.models.enums import SocialPlatform

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESCRIPTION_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE
)
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE
)
_ICON_LINK_RE = re.compile(
    r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]+href=["\'](.*?)["\']', re.IGNORECASE
)
_MAILTO_RE = re.compile(r'mailto:([^"\'\s?]+)', re.IGNORECASE)
_TEL_RE = re.compile(r'tel:([^"\'\s]+)', re.IGNORECASE)
_HREF_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)

_SOCIAL_DOMAINS: dict[str, SocialPlatform] = {
    "instagram.com": SocialPlatform.instagram,
    "facebook.com": SocialPlatform.facebook,
    "youtube.com": SocialPlatform.youtube,
    "twitter.com": SocialPlatform.twitter,
    "x.com": SocialPlatform.twitter,
    "linkedin.com": SocialPlatform.linkedin,
}

_MAX_TEXT_CHARS = 8000


@dataclass
class ExtractedSite:
    title: str = ""
    meta_description: str = ""
    visible_text: str = ""
    logo_candidate_url: str | None = None
    social_links: dict[str, str] = field(default_factory=dict)
    contact_email: str | None = None
    contact_number: str | None = None


def _first_match(pattern: re.Pattern[str], html: str) -> str | None:
    m = pattern.search(html)
    return m.group(1).strip() if m else None


def _visible_text(html: str) -> str:
    stripped = _SCRIPT_STYLE_RE.sub(" ", html)
    stripped = _TAG_RE.sub(" ", stripped)
    return _WHITESPACE_RE.sub(" ", stripped).strip()[:_MAX_TEXT_CHARS]


def extract_site_signals(html: str, base_url: str) -> ExtractedSite:
    title = _first_match(_TITLE_RE, html) or ""
    description = _first_match(_META_DESCRIPTION_RE, html) or ""

    logo_url = _first_match(_OG_IMAGE_RE, html) or _first_match(_ICON_LINK_RE, html)
    if logo_url:
        logo_url = urljoin(base_url, logo_url)

    social_links: dict[str, str] = {}
    for href in _HREF_RE.findall(html):
        for domain, platform in _SOCIAL_DOMAINS.items():
            if domain in href and platform.value not in social_links:
                social_links[platform.value] = href

    return ExtractedSite(
        title=_WHITESPACE_RE.sub(" ", title).strip(),
        meta_description=_WHITESPACE_RE.sub(" ", description).strip(),
        visible_text=_visible_text(html),
        logo_candidate_url=logo_url,
        social_links=social_links,
        contact_email=_first_match(_MAILTO_RE, html),
        contact_number=_first_match(_TEL_RE, html),
    )
