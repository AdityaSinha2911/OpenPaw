"""
browser_tools.py - Browser automation using Playwright async API.

Provides YouTube, Gmail, Google Search, WhatsApp Web, and general web
automation with a persistent browser session so logins are preserved.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

logger = logging.getLogger("openpaw.browser")

# Financial/banking domains the agent must never auto-navigate to
_BLOCKED_DOMAINS = {
    "bank", "banking", "chase", "wellsfargo", "citi", "hsbc",
    "paypal", "venmo", "zelle", "schwab", "fidelity", "vanguard",
    "tdameritrade", "etrade", "robinhood", "coinbase", "binance",
    "kraken", "stripe", "plaid",
}


def _is_blocked_url(url: str) -> bool:
    """Return True if the URL appears to be a financial/banking site."""
    lower = url.lower()
    for keyword in _BLOCKED_DOMAINS:
        if keyword in lower:
            return True
    return False


class BrowserManager:
    """Manages a persistent Playwright Chromium browser instance.

    The browser session (cookies, local storage, login state) is stored
    in ``data_dir/browser_session/`` so it survives across restarts.
    Screenshots are saved to ``data_dir/screenshots/``.
    """

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.session_dir = os.path.join(data_dir, "browser_session")
        self.screenshot_dir = os.path.join(data_dir, "screenshots")
        os.makedirs(self.session_dir, exist_ok=True)
        os.makedirs(self.screenshot_dir, exist_ok=True)

        self._playwright = None
        self._browser_context = None
        self._page = None


    # Lifecycle

    async def _ensure_browser(self):
        """Launch the browser if not already running."""
        if self._page and not self._page.is_closed():
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
            )

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        self._browser_context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.session_dir,
            headless=False,
            args=["--start-maximized"],
            viewport=None,            # use full window size
            ignore_default_args=["--enable-automation"],
        )
        pages = self._browser_context.pages
        self._page = pages[0] if pages else await self._browser_context.new_page()
        logger.info("Browser launched with persistent session at %s", self.session_dir)

    async def close(self):
        """Shut-down the browser cleanly."""
        try:
            if self._browser_context:
                await self._browser_context.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.error("Error closing browser: %s", exc)
        finally:
            self._browser_context = None
            self._page = None
            self._playwright = None
            logger.info("Browser closed")

    @property
    async def page(self):
        await self._ensure_browser()
        return self._page

    # General Web
    async def open_url(self, url: str) -> str:
        """Open a URL in the browser."""
        if _is_blocked_url(url):
            logger.warning("Blocked navigation to financial site: %s", url)
            return "Blocked: I cannot auto-navigate to banking or financial sites for safety."
        try:
            p = await self.page
            await p.goto(url, wait_until="domcontentloaded", timeout=30000)
            logger.info("Opened URL: %s", url)
            return f"Opened: {url}"
        except Exception as exc:
            logger.error("Failed to open URL %s: %s", url, exc)
            return f"Error opening URL: {exc}"

    async def take_screenshot(self) -> str:
        """Take a screenshot of the current page and return the file path."""
        try:
            p = await self.page
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(self.screenshot_dir, f"screenshot_{ts}.png")
            await p.screenshot(path=path, full_page=False)
            logger.info("Screenshot saved: %s", path)
            return f"Screenshot saved: {path}"
        except Exception as exc:
            logger.error("Screenshot failed: %s", exc)
            return f"Error taking screenshot: {exc}"

    async def click_by_text(self, text: str) -> str:
        """Click the first visible link or button whose text matches."""
        try:
            p = await self.page
            locator = p.get_by_text(text, exact=False).first
            await locator.click(timeout=10000)
            logger.info("Clicked element with text: %s", text)
            return f"Clicked: {text}"
        except Exception as exc:
            logger.error("Click failed for '%s': %s", text, exc)
            return f"Error clicking '{text}': {exc}"

    async def fill_and_submit(self, selector: str, value: str) -> str:
        """Fill an input field and press Enter."""
        try:
            p = await self.page
            await p.fill(selector, value, timeout=10000)
            await p.press(selector, "Enter")
            logger.info("Filled selector %s and submitted", selector)
            return f"Filled and submitted: {selector}"
        except Exception as exc:
            logger.error("Fill failed for '%s': %s", selector, exc)
            return f"Error filling form: {exc}"

    # Google Search
    async def google_search(self, query: str) -> str:
        """Search Google and return top 5 results."""
        try:
            p = await self.page
            url = f"https://www.google.com/search?q={quote_plus(query)}"
            await p.goto(url, wait_until="domcontentloaded", timeout=30000)
            await p.wait_for_timeout(2000)

            results = []
            items = p.locator("div#search a h3")
            count = await items.count()
            for i in range(min(count, 5)):
                h3 = items.nth(i)
                title = await h3.inner_text()
                link_el = h3.locator("xpath=ancestor::a")
                href = await link_el.get_attribute("href") or ""
                results.append(f"{i + 1}. {title}\n   {href}")

            logger.info("Google search: %s (%d results)", query, len(results))
            if results:
                return "Top Google results:\n\n" + "\n\n".join(results)
            return "No results found."
        except Exception as exc:
            logger.error("Google search failed: %s", exc)
            return f"Error searching Google: {exc}"

    # YouTube
    async def youtube_search_play(self, query: str) -> str:
        try:
            p = await self.page

            # Open search page
            await p.goto(
                f"https://www.youtube.com/results?search_query={quote_plus(query)}",
                wait_until="domcontentloaded"
            )

            # Get first video link
            video = p.locator("a#video-title").first
            video_url = await video.get_attribute("href")

            # Open video directly (fast)
            await p.goto(f"https://www.youtube.com{video_url}")

            title = await p.title()
            return f"Now playing: {title}"

        except Exception as e:
            return f"Error: {e}"

    async def youtube_open_url(self, url: str) -> str:
        """Open a specific YouTube URL."""
        try:
            p = await self.page
            await p.goto(url, wait_until="domcontentloaded", timeout=30000)
            await p.wait_for_timeout(3000)
            title = await p.title()
            logger.info("YouTube opened: %s", url)
            return f"Opened YouTube: {title}"
        except Exception as exc:
            logger.error("YouTube open URL failed: %s", exc)
            return f"Error opening YouTube URL: {exc}"

    async def youtube_pause_resume(self) -> str:
        """Toggle pause/resume on the current YouTube video."""
        try:
            p = await self.page
            video = p.locator("video").first
            is_paused = await video.evaluate("v => v.paused")
            if is_paused:
                await video.evaluate("v => v.play()")
                logger.info("YouTube: resumed")
                return "YouTube video resumed."
            else:
                await video.evaluate("v => v.pause()")
                logger.info("YouTube: paused")
                return "YouTube video paused."
        except Exception as exc:
            logger.error("YouTube pause/resume failed: %s", exc)
            return f"Error toggling YouTube playback: {exc}"

    async def youtube_skip(self) -> str:
        """Skip to the next video in YouTube autoplay."""
        try:
            p = await self.page
            next_btn = p.locator("a.ytp-next-button")
            await next_btn.click(timeout=5000)
            await p.wait_for_timeout(3000)
            title = await p.title()
            logger.info("YouTube skipped to: %s", title)
            return f"Skipped to: {title}"
        except Exception as exc:
            logger.error("YouTube skip failed: %s", exc)
            return f"Error skipping video: {exc}"

    # Gmail
    async def gmail_send(self, to: str, subject: str, body: str) -> str:
        """Compose and send an email via Gmail web interface.

        NOTE: The caller is responsible for confirmation before invoking.
        """
        try:
            p = await self.page
            await p.goto("https://mail.google.com/mail/u/0/#inbox?compose=new", wait_until="domcontentloaded", timeout=30000)
            await p.wait_for_timeout(3000)

            # Fill To field
            to_field = p.locator("input[aria-label='To recipients']").first
            await to_field.click()
            await to_field.fill(to)
            await p.keyboard.press("Tab")
            await p.wait_for_timeout(500)

            # Fill Subject
            subject_field = p.locator("input[name='subjectbox']").first
            await subject_field.fill(subject)

            # Fill Body
            body_field = p.locator("div[aria-label='Message Body']").first
            await body_field.click()
            await body_field.fill(body)

            # Click Send
            send_btn = p.locator("div[aria-label*='Send']").first
            await send_btn.click()
            await p.wait_for_timeout(2000)

            logger.info("Gmail: sent email to %s, subject: %s", to, subject)
            return f"Email sent to {to} with subject '{subject}'."
        except Exception as exc:
            logger.error("Gmail send failed: %s", exc)
            return f"Error sending email: {exc}"

    async def gmail_read(self) -> str:
        """Read the latest inbox emails from Gmail."""
        try:
            p = await self.page
            await p.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded", timeout=30000)
            await p.wait_for_timeout(3000)

            rows = p.locator("tr.zA")
            count = await rows.count()
            emails = []
            for i in range(min(count, 5)):
                row = rows.nth(i)
                sender = await row.locator("span.bA4 span").first.inner_text()
                subject = await row.locator("span.bog").first.inner_text()
                preview = await row.locator("span.y2").first.inner_text()
                emails.append(f"{i + 1}. From: {sender}\n   Subject: {subject}\n   Preview: {preview}")

            logger.info("Gmail: read %d emails", len(emails))
            if emails:
                return "Latest emails:\n\n" + "\n\n".join(emails)
            return "No emails found in inbox."
        except Exception as exc:
            logger.error("Gmail read failed: %s", exc)
            return f"Error reading Gmail: {exc}"

    async def gmail_search(self, keyword: str) -> str:
        """Search Gmail for emails matching *keyword*."""
        try:
            p = await self.page
            url = f"https://mail.google.com/mail/u/0/#search/{quote_plus(keyword)}"
            await p.goto(url, wait_until="domcontentloaded", timeout=30000)
            await p.wait_for_timeout(3000)

            rows = p.locator("tr.zA")
            count = await rows.count()
            emails = []
            for i in range(min(count, 5)):
                row = rows.nth(i)
                sender = await row.locator("span.bA4 span").first.inner_text()
                subject = await row.locator("span.bog").first.inner_text()
                emails.append(f"{i + 1}. From: {sender} | Subject: {subject}")

            logger.info("Gmail search '%s': %d results", keyword, len(emails))
            if emails:
                return f"Gmail search for '{keyword}':\n\n" + "\n".join(emails)
            return f"No emails found for '{keyword}'."
        except Exception as exc:
            logger.error("Gmail search failed: %s", exc)
            return f"Error searching Gmail: {exc}"

    # WhatsApp Web
    async def whatsapp_send(self, contact: str, message: str) -> str:
        """Send a WhatsApp message to a contact by name via WhatsApp Web."""
        try:
            p = await self.page
            await p.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)
            await p.wait_for_timeout(5000)

            # Search for contact
            search_box = p.locator("div[contenteditable='true'][data-tab='3']").first
            await search_box.click()
            await search_box.fill(contact)
            await p.wait_for_timeout(2000)

            # Click on the contact in search results
            contact_result = p.locator(f"span[title='{contact}']").first
            await contact_result.click(timeout=10000)
            await p.wait_for_timeout(1000)

            # Type and send message
            msg_box = p.locator("div[contenteditable='true'][data-tab='10']").first
            await msg_box.click()
            await msg_box.fill(message)
            await p.keyboard.press("Enter")
            await p.wait_for_timeout(1000)

            logger.info("WhatsApp: sent message to %s", contact)
            return f"WhatsApp message sent to {contact}."
        except Exception as exc:
            logger.error("WhatsApp send failed: %s", exc)
            return f"Error sending WhatsApp message: {exc}"
