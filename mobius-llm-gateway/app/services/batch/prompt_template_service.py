import re
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Dict, List

from app.schemas.batch import PromptTemplateCreateRequest

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_\.\-]+)\s*\}\}")


class PromptTemplateService:
    """In-memory prompt template registry for batch prompt-caching workflows."""

    _templates: Dict[str, Dict[str, Any]] = {}
    _lock = Lock()

    def create(self, payload: PromptTemplateCreateRequest) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        template_id = f"pt_{uuid.uuid4().hex[:20]}"
        record = {
            "template_id": template_id,
            "name": payload.name,
            "model": payload.model,
            "max_tokens": payload.max_tokens,
            "system_blocks": [b.model_dump() for b in payload.system_blocks],
            "shared_user_blocks": [b.model_dump() for b in payload.shared_user_blocks],
            "metadata": payload.metadata,
            "created_at": now,
            "expires_at": now + timedelta(minutes=payload.expires_in_minutes),
        }
        with self._lock:
            self._templates[template_id] = record
        return record

    def get(self, template_id: str) -> Dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            record = self._templates.get(template_id)
            if not record:
                return None
            if record["expires_at"] <= now:
                self._templates.pop(template_id, None)
                return None
            return record

    def render_requests(self, template_id: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        record = self.get(template_id)
        if not record:
            return []

        rendered_requests: List[Dict[str, Any]] = []
        for item in items:
            variables = item.get("variables") or {}

            system_payload = [
                self._render_block(block, variables)
                for block in record.get("system_blocks", [])
            ]

            shared_user_payload = [
                self._render_block(block, variables)
                for block in record.get("shared_user_blocks", [])
            ]

            work_item_text = self._render_text(item["work_item"], variables)
            shared_user_payload.append({
                "type": "text",
                "text": work_item_text,
            })

            rendered_requests.append({
                "custom_id": item["custom_id"],
                "params": {
                    "model": record["model"],
                    "max_tokens": record["max_tokens"],
                    "system": system_payload,
                    "messages": [
                        {
                            "role": "user",
                            "content": shared_user_payload,
                        }
                    ],
                    "metadata": record.get("metadata"),
                },
            })

        return rendered_requests

    @staticmethod
    def _render_text(text: str, variables: Dict[str, Any]) -> str:
        def replace(match: re.Match) -> str:
            key = match.group(1)
            return str(variables.get(key, ""))

        return _PLACEHOLDER.sub(replace, text)

    def _render_block(self, block: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
        out = {
            "type": "text",
            "text": self._render_text(block.get("text", ""), variables),
        }
        if block.get("cache", True):
            out["cache_control"] = {"type": "ephemeral"}
        return out
