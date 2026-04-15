from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from app.config import get_settings
from app.normalizer import truncate_for_llm

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a strict web-page QA classifier for restaurant/bar menu validation.
Return ONLY valid JSON with keys:
{
  "is_real_menu": true|false,
  "is_correct_venue": true|false,
  "has_beverage_content": true|false,
  "is_boilerplate_only": true|false,
  "is_aggregator_page": true|false,
  "confidence": 0.0,
  "reason": "short string"
}
Do not include markdown.
"""

USER_TEMPLATE = """Venue metadata:
name: {name}
address: {address}
city: {city}
state: {state}
url: {url}

Page text:
{text}
"""


class HaikuClassifier:
    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = bool(settings.anthropic_api_key)
        self.model = settings.anthropic_model
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) if self.enabled else None

    async def classify(
        self,
        *,
        name: str,
        address: str | None,
        city: str | None,
        state: str | None,
        url: str,
        page_text: str,
    ) -> dict[str, Any] | None:
        if not self.enabled or self.client is None:
            return None

        try:
            msg = await self.client.messages.create(
                model=self.model,
                max_tokens=300,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": USER_TEMPLATE.format(
                            name=name,
                            address=address or "",
                            city=city or "",
                            state=state or "",
                            url=url,
                            text=truncate_for_llm(page_text),
                        ),
                    }
                ],
            )
            raw = "".join(
                block.text for block in msg.content
                if getattr(block, "type", None) == "text"
            ).strip()
            return json.loads(raw)
        except Exception as exc:
            logger.warning("haiku_classification_failed error=%s", exc)
            return None
