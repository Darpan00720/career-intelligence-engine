"""Notification system (v4).

Pluggable channels (console / email / slack) behind a single Notifier. Channels
are configured via environment or explicit construction; unavailable channels
degrade to no-ops so the pipeline never fails on a notification.

    from core.notifications import get_notifier
    get_notifier().dispatch("offer_received", "Offer from Acme!", level="INFO")

Event helpers (high-scoring job, status change, offer, pipeline failure) build
consistent messages for the NotificationAgent.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from core.event_log import log_event


class Channel(ABC):
    name = "channel"

    @abstractmethod
    def send(self, subject: str, message: str, level: str = "INFO") -> bool:
        ...


class ConsoleChannel(Channel):
    name = "console"

    def send(self, subject: str, message: str, level: str = "INFO") -> bool:
        print(f"[notify:{level}] {subject} — {message}")
        return True


class EmailChannel(Channel):
    """SMTP email channel. No-op (returns False) unless SMTP env vars are set."""
    name = "email"

    def __init__(self):
        self.host = os.getenv("SMTP_HOST")
        self.to = os.getenv("NOTIFY_EMAIL_TO")

    @property
    def configured(self) -> bool:
        return bool(self.host and self.to)

    def send(self, subject: str, message: str, level: str = "INFO") -> bool:
        if not self.configured:
            return False
        try:  # pragma: no cover - requires live SMTP
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = os.getenv("NOTIFY_EMAIL_FROM", self.to)
            msg["To"] = self.to
            msg.set_content(message)
            with smtplib.SMTP(self.host, int(os.getenv("SMTP_PORT", "25"))) as s:
                s.send_message(msg)
            return True
        except Exception as exc:
            log_event("pipeline", "email_failed", status="error", error=str(exc), level="ERROR")
            return False


class SlackChannel(Channel):
    """Slack webhook channel. No-op unless SLACK_WEBHOOK_URL is set."""
    name = "slack"

    def __init__(self):
        self.webhook = os.getenv("SLACK_WEBHOOK_URL")

    @property
    def configured(self) -> bool:
        return bool(self.webhook)

    def send(self, subject: str, message: str, level: str = "INFO") -> bool:
        if not self.configured:
            return False
        try:  # pragma: no cover - requires live webhook
            import requests
            requests.post(self.webhook, json={"text": f"*{subject}*\n{message}"}, timeout=10)
            return True
        except Exception as exc:
            log_event("pipeline", "slack_failed", status="error", error=str(exc), level="ERROR")
            return False


class Notifier:
    def __init__(self, channels: list[Channel] | None = None):
        self.channels = channels if channels is not None else [ConsoleChannel()]

    def dispatch(self, subject: str, message: str, level: str = "INFO") -> dict:
        """Send to every configured channel. Returns {channel: delivered_bool}."""
        delivered: dict[str, bool] = {}
        for ch in self.channels:
            try:
                delivered[ch.name] = bool(ch.send(subject, message, level))
            except Exception as exc:  # noqa: BLE001 - never let notify break callers
                delivered[ch.name] = False
                log_event("pipeline", "notify_failed", status="error",
                          error=f"{ch.name}: {exc}", level="ERROR")
        log_event("pipeline", "notification", status="ok",
                  error=None, level=level)
        return delivered


def _channels_from_env() -> list[Channel]:
    """Build the channel list from NOTIFY_CHANNELS (csv), default console."""
    names = [c.strip() for c in os.getenv("NOTIFY_CHANNELS", "console").split(",") if c.strip()]
    available = {"console": ConsoleChannel, "email": EmailChannel, "slack": SlackChannel}
    channels: list[Channel] = []
    for n in names:
        cls = available.get(n)
        if cls:
            channels.append(cls())
    return channels or [ConsoleChannel()]


_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    """Process-wide notifier built from env config (cached)."""
    global _notifier
    if _notifier is None:
        _notifier = Notifier(_channels_from_env())
    return _notifier


def reset_notifier() -> None:
    """Drop the cached notifier (used by tests after changing env)."""
    global _notifier
    _notifier = None


# ── Event message builders ───────────────────────────────────────────────────

def high_scoring_job_event(company: str, title: str, score: int) -> dict:
    return {"event": "new_high_scoring_job",
            "message": f"{title} @ {company} scored {score}", "level": "INFO"}


def status_change_event(company: str, title: str, status: str) -> dict:
    return {"event": "interview_status_change",
            "message": f"{title} @ {company} → {status}", "level": "INFO"}


def offer_event(company: str, title: str) -> dict:
    return {"event": "offer_received",
            "message": f"Offer received: {title} @ {company}", "level": "INFO"}


def pipeline_failure_event(detail: str) -> dict:
    return {"event": "pipeline_failure", "message": detail, "level": "ERROR"}
