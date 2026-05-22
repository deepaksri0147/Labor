# Mobius LLM Gateway

An OpenAI-compatible LLM Gateway with modular architecture that formats requests and responses to work with various language models through Ollama.

## Features

- **OpenAI Compatible API**: Supports `/v1/chat/completions` and `/v1/models` endpoints
- **Modular Architecture**: Clean separation of concerns with dedicated modules
- **Input Formatting**: Converts OpenAI message format to model-specific prompts
- **Output Formatting**: Transforms model responses back to OpenAI format
- **Flexible Model Support**: Works with any model supported by Ollama
- **Parameter Mapping**: Maps OpenAI parameters (temperature, max_tokens, etc.) to model options
- **Configuration Management**: Environment-based configuration with sensible defaults

## Project Structure

```
mobius_llm_gateway/
├── __init__.py              # Package initialization
├── llm_gateway.py          # Main application entry point
├── models.py               # OpenAI compatible data models
├── formatters.py           # Input/Output formatters
├── api_endpoints.py        # API endpoint handlers
├── ollama.py              # Ollama client integration
├── config.py              # Configuration management
├── requirements.txt       # Python dependencies
└── README.md              # Documentation
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the gateway:
```bash
python llm_gateway.py
```

The server will start on `http://localhost:8000` (configurable via environment variables)

## API Endpoints

### Chat Completions
```
POST /v1/chat/completions
```

Example request:
```json
{
  "model": "hf.co/naga080898/qwen3-14b-xlam-fc-gguf",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello, how are you?"}
  ],
  "temperature": 0.7,
  "max_tokens": 150
}
```

### List Models
```
GET /v1/models
```

### Health Check
```
GET /health
```

## Architecture

The gateway uses a modular architecture with the following components:

1. **models.py**: OpenAI compatible Pydantic data models
2. **formatters.py**: Input/Output formatters for request/response transformation
3. **api_endpoints.py**: Business logic for API endpoint handlers
4. **ollama.py**: Ollama client integration with error handling
5. **config.py**: Centralized configuration management
6. **llm_gateway.py**: Main FastAPI application with route definitions

## Configuration

The gateway supports environment-based configuration. You can set the following environment variables:

```bash
# Server settings
HOST=0.0.0.0                    # Server host (default: 0.0.0.0)
PORT=8000                       # Server port (default: 8000)

# Ollama API settings
OLLAMA_BASE_URL=http://ollama-keda.mobiusdtaas.ai  # Ollama server URL

# Request settings
REQUEST_TIMEOUT=300             # Request timeout in seconds (default: 300)

# Logging
LOG_LEVEL=INFO                  # Log level (default: INFO)
```

Or modify the defaults in `config.py`.

## Usage with OpenAI Client Libraries

You can use this gateway with any OpenAI-compatible client library by pointing it to your gateway URL:

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy-key"  # Not used but required by client
)

response = client.chat.completions.create(
    model="hf.co/naga080898/qwen3-14b-xlam-fc-gguf",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
```

## Batch API v1 (Large Document Once + Many Work Items)

All examples below use versioned endpoints under `/v1/batch/*`.

### Why this flow

Use this when you have:
- a **large static document** (policy, regulation, manual), and
- many **small work items** to evaluate against it.

The gateway supports Anthropic prompt caching by rendering cached blocks with `cache_control: {"type": "ephemeral"}`.

### End-to-end workflow

1. Create a reusable prompt template (`/v1/batch/prompt-templates`)
2. Render N requests from template + work items (`/v1/batch/prompt-templates/render`)
3. Run token/context pre-flight estimate (`/v1/batch/batch-estimate`)
4. Submit batch (`/v1/batch/batch-submit`)
5. Poll status (`/v1/batch/batch-poll/{batch_id}`)
6. Fetch final results + usage summary (`/v1/batch/batch-fetch/{batch_id}`)

---

### 1) Create prompt template

```bash
curl -X POST "http://localhost:8000/v1/batch/prompt-templates" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "compliance-evaluator-v1",
    "model": "claude-sonnet-4-6",
    "max_tokens": 1200,
    "expires_in_minutes": 180,
    "system_blocks": [
      {"text": "{{regulation_text}}", "cache": true},
      {"text": "You are a strict compliance evaluator.", "cache": true}
    ],
    "shared_user_blocks": [
      {"text": "Company profile: {{company_profile}}", "cache": true}
    ],
    "metadata": {"domain": "regulatory"}
  }'
```

Response (wrapped by gateway envelope):
- `data.template_id`
- `data.expires_at`

---

### 2) Render multiple work items

```bash
curl -X POST "http://localhost:8000/v1/batch/prompt-templates/render" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "pt_1234567890abcdef",
    "items": [
      {
        "custom_id": "work-001",
        "work_item": "Evaluate control #A for business unit {{bu}}.",
        "variables": {
          "regulation_text": "<very large document>",
          "company_profile": "EU fintech, 250 employees",
          "bu": "Payments"
        }
      },
      {
        "custom_id": "work-002",
        "work_item": "Evaluate control #B for business unit {{bu}}.",
        "variables": {
          "regulation_text": "<same very large document>",
          "company_profile": "EU fintech, 250 employees",
          "bu": "Lending"
        }
      }
    ]
  }'
```

Use `data.requests` directly as payload for `/v1/batch/batch-submit`.

---

### 3) Token estimate before submit

```bash
curl -X POST "http://localhost:8000/v1/batch/batch-estimate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "custom_id": "work-001",
        "params": {
          "model": "claude-sonnet-4-6",
          "max_tokens": 1200,
          "system": [{"type": "text", "text": "..."}],
          "messages": [{"role": "user", "content": [{"type": "text", "text": "..."}]}]
        }
      }
    ]
  }'
```

Response fields:
- `data.items[].estimated_input_tokens`
- `data.items[].configured_max_tokens`
- `data.items[].remaining_context_tokens`
- `data.items[].is_over_context_limit`

---

### 4) Submit batch

```bash
curl -X POST "http://localhost:8000/v1/batch/batch-submit" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "custom_id": "work-001",
        "params": {
          "model": "claude-sonnet-4-6",
          "max_tokens": 1200,
          "system": [
            {"type": "text", "text": "<BP1 document>", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "<BP2 shared instructions>", "cache_control": {"type": "ephemeral"}}
          ],
          "messages": [
            {
              "role": "user",
              "content": [
                {"type": "text", "text": "<BP3 shared profile>", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "<BP4 unique work item>"}
              ]
            }
          ]
        }
      }
    ]
  }'
```

---

### 5) Poll status

```bash
curl -X GET "http://localhost:8000/v1/batch/batch-poll/$BATCH_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Wait until `data.processing_status` is `ended`.

---

### 6) Fetch results + token usage

```bash
curl -X GET "http://localhost:8000/v1/batch/batch-fetch/$BATCH_ID" \
  -H "Authorization: Bearer $TOKEN"
```

Includes:
- `data.results[]` (raw Anthropic JSONL rows)
- `data.usage_summary.total_input_tokens`
- `data.usage_summary.total_output_tokens`
- `data.usage_summary.total_cache_creation_tokens`
- `data.usage_summary.total_cache_read_tokens`
- `data.usage_summary.total_tokens`

---

### Cancel / delete

```bash
curl -X POST "http://localhost:8000/v1/batch/batch-cancel/$BATCH_ID" \
  -H "Authorization: Bearer $TOKEN"

curl -X DELETE "http://localhost:8000/v1/batch/batch-delete/$BATCH_ID" \
  -H "Authorization: Bearer $TOKEN"
```

---

### Error format

All error responses are normalized as:

```json
{
  "status": "error",
  "statusCode": 503,
  "message": "Anthropic batch API temporarily unavailable due to repeated upstream failures.",
  "details": {
    "error": "CIRCUIT_OPEN",
    "message": "Anthropic batch API temporarily unavailable due to repeated upstream failures.",
    "retry_after_seconds": 24
  }
}
```

### Reliability features implemented

- Upstream retries with exponential backoff + jitter
- Circuit breaker after repeated upstream failures
- Auto polling worker for submitted batches
- Retry queue for errored items in completed batches
- Token estimate pre-flight endpoint
- Usage summary (input/output/cache tokens) on fetch
