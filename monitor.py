#!/usr/bin/env python3
"""Monitor selected Jack Stonehouse product pages and alert Discord on restocks.

Uses only Python's standard library so it runs at no cost in GitHub Actions.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CONFIG_PATH = Path("config.json")
STATE_PATH = Path("state.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

OUT_OF_STOCK_PATTERNS = [
    r"\bout of stock\b",
    r"\bsold out\b",
    r"\bcurrently unavailable\b",
    r"\bemail me when back in stock\b",
    r"\bnotify me when available\b",
]

IN_STOCK_PATTERNS = [
    r"\badd to basket\b",
    r"\badd to bag\b",
    r"\badd to cart\b",
    r'"availability"\s*:\s*"https?://schema\.org/InStock"',
    r'"availability"\s*:\s*"InStock"',
]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch_page(url: str, attempts: int = 3) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise RuntimeError(f"HTTP {status}")
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(3 * attempt)

    raise RuntimeError(f"Could not fetch page after {attempts} attempts: {last_error}")


def strip_visible_text(page_html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", page_html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip().lower()


def detect_stock(page_html: str) -> tuple[str, str]:
    """Return (status, evidence), where status is in_stock/out_of_stock/unknown."""
    lower_html = page_html.lower()
    visible_text = strip_visible_text(page_html)

    # Structured data is normally the strongest signal.
    if re.search(r'"availability"\s*:\s*"https?://schema\.org/OutOfStock"', lower_html, re.I):
        return "out_of_stock", "Structured data says OutOfStock"
    if re.search(r'"availability"\s*:\s*"https?://schema\.org/InStock"', lower_html, re.I):
        return "in_stock", "Structured data says InStock"

    # A visible out-of-stock marker takes precedence over generic buttons elsewhere on the page.
    for pattern in OUT_OF_STOCK_PATTERNS:
        if re.search(pattern, visible_text, re.I):
            return "out_of_stock", f"Page contains: {pattern.replace('\\b', '')}"

    for pattern in IN_STOCK_PATTERNS:
        if re.search(pattern, lower_html if 'availability' in pattern else visible_text, re.I):
            return "in_stock", f"Page contains an active purchase signal"

    return "unknown", "No reliable stock marker found"


def discord_post(webhook_url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        if getattr(response, "status", 204) not in (200, 204):
            raise RuntimeError(f"Discord webhook returned HTTP {response.status}")


def send_restock_alert(webhook_url: str, product: dict[str, str], previous: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    change = "Newly detected in stock" if previous in ("", "unknown") else "Back in stock"
    payload = {
        "username": "Jack Stonehouse Monitor",
        "embeds": [
            {
                "title": "🚨 Jack Stonehouse Restock",
                "url": product["url"],
                "description": f"**{product['name']}**",
                "color": 5763719,
                "fields": [
                    {"name": "Status", "value": f"🟢 {change}", "inline": True},
                    {"name": "Previous status", "value": previous.replace("_", " ").title() or "Unknown", "inline": True},
                    {"name": "Product page", "value": f"[Open and buy]({product['url']})", "inline": False},
                ],
                "footer": {"text": "Checked automatically by GitHub Actions"},
                "timestamp": now,
            }
        ],
    }
    discord_post(webhook_url, payload)


def send_test_alert(webhook_url: str) -> None:
    discord_post(
        webhook_url,
        {
            "content": "✅ Test successful — your cloud Jack Stonehouse monitor can send Discord alerts."
        },
    )


def main() -> int:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL is not configured.", file=sys.stderr)
        return 1

    if os.environ.get("TEST_NOTIFICATION", "false").lower() == "true":
        send_test_alert(webhook_url)
        print("Test notification sent.")
        return 0

    config = load_json(CONFIG_PATH, {})
    products = config.get("products", [])
    if not products:
        print("ERROR: config.json contains no products.", file=sys.stderr)
        return 1

    old_state = load_json(STATE_PATH, {"products": {}})
    old_products = old_state.get("products", {})
    new_products: dict[str, Any] = {}
    alerts = 0
    errors = 0

    for index, product in enumerate(products, start=1):
        product_id = product["id"]
        previous = old_products.get(product_id, {}).get("status", "")
        try:
            page_html = fetch_page(product["url"])
            status, evidence = detect_stock(page_html)
            print(f"[{index}/{len(products)}] {product['name']}: {status} ({evidence})")

            if status == "in_stock" and previous != "in_stock":
                send_restock_alert(webhook_url, product, previous)
                alerts += 1

            new_products[product_id] = {
                "name": product["name"],
                "url": product["url"],
                "status": status,
                "evidence": evidence,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        except Exception as exc:  # keep checking the remaining URLs
            errors += 1
            print(f"[{index}/{len(products)}] ERROR {product['name']}: {exc}", file=sys.stderr)
            # Preserve the prior state after a transient request failure.
            new_products[product_id] = old_products.get(
                product_id,
                {
                    "name": product["name"],
                    "url": product["url"],
                    "status": "unknown",
                    "evidence": f"Check failed: {exc}",
                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )

    save_json(
        STATE_PATH,
        {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "products": new_products,
        },
    )
    print(f"Complete: {len(products)} products checked, {alerts} alert(s), {errors} error(s).")

    # Fail only if every product failed, so GitHub flags a completely broken run.
    return 1 if errors == len(products) else 0


if __name__ == "__main__":
    raise SystemExit(main())
