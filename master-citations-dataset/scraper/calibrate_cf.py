"""One-shot calibrator for the Cloudflare Turnstile checkbox position.

Workflow:
  1. Launch Chrome with --remote-debugging-port=9222 --window-size=1280,900
  2. Open https://indiankanoon.org/ — if CF doesn't trigger, open a /doc/ page.
  3. Wait until you see the Turnstile widget (the "Verify you are human" box).
  4. Run this script. It tries to auto-detect the iframe; if that fails, you
     enter (x, y) manually using Chrome DevTools to find the checkbox center.
  5. Result is saved to indiankanoon/cf_coords.json.

Once calibrated, scraper.py loads the coords and clicks automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from cloudflare_bypass import (  # noqa: E402
    CloudflareCoords,
    detect_turnstile_iframe_box,
    estimate_checkbox_coords,
    is_blocked,
)

COORDS_PATH = Path(__file__).parent / "cf_coords.json"


def prompt_manual_coords(viewport: dict) -> CloudflareCoords:
    print("\n  Auto-detect failed. Enter coords manually.")
    print("  In Chrome DevTools console run:")
    print("    document.querySelector('iframe').getBoundingClientRect()")
    print("  Then estimate the checkbox center (top-left of iframe + ~30, 33).")
    x = int(input("  Enter checkbox X (pixels from viewport left): ").strip())
    y = int(input("  Enter checkbox Y (pixels from viewport top): ").strip())
    return CloudflareCoords(
        x=x,
        y=y,
        viewport_width=int(viewport["width"]),
        viewport_height=int(viewport["height"]),
    )


def main() -> None:
    print("=" * 60)
    print("  Cloudflare Coords Calibrator")
    print("=" * 60)
    print()
    print("  Prerequisite: Chrome running with")
    print("    --remote-debugging-port=9222 --window-size=1280,900")
    print("  and a tab showing the CF 'Verify you are human' page.")
    print()
    input("  Press ENTER when Chrome is ready and the CF widget is visible... ")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        viewport = page.viewport_size or {"width": 1280, "height": 900}
        print(f"\n  Viewport: {viewport['width']}x{viewport['height']}")
        print(f"  Page title: {page.title()!r}")
        print(f"  Currently CF-blocked: {is_blocked(page)}")

        print("\n  Looking for Turnstile iframe...")
        box = detect_turnstile_iframe_box(page)
        if box is not None:
            print(f"    Found iframe at x={box['x']:.0f}, y={box['y']:.0f}, "
                  f"w={box['width']:.0f}, h={box['height']:.0f}")
            coords = estimate_checkbox_coords(page)
        else:
            print("    No Turnstile iframe found in the current page DOM.")
            coords = None

        if coords is None:
            coords = prompt_manual_coords(viewport)
        else:
            print(f"\n  Auto-estimated checkbox center: ({coords.x}, {coords.y})")
            override = input("  Accept? [Y/n] (n = enter manually): ").strip().lower()
            if override == "n":
                coords = prompt_manual_coords(viewport)

        coords.save(COORDS_PATH)
        print(f"\n  Saved to {COORDS_PATH}:")
        print(f"    x={coords.x}, y={coords.y}, "
              f"viewport={coords.viewport_width}x{coords.viewport_height}")


if __name__ == "__main__":
    main()
