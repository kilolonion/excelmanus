"""Isolated, network-disabled workbench renderer. Invoked with fixed file inputs."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys


def main(arguments: list[str] | None = None) -> None:
    source, output, metadata = map(Path, arguments or sys.argv[1:])
    observation = json.loads(source.read_text(encoding="utf-8"))
    geometry = observation["regions"][0].get("geometry", {})
    width = math.ceil(geometry.get("width_px", 1000)) + 120
    height = math.ceil(geometry.get("height_px", 700)) + 120
    if width * height > 16_000_000 or max(width, height) > 8192:
        raise ValueError("Region too large to preview; request smaller tiles")
    from playwright.sync_api import sync_playwright

    assets = Path(__file__).with_name("render_assets")
    with sync_playwright() as runtime:
        # executable_path describes headed Chromium, even when launch(headless=True)
        # selects chromium_headless_shell. Let Playwright validate the executable
        # it actually launches so shell-only desktop installations work offline.
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": max(640, width), "height": max(480, height)},
                device_scale_factor=1,
            )
            page.set_default_timeout(20000)
            page.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if route.request.url.startswith(("file:", "data:", "blob:"))
                    else route.abort()
                ),
            )
            page.goto((assets / "workbench.html").as_uri())
            page.wait_for_function("typeof window.renderObservation === 'function'")
            result = page.evaluate(
                "async observation => window.renderObservation(observation)",
                observation,
            )
            clip = result["clip"]
            if (
                any(
                    not math.isfinite(clip[k]) or clip[k] <= 0
                    for k in ("width", "height")
                )
                or clip["width"] * clip["height"] > 16_000_000
                or max(clip["width"], clip["height"]) > 8192
            ):
                raise ValueError(
                    "Rendered merge/object extent exceeds the preview budget; request a smaller range"
                )
            page.screenshot(path=str(output), clip=clip, animations="disabled")
            result["engine"] = "univer-workbench"
            result["browser_version"] = browser.version
            metadata.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Workbook renderer: {exc}", file=sys.stderr)
        raise SystemExit(1)
