import uuid
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STORE_PATH = Path(__file__).parent.parent / "data" / "vocabularies.json"


def _load() -> Dict[str, dict]:
    try:
        if _STORE_PATH.exists():
            return json.loads(_STORE_PATH.read_text())
    except Exception as e:
        logger.warning("Could not load vocabularies from disk: %s", e)
    return {}


def _save(data: Dict[str, dict]) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STORE_PATH.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning("Could not save vocabularies to disk: %s", e)


def _build_instruction(vocab: dict) -> str:
    """Compile all vocabulary parameters into a single instruction block."""
    parts = [vocab["instructions"]]

    if vocab.get("tone"):
        parts.append(f"TONE: Your tone must be {vocab['tone']}.")

    if vocab.get("target_audience"):
        parts.append(f"AUDIENCE: You are speaking to {vocab['target_audience']}. Adjust your language accordingly.")

    if vocab.get("response_format"):
        fmt = vocab["response_format"]
        format_map = {
            "conversational": "Use a natural conversational style. No bullet points or headers.",
            "bullet_points": "Structure all responses using bullet points.",
            "numbered_list": "Structure all responses using numbered lists.",
            "markdown": "Use markdown formatting with headers, bold, and bullet points where appropriate.",
        }
        parts.append(f"FORMAT: {format_map.get(fmt, fmt)}")

    if vocab.get("response_length"):
        length = vocab["response_length"]
        length_map = {
            "brief": "Keep responses short and concise — 2 to 3 sentences maximum.",
            "moderate": "Keep responses at a moderate length — clear and complete but not excessive.",
            "detailed": "Provide detailed and thorough responses covering all relevant aspects.",
        }
        parts.append(f"LENGTH: {length_map.get(length, length)}")

    if vocab.get("required_phrases"):
        phrases = ", ".join(f"'{p}'" for p in vocab["required_phrases"])
        parts.append(f"REQUIRED PHRASES: You must naturally include at least 3 of these phrases in every response: {phrases}.")

    if vocab.get("avoid_words"):
        words = ", ".join(f"'{w}'" for w in vocab["avoid_words"])
        parts.append(f"FORBIDDEN WORDS: Never use these words or phrases: {words}.")

    if vocab.get("forbidden_topics"):
        topics = ", ".join(vocab["forbidden_topics"])
        parts.append(f"FORBIDDEN TOPICS: Never discuss or mention: {topics}.")

    if vocab.get("greeting_style"):
        parts.append(f"OPENING: {vocab['greeting_style']}")

    if vocab.get("closing_style"):
        parts.append(f"CLOSING: {vocab['closing_style']}")

    if vocab.get("language"):
        parts.append(f"LANGUAGE: Always respond in language code '{vocab['language']}' regardless of what language the user writes in.")

    if vocab.get("examples"):
        parts.append("EXAMPLES OF EXPECTED BEHAVIOR:")
        for i, ex in enumerate(vocab["examples"], 1):
            parts.append(f"  Example {i}:\n  User: {ex['user']}\n  Assistant: {ex['assistant']}")

    return "\n\n".join(parts)


def _inject_into_request(request: Dict[str, Any], instructions: str, mode: str) -> Dict[str, Any]:
    """Inject vocabulary instructions into a single request's system prompt."""
    params = request.get("params", request)
    system = params.get("system")

    if mode == "replace":
        injected_system = [{"type": "text", "text": instructions}]

    elif isinstance(system, list):
        vocab_block = {"type": "text", "text": instructions}
        injected_system = [vocab_block] + system if mode == "prepend" else system + [vocab_block]

    elif isinstance(system, str):
        injected_system = (
            instructions + "\n\n" + system if mode == "prepend"
            else system + "\n\n" + instructions
        )

    else:
        injected_system = [{"type": "text", "text": instructions}]

    result = dict(request)
    if "params" in request:
        result["params"] = {**params, "system": injected_system}
    else:
        result["system"] = injected_system
    return result


class VocabularyService:

    def create(self, data: dict) -> dict:
        store = _load()
        vocabulary_id = f"vocab_{uuid.uuid4().hex[:12]}"
        vocab = {"vocabulary_id": vocabulary_id, **data}
        store[vocabulary_id] = vocab
        _save(store)
        logger.info("Vocabulary created | id=%s name=%s", vocabulary_id, data.get("name"))
        return vocab

    def list_all(self) -> List[dict]:
        return list(_load().values())

    def get(self, vocabulary_id: str) -> Optional[dict]:
        return _load().get(vocabulary_id)

    def delete(self, vocabulary_id: str) -> bool:
        store = _load()
        if vocabulary_id not in store:
            return False
        del store[vocabulary_id]
        _save(store)
        logger.info("Vocabulary deleted | id=%s", vocabulary_id)
        return True

    def apply_to_requests(
        self,
        requests: List[Dict[str, Any]],
        vocabulary_id: str,
    ) -> List[Dict[str, Any]]:
        store = _load()
        batch_vocab = store.get(vocabulary_id)

        result = []
        for req in requests:
            req_vocab_id = req.get("params", {}).get("vocabulary_id") or req.get("vocabulary_id")
            if req_vocab_id and req_vocab_id in store:
                vocab = store[req_vocab_id]
            elif batch_vocab:
                vocab = batch_vocab
            else:
                result.append(req)
                continue

            cleaned = dict(req)
            if "params" in cleaned:
                cleaned["params"] = {k: v for k, v in cleaned["params"].items() if k != "vocabulary_id"}
            cleaned.pop("vocabulary_id", None)

            instructions = _build_instruction(vocab)
            injected = _inject_into_request(cleaned, instructions, vocab["injection_mode"])
            result.append(injected)

        return result
