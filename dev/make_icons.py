"""Draw the web view's icons from docs/assets/ideamachine-mark-*.svg: python3 dev/make_icons.py

Writes src/im/static/: icon.svg (the mark, light or dark with the system theme, for the tab) and the PNGs a phone
installs (the dark mark on a dark square, inside the central 76% so iOS's rounded mask and Android's maskable crop
never cut it). Dev only: needs Playwright's Chromium to rasterize, which the package doesn't depend on.
"""

import re
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LIGHT = (ROOT / "docs/assets/ideamachine-mark-light.svg").read_text()
DARK = (ROOT / "docs/assets/ideamachine-mark-dark.svg").read_text()
OUT = ROOT / "src/im/static"
BG = "#1A1918"
PNGS = {"icon-192.png": 192, "icon-512.png": 512, "apple-touch-icon.png": 180}


def adaptive() -> str:
    """The light mark, recoloured by class so a media query can switch it to the dark mark's colours."""
    body = LIGHT.replace('"#1A1918"', '"currentColor"').replace('fill="#E09A00"', 'class="core"')
    style = ("<style>svg{color:#1A1918}.core{fill:#E09A00}"
             "@media (prefers-color-scheme: dark){svg{color:#E8E4DA}.core{fill:#FFD15C}}</style>")
    return re.sub(r"(<svg[^>]*>)", r"\1" + style, body, count=1)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "icon.svg").write_text(adaptive())
    mark = re.sub(r'\s(width|height)="\d+"', "", DARK, count=2)
    with sync_playwright() as p:
        # A Chromium that isn't the one this Playwright version expects: IM_CHROMIUM=/path/to/chrome.
        browser = p.chromium.launch(executable_path=os.environ.get("IM_CHROMIUM"))
        for name, n in PNGS.items():
            page = browser.new_page(viewport={"width": n, "height": n})
            page.set_content(f'<body style="margin:0;background:{BG};display:grid;place-items:center;height:{n}px">'
                             f'<div style="width:{0.76 * n}px;height:{0.76 * n}px">{mark}</div></body>')
            page.locator("body").screenshot(path=str(OUT / name))
        browser.close()


if __name__ == "__main__":
    main()
