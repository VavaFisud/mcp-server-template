from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx

from config import Settings


class PokeNotifier:
    """Wrapper around the Poke inbound webhook API."""

    DEFAULT_ENDPOINT = "https://poke.com/api/v1/inbound-sms/webhook"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.Client(timeout=10)
        self._endpoint = settings.poke_webhook_url or self.DEFAULT_ENDPOINT
        self._api_key = settings.poke_api_key

    def send(self, message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"message": message}
        if context:
            payload["context"] = context
        response = self._client.post(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            content=json.dumps(payload),
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {"status": "sent"}
