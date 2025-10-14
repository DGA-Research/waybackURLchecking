#!/usr/bin/env python3
"""Check whether tweet URLs listed in an Excel spreadsheet are still publicly accessible.

The script reads an Excel workbook, extracts tweet IDs from the supplied URL column,
queries Twitter's unauthenticated syndication endpoint, and writes the results to a new file.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import pandas as pd
import requests


# Matches twitter.com or x.com status URLs and captures the numeric tweet ID.
TWEET_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:twitter|x)\.com/.+?/status(?:es)?/(\d+)")


def extract_tweet_id(url: str) -> Optional[str]:
    """Return the tweet ID embedded in the URL, or None when it cannot be found."""
    if not isinstance(url, str):
        return None
    match = TWEET_URL_RE.search(url)
    return match.group(1) if match else None


def normalize_input_url(raw_url: str) -> Optional[str]:
    """Normalize the incoming tweet URL so it can be used for lookups."""
    if not isinstance(raw_url, str):
        return None
    trimmed = raw_url.strip()
    if not trimmed:
        return None
    if trimmed.startswith("//"):
        trimmed = "https:" + trimmed
    if not trimmed.lower().startswith(("http://", "https://")):
        trimmed = "https://" + trimmed

    parsed = urlparse(trimmed)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    allowed_hosts = {
        "twitter.com",
        "mobile.twitter.com",
        "m.twitter.com",
        "x.com",
        "mobile.x.com",
    }
    if host not in allowed_hosts:
        return None

    # Drop query/fragment noise to keep canonical shape.
    return urlunparse(("https", host, parsed.path, "", "", ""))


def convert_to_x_domain(url: str, keep_query: bool = False) -> str:
    """Ensure the URL uses the x.com domain, optionally preserving its query string."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("twitter.com"):
        host = "x.com"
    elif host.endswith("mobile.twitter.com") or host.endswith("m.twitter.com"):
        host = "x.com"
    elif host.endswith(".x.com"):
        host = "x.com"
    elif host != "x.com":
        host = "x.com"

    query = parsed.query if keep_query else ""
    return urlunparse(("https", host, parsed.path, "", query, ""))


def build_fetch_urls(tweet_id: str, normalized_url: str, canonical_url: Optional[str]) -> List[str]:
    """Return a list of candidate URLs to try when fetching the tweet content."""
    seeds = []
    if canonical_url:
        seeds.append(canonical_url)
    if normalized_url:
        seeds.append(normalized_url)

    seeds.extend(
        [
            f"https://x.com/i/web/status/{tweet_id}",
            f"https://twitter.com/i/web/status/{tweet_id}",
            f"https://x.com/i/status/{tweet_id}",
            f"https://twitter.com/i/status/{tweet_id}",
        ]
    )

    seen = set()
    candidates: List[str] = []

    for raw in seeds:
        if not raw:
            continue
        # Ensure the seed has a scheme before parsing.
        if raw.startswith("//"):
            raw = "https:" + raw
        elif not raw.lower().startswith(("http://", "https://")):
            raw = "https://" + raw.lstrip("/")

        parsed = urlparse(raw)
        base = urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path, "", "", ""))
        normalized_base = normalize_input_url(base)
        if normalized_base:
            x_url = convert_to_x_domain(normalized_base, keep_query=False)
            if x_url not in seen:
                seen.add(x_url)
                candidates.append(x_url)

        if parsed.query:
            # Retain the query string for another attempt.
            query_url = urlunparse(
                (parsed.scheme or "https", parsed.netloc, parsed.path, "", parsed.query, "")
            )
            normalized_query = normalize_input_url(query_url)
            if normalized_query:
                x_query_url = convert_to_x_domain(query_url, keep_query=True)
                if x_query_url not in seen:
                    seen.add(x_query_url)
                    candidates.append(x_query_url)

    return candidates


def check_tweet(
    tweet_id: str,
    original_url: str,
    oembed_session: requests.Session,
    bot_session: requests.Session,
    timeout: float,
) -> Dict[str, Optional[str]]:
    """Check whether the tweet is still accessible using oEmbed and a Twitterbot fetch."""
    normalized_input = normalize_input_url(original_url)
    if not normalized_input:
        return {
            "availability": "invalid_url",
            "http_status": None,
            "detail": "URL could not be normalized",
            "oembed_status": None,
            "checked_url": None,
        }

    endpoint = "https://publish.twitter.com/oembed"
    params = {"url": normalized_input}
    try:
        response = oembed_session.get(endpoint, params=params, timeout=timeout)
    except requests.RequestException as exc:
        return {
            "availability": "error",
            "http_status": None,
            "detail": f"oEmbed request failed: {exc}",
            "oembed_status": None,
            "checked_url": None,
        }

    oembed_status = response.status_code
    availability: str
    detail: Optional[str]

    status_code = response.status_code
    canonical_url: Optional[str] = None
    if status_code == 200:
        availability = "public"
        detail = None
        try:
            canonical_url = response.json().get("url")
        except ValueError as exc:
            detail = f"Failed to parse oEmbed payload: {exc}"
    elif status_code in (401, 403):
        return {
            "availability": "restricted",
            "http_status": status_code,
            "detail": "Tweet requires authentication or is protected",
            "oembed_status": oembed_status,
            "checked_url": None,
        }
    elif status_code == 404:
        return {
            "availability": "not_found",
            "http_status": status_code,
            "detail": "Tweet not found or removed",
            "oembed_status": oembed_status,
            "checked_url": None,
        }
    elif status_code == 429:
        return {
            "availability": "rate_limited",
            "http_status": status_code,
            "detail": "Rate limited by Twitter oEmbed endpoint",
            "oembed_status": oembed_status,
            "checked_url": None,
        }
    else:
        return {
            "availability": "unknown",
            "http_status": status_code,
            "detail": f"Unexpected {status_code} response from oEmbed",
            "oembed_status": oembed_status,
            "checked_url": None,
        }

    fetch_urls = build_fetch_urls(tweet_id, normalized_input, canonical_url)
    last_status: Optional[int] = None
    last_detail: Optional[str] = None
    last_url: Optional[str] = None
    last_exception: Optional[str] = None

    for candidate_url in fetch_urls:
        try:
            tweet_response = bot_session.get(candidate_url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            last_exception = str(exc)
            continue

        fetch_status = tweet_response.status_code
        last_status = fetch_status
        last_url = candidate_url

        if fetch_status == 200:
            return {
                "availability": "public",
                "http_status": fetch_status,
                "detail": None,
                "oembed_status": oembed_status,
                "checked_url": candidate_url,
            }

        if fetch_status in (401, 403):
            return {
                "availability": "restricted",
                "http_status": fetch_status,
                "detail": "Tweet requires authentication or is protected",
                "oembed_status": oembed_status,
                "checked_url": candidate_url,
            }

        if fetch_status == 410:
            return {
                "availability": "gone",
                "http_status": fetch_status,
                "detail": "Tweet removed (410)",
                "oembed_status": oembed_status,
                "checked_url": candidate_url,
            }

        if fetch_status == 429:
            return {
                "availability": "rate_limited",
                "http_status": fetch_status,
                "detail": "Rate limited while fetching tweet",
                "oembed_status": oembed_status,
                "checked_url": candidate_url,
            }

        if fetch_status == 451:
            return {
                "availability": "unavailable_legal",
                "http_status": fetch_status,
                "detail": "Tweet unavailable due to legal demand",
                "oembed_status": oembed_status,
                "checked_url": candidate_url,
            }

        if fetch_status == 404:
            last_detail = (
                "Tweet page returned 404 despite oEmbed success" if status_code == 200 else "Tweet not found or removed"
            )
            # continue to try additional candidates
            continue

        last_detail = f"Unexpected {fetch_status} response while fetching tweet"

    if last_status is None:
        return {
            "availability": "error",
            "http_status": None,
            "detail": f"Direct fetch failed: {last_exception}" if last_exception else "Direct fetch failed",
            "oembed_status": oembed_status,
            "checked_url": None,
        }

    return {
        "availability": "not_found" if last_status == 404 else "unknown",
        "http_status": last_status,
        "detail": last_detail,
        "oembed_status": oembed_status,
        "checked_url": last_url,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether tweet URLs in an Excel sheet are still publicly accessible."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to the source Excel workbook containing tweet URLs.",
    )
    parser.add_argument(
        "--url-column",
        default="URL",
        help="Name of the column that contains tweet URLs (default: URL).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file path. Defaults to <input>_checked.xlsx next to the input file.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Number of seconds to wait for each HTTP request (default: 10).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to pause between requests to avoid rate limits (default: 0.5).",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Print a progress update after this many rows (set to 0 to disable).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if not args.input.exists():
        parser.error(f"Input file not found: {args.input}")

    try:
        df = pd.read_excel(args.input)
    except Exception as exc:  # pylint: disable=broad-except
        parser.error(f"Unable to read Excel file ({exc})")

    if args.url_column not in df.columns:
        parser.error(f"Column '{args.url_column}' not found in spreadsheet. Available: {list(df.columns)}")

    total_rows = len(df)
    print(f"Processing {total_rows} rows from {args.input}...")

    oembed_session = requests.Session()
    bot_session = requests.Session()
    bot_session.headers.update(
        {"User-Agent": "Mozilla/5.0 (compatible; Twitterbot/1.0; +https://twitter.com/twitterbot)"}
    )
    cache: Dict[str, Dict[str, Optional[str]]] = {}
    results = []
    checked_at = datetime.now(timezone.utc).isoformat()

    for index, url in enumerate(df[args.url_column], start=1):
        tweet_id = extract_tweet_id(url)
        if not tweet_id:
            results.append(
                {
                    "tweet_id": None,
                    "availability": "invalid_url",
                    "http_status": None,
                    "detail": "Tweet ID not found in URL",
                    "checked_at": checked_at,
                    "oembed_status": None,
                    "checked_url": None,
                }
            )
            continue

        if tweet_id not in cache:
            cache[tweet_id] = check_tweet(tweet_id, url, oembed_session, bot_session, args.timeout)
            time.sleep(args.sleep)

        results.append(
            {
                "tweet_id": tweet_id,
                "availability": cache[tweet_id]["availability"],
                "http_status": cache[tweet_id]["http_status"],
                "detail": cache[tweet_id]["detail"],
                "checked_at": checked_at,
                "oembed_status": cache[tweet_id].get("oembed_status"),
                "checked_url": cache[tweet_id].get("checked_url"),
            }
        )

        if args.progress_every and index % args.progress_every == 0:
            print(f"Checked {index}/{total_rows} rows...")

    output_df = df.copy()
    results_df = pd.DataFrame(results)
    combined = pd.concat([output_df.reset_index(drop=True), results_df], axis=1)

    output_path = args.output
    if output_path is None:
        output_path = args.input.with_name(f"{args.input.stem}_checked.xlsx")

    try:
        combined.to_excel(output_path, index=False)
    except Exception as exc:  # pylint: disable=broad-except
        parser.error(f"Failed to write output file ({exc})")

    print(f"Wrote results to {output_path}")
    availability_counts = combined["availability"].value_counts(dropna=False).to_dict()
    print(f"Availability summary: {availability_counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
