from __future__ import annotations

import re

from langchain_core.tools import tool
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
WHITESPACE_PATTERN = re.compile(r"\s+")
NAVIGATION_ATTEMPTS = 2


async def auto_scroll(page: Page, step: int = 900, pause_ms: int = 300) -> None:
    await page.evaluate(
        """
        async ({ step, pauseMs }) => {
            await new Promise((resolve) => {
                let totalHeight = 0;
                let previousHeight = -1;

                const timer = setInterval(() => {
                    const scrollHeight = Math.max(
                        document.body.scrollHeight,
                        document.documentElement.scrollHeight
                    );

                    window.scrollBy(0, step);
                    totalHeight += step;

                    if (totalHeight >= scrollHeight || scrollHeight === previousHeight) {
                        clearInterval(timer);
                        window.scrollTo(0, 0);
                        resolve();
                    }

                    previousHeight = scrollHeight;
                }, pauseMs);
            });
        }
        """,
        {"step": step, "pauseMs": pause_ms},
    )
    await page.wait_for_timeout(1500)


async def launch_page(url: str) -> tuple[Playwright, Browser, BrowserContext, Page]:
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()

    try:
        last_error: Exception | None = None
        for attempt_index in range(NAVIGATION_ATTEMPTS):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2000)
                try:
                    await auto_scroll(page)
                except Exception:
                    # Scrolling is best-effort; keep scrape alive if this fails.
                    pass
                await page.wait_for_timeout(1500)
                return playwright, browser, context, page
            except Exception as navigation_error:
                last_error = navigation_error
                if attempt_index < NAVIGATION_ATTEMPTS - 1:
                    await page.wait_for_timeout(1200)
                    continue
        if last_error is not None:
            raise last_error
    except Exception:
        await close_page(playwright, browser, context)
        raise



async def close_page(playwright: Playwright, browser: Browser, context: BrowserContext) -> None:
    await context.close()
    await browser.close()
    await playwright.stop()


def _sanitize_text(text_value: str) -> str:
    # Strip control bytes and collapse whitespace before values leave the scraper.
    without_controls = CONTROL_CHARACTER_PATTERN.sub("", text_value)
    collapsed_whitespace = WHITESPACE_PATTERN.sub(" ", without_controls)
    return collapsed_whitespace.strip()


def _string_value(value: object) -> str:
    if isinstance(value, str):
        return _sanitize_text(value)
    return ""


def _block_list_value(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    blocks: list[dict[str, str]] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        blocks.append(
            {
                "tag": _string_value(block.get("tag")),
                "text": _string_value(block.get("text")),
                "html_marker": _string_value(block.get("html_marker")),
            }
        )
    return blocks


async def _extract_text_bundle(url: str) -> dict[str, object]:
    try:
        playwright, browser, context, page = await launch_page(url)
    except Exception as scrape_error:
        return {
            "final_url": _string_value(url),
            "body_text": "",
            "body_text_blocks": [],
            "error": _string_value(str(scrape_error)),
        }
    try:
        data = await page.evaluate(
            f"""
            () => {{
                const body = document.body;
                const MAX_INLINE_LINK_LENGTH = 600;

                const clean = (value) => (value || "")
                    .replace(/[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F]/g, "")
                    .replace(/\\s+/g, " ")
                    .trim();

                const isVisible = (el) => {{
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== "none" &&
                           style.visibility !== "hidden" &&
                           rect.width > 0 &&
                           rect.height > 0;
                }};

                const abs = (href) => {{
                    try {{ return new URL(href, window.location.href).href; }}
                    catch {{ return null; }}
                }};

                const inlineText = (node) => {{
                    if (!node) return "";

                    if (node.nodeType === Node.TEXT_NODE) {{
                        return clean(node.textContent);
                    }}

                    if (node.nodeType !== Node.ELEMENT_NODE) {{
                        return "";
                    }}

                    const el = node;
                    if (!isVisible(el)) return "";

                    const tag = el.tagName.toLowerCase();
                    if (tag === "script" || tag === "style" || tag === "noscript") {{
                        return "";
                    }}

                    if (tag === "a") {{
                        const text = clean(el.innerText);
                        const href = abs(el.getAttribute("href"));

                        if (href && href.length <= MAX_INLINE_LINK_LENGTH && text) {{
                            return `[${{text}}](${{href}})`;
                        }}

                        if (text) return text;
                        return "";
                    }}

                    const parts = [];
                    for (const child of el.childNodes) {{
                        const value = inlineText(child);
                        if (value) parts.push(value);
                    }}
                    return clean(parts.join(" "));
                }};

                const extractTextBlocks = (root) => {{
                    if (!root) return [];

                    const selectors = "h1,h2,h3,h4,h5,h6,article,section,li,p,div,tr,td";
                    const rawBlocks = [...root.querySelectorAll(selectors)]
                        .filter(isVisible)
                        .map(el => {{
                            const tag = el.tagName.toLowerCase();
                            const text = inlineText(el);
                            if (!text || text.length <= 20) return null;
                            return {{
                                tag,
                                text,
                                html_marker: `<${{tag}}>...</${{tag}}>`,
                            }};
                        }})
                        .filter(Boolean);

                    const deduped = [];
                    const seenRecent = [];

                    for (const block of rawBlocks) {{
                        const key = block.text;
                        if (seenRecent.includes(key)) continue;

                        deduped.push(block);
                        seenRecent.push(key);
                        if (seenRecent.length > 8) seenRecent.shift();
                    }}

                    return deduped;
                }};

                const blockText = (root) => {{
                    if (!root) return "";
                    return extractTextBlocks(root)
                        .map(b => b.html_marker.replace("...", b.text))
                        .join("\\n\\n");
                }};

                const root = body || document.documentElement;

                return {{
                    final_url: window.location.href,
                    body_text: blockText(root),
                    body_text_blocks: extractTextBlocks(root),
                }};
            }}
            """
        )
        if isinstance(data, dict):
            return data
        return {"final_url": "", "body_text": "", "body_text_blocks": []}
    except Exception as scrape_error:
        return {
            "final_url": _string_value(url),
            "body_text": "",
            "body_text_blocks": [],
            "error": _string_value(str(scrape_error)),
        }
    finally:
        await close_page(playwright, browser, context)


@tool
async def broad_scrape(url: str) -> dict[str, str]:
    """Scrape a webpage and return consolidated visible body text, used for general information."""
    bundle = await _extract_text_bundle(url)
    return {
        "final_url": _string_value(bundle.get("final_url")),
        "body_text": _string_value(bundle.get("body_text")),
    }


@tool
async def detailed_scrape(url: str) -> dict[str, object]:
    """Scrape a webpage and return structured visible body text blocks, used for detailed information."""
    bundle = await _extract_text_bundle(url)
    return {
        "final_url": _string_value(bundle.get("final_url")),
        "body_text_blocks": _block_list_value(bundle.get("body_text_blocks")),
    }
