"""Telegram delivery.

Credentials come from the environment — TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID — which the GitHub Actions workflow fills in from the
repository secrets of the same name.
"""

from __future__ import annotations

import html
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 20

# Telegram throttles at roughly one message per second per chat.
SECONDS_BETWEEN_MESSAGES = 1.2
MAX_RETRIES = 3


class TelegramNotConfigured(RuntimeError):
    """Raised when the bot token or chat ID is missing from the environment."""


def credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise TelegramNotConfigured(
            "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (repository secrets)"
        )
    return token, chat_id


def format_job(job: dict) -> str:
    """The alert body for one listing."""
    escape = html.escape
    return (
        f"🆕 <b>{escape(job['company'])}</b>\n"
        f"{escape(job['title'])}\n"
        f"📍 {escape(job['location'])}\n"
        f"{escape(job['url'])}"
    )


def _send(session: requests.Session, token: str, chat_id: str, text: str) -> bool:
    """Post one message, honouring Telegram's rate-limit responses."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.post(
                API_URL.format(token=token),
                data={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.warning("Telegram request failed (attempt %d): %s", attempt, exc)
            time.sleep(2 * attempt)
            continue

        if response.status_code == 429:
            retry_after = (response.json().get("parameters") or {}).get("retry_after", 5)
            log.warning("Telegram rate limit hit; waiting %ss", retry_after)
            time.sleep(float(retry_after) + 1)
            continue

        if response.ok:
            return True

        log.warning("Telegram rejected the message (%s): %s", response.status_code, response.text[:200])
        time.sleep(2 * attempt)

    return False


def send_jobs(jobs: list[dict]) -> set[str]:
    """Alert on each job. Returns the ids that were delivered successfully."""
    if not jobs:
        return set()

    token, chat_id = credentials()
    session = requests.Session()
    delivered: set[str] = set()

    for index, job in enumerate(jobs):
        if index:
            time.sleep(SECONDS_BETWEEN_MESSAGES)
        if _send(session, token, chat_id, format_job(job)):
            delivered.add(job["id"])
        else:
            log.error("giving up on alert for %s — %s", job["company"], job["title"])

    log.info("Telegram: %d of %d alerts delivered", len(delivered), len(jobs))
    return delivered
