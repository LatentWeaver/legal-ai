"""Cloudflare bypass for Indian Kanoon browser scraper.

Three-tier strategy:
  1. Passive wait — managed challenges auto-resolve in ~5-15s
  2. Coord click — for interactive Turnstile checkboxes (uses pre-calibrated coords
     because the Turnstile widget lives in a cross-origin iframe whose checkbox
     position is layout-dependent)
  3. Manual fallback — user passes the challenge in the browser tab

Coords are captured once via calibrate_cf.py and cached to cf_coords.json.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

BypassResult = Literal["auto_passed", "manual_passed", "still_blocked"]

# Indian Kanoon serves both the English and traditional-Chinese CF interstitial.
BLOCKED_TITLE_MARKERS: tuple[str, ...] = ("Just a moment", "請稍候")

TURNSTILE_SELECTORS: tuple[str, ...] = (
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="cdn-cgi/challenge-platform"]',
    'iframe[title*="Cloudflare"]',
    'iframe[title*="verify you are human"]',
)

# Turnstile checkbox sits ~30px right, ~33px down from iframe top-left.
CHECKBOX_OFFSET_X = 30
CHECKBOX_OFFSET_Y = 33


@dataclass(frozen=True)
class CloudflareCoords:
    """Calibrated coords of the Turnstile checkbox center.

    Viewport dimensions are stored so the scraper can warn if runtime viewport
    differs from calibration (coords are viewport-relative).
    """

    x: int
    y: int
    viewport_width: int
    viewport_height: int

    @classmethod
    def load(cls, path: Path) -> "CloudflareCoords | None":
        if not path.exists():
            return None
        with path.open() as f:
            data = json.load(f)
        return cls(**data)

    def save(self, path: Path) -> None:
        with path.open("w") as f:
            json.dump(asdict(self), f, indent=2)


def is_blocked(page: Page) -> bool:
    """Return True if the page currently shows a Cloudflare interstitial."""
    try:
        title = page.title()
    except PlaywrightError:
        return False
    return any(marker in title for marker in BLOCKED_TITLE_MARKERS)


def detect_turnstile_iframe_box(page: Page) -> dict | None:
    """Return bounding box dict {x, y, width, height} of the Turnstile iframe, if present."""
    for selector in TURNSTILE_SELECTORS:
        element = page.query_selector(selector)
        if element is None:
            continue
        box = element.bounding_box()
        if box is not None:
            return box
    return None


def estimate_checkbox_coords(page: Page) -> CloudflareCoords | None:
    """Auto-estimate checkbox center from Turnstile iframe bounding box."""
    box = detect_turnstile_iframe_box(page)
    if box is None:
        return None
    viewport = page.viewport_size or {"width": 1280, "height": 900}
    return CloudflareCoords(
        x=int(box["x"]) + CHECKBOX_OFFSET_X,
        y=int(box["y"]) + CHECKBOX_OFFSET_Y,
        viewport_width=int(viewport["width"]),
        viewport_height=int(viewport["height"]),
    )


def wait_for_turnstile_iframe(page: Page, timeout_s: float = 10.0) -> dict | None:
    """Poll the DOM until the Turnstile iframe appears (CF JS is async)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        box = detect_turnstile_iframe_box(page)
        if box is not None:
            return box
        page.wait_for_timeout(500)
    return None


def _calibrate_inline(page: Page) -> CloudflareCoords | None:
    """Calibrate coords against the live CF challenge currently on screen.

    Tries auto-detect first; falls back to a manual prompt. Returns None if
    calibration is impossible (user aborts or can't find the widget).
    """
    print("    No saved coords — calibrating from this CF challenge...")

    box = wait_for_turnstile_iframe(page, timeout_s=10)
    viewport = page.viewport_size or {"width": 1280, "height": 900}

    if box is not None:
        proposed = CloudflareCoords(
            x=int(box["x"]) + CHECKBOX_OFFSET_X,
            y=int(box["y"]) + CHECKBOX_OFFSET_Y,
            viewport_width=int(viewport["width"]),
            viewport_height=int(viewport["height"]),
        )
        print(
            f"    Auto-detected iframe at ({box['x']:.0f}, {box['y']:.0f}) "
            f"size {box['width']:.0f}x{box['height']:.0f}"
        )
        print(f"    Proposed checkbox center: ({proposed.x}, {proposed.y})")
        accept = input("    Use these coords? [Y/n]: ").strip().lower()
        if accept != "n":
            return proposed
    else:
        print("    Could not auto-detect Turnstile iframe within 10s.")

    print("    Enter coords manually. In Chrome DevTools console:")
    print("      document.querySelector('iframe').getBoundingClientRect()")
    print("    then add ~30, ~33 to (x, y) to get checkbox center.")
    try:
        x = int(input("    Checkbox X: ").strip())
        y = int(input("    Checkbox Y: ").strip())
    except (ValueError, KeyboardInterrupt):
        return None
    return CloudflareCoords(
        x=x,
        y=y,
        viewport_width=int(viewport["width"]),
        viewport_height=int(viewport["height"]),
    )


def _passive_wait(page: Page, max_seconds: float = 25.0, poll: float = 2.0) -> bool:
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        if not is_blocked(page):
            return True
        page.wait_for_timeout(int(poll * 1000))
    return not is_blocked(page)


def _coord_click(page: Page, coords: CloudflareCoords) -> bool:
    # CF gates the click on widget being fully rendered.
    page.wait_for_timeout(3000)

    viewport = page.viewport_size
    if viewport is not None and (
        viewport["width"] != coords.viewport_width
        or viewport["height"] != coords.viewport_height
    ):
        print(
            f"    WARNING: viewport ({viewport['width']}x{viewport['height']}) "
            f"differs from calibration "
            f"({coords.viewport_width}x{coords.viewport_height}). "
            f"Coords may miss the checkbox."
        )

    # Trusted events via CDP-attached Chrome; two-segment approach with steps for human-like motion.
    page.mouse.move(max(0, coords.x - 100), max(0, coords.y - 50), steps=10)
    page.wait_for_timeout(120)
    page.mouse.move(coords.x, coords.y, steps=20)
    page.wait_for_timeout(80)
    page.mouse.click(coords.x, coords.y, delay=80)

    for _ in range(15):
        page.wait_for_timeout(2000)
        if not is_blocked(page):
            return True
    return False


def _manual_fallback(page: Page) -> bool:
    print("    Auto-bypass failed. Pass Cloudflare manually in the browser tab.")
    input("    Press ENTER after you've passed it... ")
    return not is_blocked(page)


def bypass(page: Page, coords_path: Path) -> BypassResult:
    """Run the three-tier Cloudflare bypass with JIT coord calibration.

    If no coords are saved at coords_path on first CF block, this function
    auto-detects (or prompts) and saves them before clicking. Subsequent
    blocks reuse the saved coords without prompting.
    """
    if not is_blocked(page):
        return "auto_passed"

    print("    Cloudflare detected. Tier 1: passive wait (up to 25s)...")
    if _passive_wait(page):
        print("    Tier 1 cleared.")
        return "auto_passed"

    coords = CloudflareCoords.load(coords_path)
    if coords is None:
        coords = _calibrate_inline(page)
        if coords is not None:
            coords.save(coords_path)
            print(f"    Saved coords to {coords_path} for future use.")

    if coords is not None:
        print(f"    Tier 2: clicking checkbox at ({coords.x}, {coords.y})...")
        if _coord_click(page, coords):
            print("    Tier 2 cleared.")
            return "auto_passed"
        print("    Tier 2 failed.")

    if _manual_fallback(page):
        return "manual_passed"
    return "still_blocked"
