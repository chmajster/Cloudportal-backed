"""Durable domain event broker and extension runtime."""

from app.events.service import publish_event

__all__ = ['publish_event']
