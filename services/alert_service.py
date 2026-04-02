import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Callable

from telegram import Bot
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown

from config import settings

logger = logging.getLogger(__name__)

MessageSender = Callable[[str], None]
TELEGRAM_STATUS_EMOJI = {
    "price_decreased": "🔻",
    "price_increased": "🔺",
}


def dispatch_alert(alert, product) -> str:
    """
    Send a price-drop notification through the first available channel.

    Channel priority: email → telegram → log
    Returns the channel string that was used.
    """
    return send_message(
        message=alert.message,
        subject=f"Price Alert: {product.name}",
        fallback_log_prefix="[PRICE ALERT]",
    )


def send_message(
    *,
    message: str,
    subject: str | None = None,
    fallback_log_prefix: str = "[MESSAGE]",
    telegram_message: str | None = None,
) -> str:
    """
    Send a generic message through the first available channel.

    Channel priority: email → telegram → log
    Returns the channel string that was used.
    """
    for channel_name, sender in _message_channels(
        subject=subject,
        telegram_message=telegram_message or message,
    ):
        try:
            sender(message)
            logger.info("%s message sent", channel_name.capitalize())
            return channel_name
        except Exception as exc:
            logger.error("%s message failed: %s", channel_name.capitalize(), exc)

    logger.info("%s %s", fallback_log_prefix, message)
    return "log"


def _message_channels(
    *, subject: str | None = None, telegram_message: str
) -> list[tuple[str, MessageSender]]:
    channels: list[tuple[str, MessageSender]] = []

    if settings.GMAIL_USER and settings.GMAIL_PASSWORD:
        channels.append(
            ("email", lambda message: _send_email_message(message, subject))
        )

    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        channels.append(
            ("telegram", lambda _message: _send_telegram_message(telegram_message))
        )

    return channels


def _send_email(alert, product) -> None:
    """Send alert via Gmail SMTP with STARTTLS."""
    _send_email_message(alert.message, f"Price Alert: {product.name}")


def _send_email_message(message: str, subject: str | None = None) -> None:
    """Send a plain email message via Gmail SMTP with STARTTLS."""
    to_addr = settings.ALERT_TO_EMAIL or settings.GMAIL_USER
    msg = MIMEText(message)
    msg["Subject"] = subject or "Product Tracker Notification"
    msg["From"] = settings.GMAIL_USER
    msg["To"] = to_addr

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.GMAIL_USER, settings.GMAIL_PASSWORD)
        smtp.sendmail(settings.GMAIL_USER, [to_addr], msg.as_string())


def _send_telegram_message(message: str) -> None:
    """Send a Telegram message via python-telegram-bot using MarkdownV2."""
    asyncio.run(
        Bot(token=settings.TELEGRAM_BOT_TOKEN).send_message(
            chat_id=settings.TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    )


def escape_telegram_markdown(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    return escape_markdown(text, version=2)


def escape_telegram_link(url: str) -> str:
    """Escape a URL for use in a Telegram MarkdownV2 link target."""
    return escape_markdown(url, version=2, entity_type="text_link")


def get_telegram_status_emoji(status: str, default: str = "") -> str:
    """Return a Telegram emoji marker for a named status."""
    return TELEGRAM_STATUS_EMOJI.get(status, default)
