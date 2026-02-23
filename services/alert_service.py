import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from config import settings

logger = logging.getLogger(__name__)


def dispatch_alert(alert, product) -> str:
    """
    Send a price-drop notification through the first available channel.

    Channel priority: email → telegram → log
    Returns the channel string that was used.
    """
    if settings.GMAIL_USER and settings.GMAIL_PASSWORD:
        try:
            _send_email(alert, product)
            logger.info(f"Email alert sent for product {product.id}")
            return "email"
        except Exception as e:
            logger.error(f"Email alert failed for product {product.id}: {e}")

    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        try:
            _send_telegram(alert)
            logger.info(f"Telegram alert sent for product {product.id}")
            return "telegram"
        except Exception as e:
            logger.error(f"Telegram alert failed for product {product.id}: {e}")

    logger.info(f"[PRICE ALERT] {alert.message}")
    return "log"


def _send_email(alert, product) -> None:
    """Send alert via Gmail SMTP with STARTTLS."""
    to_addr = settings.ALERT_TO_EMAIL or settings.GMAIL_USER
    msg = MIMEText(alert.message)
    msg["Subject"] = f"Price Alert: {product.name}"
    msg["From"] = settings.GMAIL_USER
    msg["To"] = to_addr

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.GMAIL_USER, settings.GMAIL_PASSWORD)
        smtp.sendmail(settings.GMAIL_USER, [to_addr], msg.as_string())


def _send_telegram(alert) -> None:
    """Send alert via Telegram Bot API sendMessage."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    httpx.get(
        url,
        params={
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "text": alert.message,
        },
        timeout=10,
    ).raise_for_status()
