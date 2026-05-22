# app/routers/inference.py
from fastapi import APIRouter, Security, HTTPException, Body, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
import uuid

from app.utils.utils import decode_jwt, extract_identity
from app.schemas.inference import InferenceRequest
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, NodeType
from app.celery_app import celery_app as celery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inference", tags=["inference"])
auth_scheme = HTTPBearer()


@router.post("/chat", summary="Unified Inference Endpoint")
@action_log(action_type=ActionType.EXECUTE, node_type=NodeType.INFERENCE)
async def unified_inference(
    request: Request,
    body: InferenceRequest = Body(
        ...,
        openapi_examples={
            "ollama_chat": {
                "summary": "Ollama - Chat (with session_id)",
                "description": "Multi-turn chat with a locally hosted Ollama model. Maintains conversation history via session_id — omit it to auto-generate a new session.",
                "value": {
                    "tool": "ollama",
                    "endpoint": "chat",
                    "session_id": "session-abc123",
                    "agent_id": None,
                    "input": {
                        "model": "llama3.2",
                        "messages": [
                            {"role": "user", "content": "What is machine learning?"}
                        ]
                    }
                }
            },
            "ollama_generate": {
                "summary": "Ollama - Generate",
                "description": "Single-turn text generation from a raw prompt using a locally hosted Ollama model. No message history — use Chat for multi-turn conversations.",
                "value": {
                    "tool": "ollama",
                    "endpoint": "generate",
                    "agent_id": None,
                    "input": {
                        "model": "llama3.2",
                        "prompt": "Explain quantum computing in simple terms."
                    }
                }
            },
            "ollama_embeddings": {
                "summary": "Ollama - Embeddings",
                "description": "Generate vector embeddings for a given text using a locally hosted Ollama embedding model. Use nomic-embed-text or any embedding-capable model.",
                "value": {
                    "tool": "ollama",
                    "endpoint": "embeddings",
                    "agent_id": None,
                    "input": {
                        "model": "nomic-embed-text",
                        "input": "The quick brown fox jumps over the lazy dog"
                    }
                }
            },
            "vllm_chat_with_deployment": {
                "summary": "vLLM - Chat Completions (deployment_id)",
                "description": "OpenAI-compatible chat completions via a vLLM deployment registered in the platform. The deployment_id is resolved to its base_url automatically.",
                "value": {
                    "tool": "vllm",
                    "endpoint": "chat_completions",
                    "deployment_id": "<deployment-id>",
                    "agent_id": None,
                    "input": {
                        "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": "Tell me about deep learning."}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 512
                    }
                }
            },
            "vllm_chat_with_base_url": {
                "summary": "vLLM - Chat Completions (base_url)",
                "description": "OpenAI-compatible chat completions by supplying the vLLM server base_url directly in input. Use this when the deployment is not registered in the platform.",
                "value": {
                    "tool": "vllm",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "base_url": "http://vllm-service:8000",
                        "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                        "messages": [
                            {"role": "user", "content": "Tell me about deep learning."}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 512
                    }
                }
            },
            "vllm_completions": {
                "summary": "vLLM - Completions",
                "description": "OpenAI-compatible legacy text completions from a raw prompt via a vLLM deployment. Prefer chat_completions for instruction-tuned models.",
                "value": {
                    "tool": "vllm",
                    "endpoint": "completions",
                    "deployment_id": "<deployment-id>",
                    "agent_id": None,
                    "input": {
                        "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                        "prompt": "The future of artificial intelligence is",
                        "max_tokens": 128,
                        "temperature": 0.8
                    }
                }
            },
            "vllm_embeddings": {
                "summary": "vLLM - Embeddings",
                "description": "Generate vector embeddings for one or more texts using a vLLM embedding deployment. Accepts a string or an array of strings in the input field.",
                "value": {
                    "tool": "vllm",
                    "endpoint": "embeddings",
                    "deployment_id": "<deployment-id>",
                    "agent_id": None,
                    "input": {
                        "model": "Qwen/Qwen3-Embedding-8B",
                        "input": ["Machine learning is a subset of AI.", "Deep learning uses neural networks."]
                    }
                }
            },
            "lorax_chat_with_deployment": {
                "summary": "LoRAX - Chat Completions (deployment_id)",
                "description": "OpenAI-compatible chat completions via a LoRAX deployment registered in the platform. Supports LoRA adapter switching per request via adapter_id.",
                "value": {
                    "tool": "lorax",
                    "endpoint": "chat_completions",
                    "deployment_id": "<deployment-id>",
                    "agent_id": None,
                    "input": {
                        "model": "meta-llama/Llama-3.3-70B-Instruct",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": "Explain neural networks."}
                        ],
                        "adapter_id": "my-custom-lora",
                        "max_tokens": 256,
                        "temperature": 0.7
                    }
                }
            },
            "lorax_chat_with_base_url": {
                "summary": "LoRAX - Chat Completions (base_url)",
                "description": "OpenAI-compatible chat completions by supplying the LoRAX server base_url directly in input. Use this when the deployment is not registered in the platform.",
                "value": {
                    "tool": "lorax",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "base_url": "http://lorax-service:80",
                        "model": "meta-llama/Llama-3.3-70B-Instruct",
                        "messages": [
                            {"role": "user", "content": "Explain neural networks."}
                        ],
                        "max_tokens": 256,
                        "temperature": 0.7
                    }
                }
            },
            "lorax_generate": {
                "summary": "LoRAX - Generate",
                "description": "Single-turn text generation from a raw prompt using the LoRAX /generate endpoint. Supports per-request LoRA adapters via adapter_id in parameters.",
                "value": {
                    "tool": "lorax",
                    "endpoint": "generate",
                    "deployment_id": "<deployment-id>",
                    "agent_id": None,
                    "input": {
                        "inputs": "What is the future of AI?",
                        "parameters": {
                            "max_new_tokens": 128,
                            "temperature": 0.7
                        }
                    }
                }
            },
            "lorax_completions": {
                "summary": "LoRAX - Completions",
                "description": "OpenAI-compatible legacy text completions from a raw prompt via a LoRAX deployment. Prefer chat_completions for instruction-tuned models.",
                "value": {
                    "tool": "lorax",
                    "endpoint": "completions",
                    "deployment_id": "<deployment-id>",
                    "agent_id": None,
                    "input": {
                        "model": "meta-llama/Llama-3.3-70B-Instruct",
                        "prompt": "The future of artificial intelligence is",
                        "max_tokens": 150,
                        "temperature": 0.7
                    }
                }
            },
            "arctic_chat": {
                "summary": "Arctic - Chat Completions",
                "description": "OpenAI-compatible chat completions using a Snowflake Arctic (vLLM speculative decoding) deployment registered in the platform.",
                "value": {
                    "tool": "arctic",
                    "endpoint": "chat_completions",
                    "deployment_id": "arctic-dep-001",
                    "agent_id": None,
                    "input": {
                        "model": "Snowflake/snowflake-arctic-instruct",
                        "messages": [
                            {"role": "user", "content": "Explain transformer architecture."}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 512
                    }
                }
            },
            "arctic_completions": {
                "summary": "Arctic - Completions",
                "description": "OpenAI-compatible legacy text completions from a raw prompt using a Snowflake Arctic deployment. Prefer arctic_chat for instruction-tuned models.",
                "value": {
                    "tool": "arctic",
                    "endpoint": "completions",
                    "deployment_id": "arctic-dep-001",
                    "agent_id": None,
                    "input": {
                        "model": "Snowflake/snowflake-arctic-instruct",
                        "prompt": "The future of AI is",
                        "max_tokens": 150,
                        "temperature": 0.7
                    }
                }
            },
            "openai_chat": {
                "summary": "OpenAI - Chat Completions",
                "description": "Chat completions using an OpenAI model. Requires OPENAI_API_KEY set in the environment.",
                "value": {
                    "tool": "openai",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "model": "gpt-4o",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": "Who are you?"}
                        ],
                        "max_tokens": 512,
                        "temperature": 0.7
                    }
                }
            },
            "openai_completions": {
                "summary": "OpenAI - Completions",
                "description": "OpenAI-compatible legacy text completions from a raw prompt. Prefer chat_completions for instruction-tuned models.",
                "value": {
                    "tool": "openai",
                    "endpoint": "completions",
                    "agent_id": None,
                    "input": {
                        "model": "gpt-3.5-turbo-instruct",
                        "prompt": "The future of AI is",
                        "max_tokens": 150,
                        "temperature": 0.7
                    }
                }
            },
            "openai_embeddings": {
                "summary": "OpenAI - Embeddings",
                "description": "Generate vector embeddings for a given text using an OpenAI embedding model. Accepts a string or an array of strings.",
                "value": {
                    "tool": "openai",
                    "endpoint": "embeddings",
                    "agent_id": None,
                    "input": {
                        "model": "text-embedding-3-small",
                        "input": "The quick brown fox jumps over the lazy dog"
                    }
                }
            },
            "gemini_chat": {
                "summary": "Gemini - Chat Completions",
                "description": "Chat completions using a Google Gemini model. Requires GEMINI_API_KEY set in the environment.",
                "value": {
                    "tool": "gemini",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "model": "gemini-2.0-flash",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": "Who are you?"}
                        ],
                        "max_tokens": 512,
                        "temperature": 0.7
                    }
                }
            },
            "gemini_embeddings": {
                "summary": "Gemini - Embeddings",
                "description": "Generate vector embeddings for a given text using a Google Gemini embedding model. Requires GEMINI_API_KEY set in the environment.",
                "value": {
                    "tool": "gemini",
                    "endpoint": "embeddings",
                    "agent_id": None,
                    "input": {
                        "model": "gemini-embedding-001",
                        "input": "The quick brown fox jumps over the lazy dog"
                    }
                }
            },
            "anthropic_chat": {
                "summary": "Anthropic - Chat Completions",
                "description": "Chat completions using an Anthropic Claude model. Requires ANTHROPIC_API_KEY set in the environment.",
                "value": {
                    "tool": "anthropic",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "model": "claude-opus-4-7",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": "Who are you?"}
                        ],
                        "max_tokens": 512,
                        "temperature": 0.7
                    }
                }
            },
            "anthropic_thinking": {
                "summary": "Anthropic - Extended Thinking",
                "description": "Claude with extended thinking enabled. The model reasons internally before responding. budget_tokens controls how many tokens Claude can use for thinking.",
                "value": {
                    "tool": "anthropic",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "model": "claude-opus-4-7",
                        "thinking": {"type": "enabled", "budget_tokens": 5000},
                        "messages": [
                            {"role": "user", "content": "Solve: A train travels 120 km in 2 hours then 180 km in 3 hours. What is the average speed?"}
                        ],
                        "max_tokens": 8000
                    }
                }
            },
            "anthropic_vision": {
                "summary": "Anthropic - Vision (image input)",
                "description": "Claude vision — pass an image as base64 data URI or a public URL alongside text. Supported by claude-opus-4-7 and claude-sonnet-4-6.",
                "value": {
                    "tool": "anthropic",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "model": "claude-opus-4-7",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "What is in this image?"},
                                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<BASE64_STRING>"}}
                                ]
                            }
                        ],
                        "max_tokens": 512
                    }
                }
            },
            "gemini_vision": {
                "summary": "Gemini - Vision (image input)",
                "description": "Gemini vision — pass an image as base64 data URI or a public URL alongside text. Supported by gemini-2.5-pro and gemini-2.0-flash.",
                "value": {
                    "tool": "gemini",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "model": "gemini-2.5-pro",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Describe this image in detail."},
                                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<BASE64_STRING>"}}
                                ]
                            }
                        ],
                        "max_tokens": 1024
                    }
                }
            },
            "openai_reasoning": {
                "summary": "OpenAI - Reasoning (GPT-5)",
                "description": "GPT-5 with reasoning_effort parameter. Use low/medium/high to trade off speed vs. depth of reasoning.",
                "value": {
                    "tool": "openai",
                    "endpoint": "chat_completions",
                    "agent_id": None,
                    "input": {
                        "model": "gpt-5",
                        "reasoning_effort": "high",
                        "messages": [
                            {"role": "user", "content": "What is the 100th Fibonacci number?"}
                        ],
                        "max_tokens": 2048
                    }
                }
            },
            "vllm_vision": {
                "summary": "vLLM - Vision (Qwen2.5-VL-72B)",
                "description": "Vision inference via vLLM using Qwen2.5-VL-72B-Instruct. Deploy the model first via the deployment API, then pass image content in messages.",
                "value": {
                    "tool": "vllm",
                    "endpoint": "chat_completions",
                    "deployment_id": "<deployment-id>",
                    "agent_id": None,
                    "input": {
                        "model": "Qwen/Qwen2.5-VL-72B-Instruct",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "What objects are in this image?"},
                                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<BASE64_STRING>"}}
                                ]
                            }
                        ],
                        "max_tokens": 512
                    }
                }
            },
            "onnx_infer": {
                "summary": "ONNX - Inference",
                "description": "Run tensor inference on an ONNX model deployed on Kubeflow. Requires onnx_model (model name) and cluster. Input is a list of named tensors with shape and datatype.",
                "value": {
                    "tool": "onnx",
                    "endpoint": "infer",
                    "deployment_id": "onnx-dep-101",
                    "onnx_model": "pestle_L",
                    "cluster": "kubeflow",
                    "agent_id": None,
                    "input": {
                        "inputs": [
                            {
                                "name": "input_data",
                                "shape": [1, 224, 224, 3],
                                "datatype": "FP32",
                                "data": [0.5, 0.3, 0.8]
                            }
                        ]
                    }
                }
            }
        }
    ),
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme)
):
    
    # Validate authorization
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401, 
            detail="Authorization token is required"
        )

    token = credentials.credentials
    
    # Decode and validate JWT
    decoded_payload = decode_jwt(token)
    if not decoded_payload:
        raise HTTPException(
            status_code=401, 
            detail="Invalid or corrupted token"
        )
    
    # Extract identity
    identity = extract_identity(decoded_payload)
    tenant_id = identity.get("tenantId")
    user_id = identity.get("userId")
    
    # Validate required identity fields
    if not tenant_id:
        raise HTTPException(
            status_code=401, 
            detail="tenantId not found in token"
        )
    
    if not user_id:
        raise HTTPException(
            status_code=401, 
            detail="userId not found in token"
        )

    # Extract common fields
    tool = body.tool
    endpoint = body.endpoint
    user_input = body.input

    # Build tool_config based on tool type
    tool_config = {
        "endpoint": endpoint,
        "agent_id": body.agent_id,        # None when not provided or empty
        "deployment_id": body.deployment_id,  # None when not provided or empty
    }

    if tool == "ollama":
        session_id = body.session_id if body.session_id else str(uuid.uuid4())
        tool_config["session_id"] = session_id
    elif tool == "onnx":
        tool_config["onnx_model"] = body.onnx_model
        if body.cluster:
            tool_config["cluster"] = body.cluster

    logger.info("Queuing inference: tool=%s endpoint=%s tenantId=%s userId=%s", tool, endpoint, tenant_id, user_id)

    task = celery.send_task(
        "inference.execute",
        kwargs={"task_args": {
            "tool": tool,
            "tool_config": tool_config,
            "user_input": user_input,
            "token": token,
            "identity": identity,
        }},
        queue="inference",
        expires=3600,
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 1,
            "interval_step": 2,
            "interval_max": 10,
        },
    )
    logger.info("Queued inference.execute | tool=%s task_id=%s", tool, task.id)
    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Inference task queued. Poll /job/{task_id} for result.",
    }