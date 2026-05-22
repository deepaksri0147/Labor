from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, Optional, List
from enum import Enum


class SpeculativeMethod(str, Enum):
    SUFFIX_DECODING = "suffix_decoding"
    NGRAM = "ngram"


class ArcticInferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=True)
    speculative_method: Optional[SpeculativeMethod] = Field(
        default=None,
        description="'suffix_decoding' — model-free, best for agentic/repetitive workloads. 'ngram' — n-gram based speculation. Omit to use a trained speculator model.",
    )
    speculator_model: Optional[str] = Field(
        default=None,
        description="HuggingFace ID of a trained LSTM/MLP speculator model. Used when speculative_method is not set.",
    )
    num_speculative_tokens: int = Field(
        default=5,
        description="Number of tokens to speculate per step.",
        ge=1,
        le=20,
    )
    tensor_parallel_size: Optional[int] = Field(
        default=None,
        description="Tensor parallel size for the main model across GPUs.",
    )
    ulysses_sequence_parallel_size: Optional[int] = Field(
        default=None,
        description="Enable Arctic Ulysses sequence parallelism. Set to number of GPUs to split sequence across.",
    )

class ErrorDetail(BaseModel):
    error: str = Field(..., description="Error category")
    stage: str = Field(..., description="Deployment stage where failure occurred")
    message: str = Field(..., description="Human-readable error message")
    namespace: str
    model_id: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    details: Optional[Dict[str, Any]] = None

class ErrorResponse(BaseModel):
    status: str = "error"
    statusCode: int = 500
    message: str = Field(..., description="Stringified ErrorDetail or summary")
    details: ErrorDetail

ERROR_RESPONSES = {
    400: {"description": "Bad Request", "model": ErrorResponse},
    401: {"description": "Unauthorized"},
    403: {"description": "Forbidden (e.g. Quota Exceeded)", "model": ErrorResponse},
    404: {"description": "Not Found"},
    500: {"description": "Internal Server Error (Deployment Failed)", "model": ErrorResponse},
}

class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

class DeploymentCreateRequest(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        protected_namespaces=(),
        json_schema_extra={
            "example": {
                "name": "llama-3-8b-deployment",
                "namespace": "dev-mobius",
                "model_id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "image": "vllm/vllm-openai:latest",
                "container_port": 8000,
                "cpu_request": "4",
                "memory_request": "16Gi",
                "gpu_request": "1",
                "cpu_limit": "8",
                "memory_limit": "32Gi",
                "gpu_limit": "1"
            }
        }
    )
    name: str = Field(..., description="Deployment name", min_length=1, max_length=63)
    namespace: str = Field(..., description="Kubernetes namespace", min_length=1, max_length=63)
    model_id: str = Field(..., description="HuggingFace model ID")
    image: str = Field(..., description="Container image")
    container_port: int = Field(default=8080, description="Container port", ge=1, le=65535)
    cpu_request: str = Field(default="1", description="CPU request (e.g., '1', '500m')")
    memory_request: str = Field(default="2Gi", description="Memory request (e.g., '2Gi', '512Mi')")
    gpu_request: Optional[str] = Field(default=None, description="GPU request")
    cpu_limit: str = Field(default="2", description="CPU limit (e.g., '2', '1000m')")
    memory_limit: str = Field(default="4Gi", description="Memory limit (e.g., '4Gi', '1Gi')")
    gpu_limit: Optional[str] = Field(default=None, description="GPU limit")
    arctic_config: Optional[ArcticInferenceConfig] = Field(
        default=None,
        description="Optional Arctic Inference plugin config. When set, deploys vLLM with Snowflake ArcticInference enabled for faster speculative decoding.",
    )
    vllm_device: Optional[str] = Field(
        default=None,
        description="vLLM device type. Use 'cpu' for CPU-only clusters (no GPU). Defaults to 'cuda'.",
    )


class DeploymentCreateResponse(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "deploymentId": "170ed9e7-598f-441a-a9af-e4757bf35ce6",
                "serviceName": "llama-3-8b-deployment",
                "namespace": "dev-mobius",
                "modelId": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "image": "vllm/vllm-openai:latest",
                "serviceType": "ClusterIP",
                "externalIp": None,
                "clusterIp": "10.96.45.12",
                "deployment_name": "llama-3-8b-deployment"
            }
        }
    )
    deploymentId: str = Field(..., description="Kubernetes deployment UID")
    serviceName: str = Field(..., description="Kubernetes service name")
    namespace: str = Field(..., description="Kubernetes namespace")
    modelId: str = Field(..., description="modelId")
    image: str = Field(..., description="image")
    serviceType: str = Field(..., description="Kubernetes serviceType")
    externalIp: Optional[str] = Field(default=None, description="DEPLOYMENT externalIp")
    clusterIp: Any = Field(..., description="clusterIp DEPLOYMENT")
    deployment_name: str = Field(..., description="Deployment name")


class DeploymentListItem(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "deploymentId": "170ed9e7-598f-441a-a9af-e4757bf35ce6",
                "name": "llama-3-8b-deployment",
                "namespace": "dev-mobius",
                "replicas": 1,
                "ready_replicas": 1,
                "timestamp": "2026-05-19T08:00:00.000Z",
                "image": "vllm/vllm-openai:latest",
                "status": "ready"
            }
        }
    )
    deploymentId: str = Field(..., description="Kubernetes deployment UID")
    name: str = Field(..., description="Deployment name")
    namespace: str = Field(..., description="Kubernetes namespace")
    replicas: int = Field(..., description="Desired number of replicas")
    ready_replicas: int = Field(..., description="Number of ready replicas")
    timestamp: str = Field(..., description="Creation timestamp")
    image: Optional[str] = Field(None, description="Container image")
    status: str = Field(..., description="Deployment status (ready/not_ready)")


class VaultHealthResponse(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "vault_enabled": True,
                "vault_connected": True,
                "vault_address": "https://vault.mobiusdtaas.ai",
                "vault_base_path": "secret/08115c0a-abb3-40d4-914f-3ab4270d624f/a0334aee-9467-af6f-9083-f081c265361c",
                "kubeconfig_available": True,
                "kubeconfig_source": "vault:/tmp/kubeconfig/config",
                "hf_token_available": True,
                "configuration_source": "vault_with_env_fallback",
                "message": "All configurations available",
                "timestamp": "2025-12-12T10:30:00.000Z"
            }
        }
    )
    vault_enabled: bool = Field(..., description="Whether Vault is enabled")
    vault_connected: bool = Field(..., description="Whether Vault is reachable")
    vault_address: str = Field(..., description="Vault server address")
    vault_base_path: str = Field(..., description="Base path for secrets in Vault")
    kubeconfig_available: bool = Field(..., description="Whether kubeconfig is available")
    kubeconfig_source: Optional[str] = Field(None, description="Source of kubeconfig")
    hf_token_available: bool = Field(..., description="Whether HuggingFace token is available")
    configuration_source: str = Field(..., description="Primary configuration source")
    message: Optional[str] = Field(None, description="Additional status message")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class DeleteResponse(BaseSchema):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "message": "Deployment 'llama-3-8b-deployment' deleted successfully from namespace 'dev-mobius'",
                "deployment_name": "llama-3-8b-deployment",
                "namespace": "dev-mobius",
                "deployment_id": "170ed9e7-598f-441a-a9af-e4757bf35ce6",
                "timestamp": "2026-05-19T08:00:00.000Z"
            }
        }
    )
    message: str = Field(..., description="Success message")
    deployment_name: str = Field(..., description="Name of deleted deployment")
    namespace: str = Field(..., description="Namespace of deleted deployment")
    deployment_id: Optional[str] = Field(None, description="Deployment ID")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ContainerState(BaseSchema):
    status: str
    reason: Optional[str] = None
    message: Optional[str] = None
    started_at: Optional[str] = None
    exit_code: Optional[int] = None

class ContainerInfo(BaseSchema):
    name: str
    ready: bool
    restart_count: int
    state: ContainerState

class PodInfo(BaseSchema):
    name: str
    phase: str
    ready: bool
    restart_count: int
    containers: List[ContainerInfo]

class PodSummary(BaseSchema):
    total: int
    running: int
    pending: int
    failed: int
    succeeded: int
    unknown: int

class DeploymentCondition(BaseSchema):
    type: str
    status: str
    reason: Optional[str] = None
    message: Optional[str] = None
    last_update_time: Optional[str] = None
     
class DeploymentStatusResponse(BaseSchema):
    """Response model for deployment status endpoint"""
    deployment_name: str
    namespace: str
    status: str  # healthy, deploying, pending, failed, degraded, unknown
    replicas: Dict[str, int]  # desired, ready, available, updated
    pod_summary: PodSummary
    pods: List[PodInfo]
    conditions: List[DeploymentCondition]
    timestamp: str
class EnvelopedResponse(BaseModel):
    status: str = Field(default="success")
    statusCode: int = Field(default=200)
    data: Any

class DeploymentCreateResponseEnveloped(EnvelopedResponse):
    data: DeploymentCreateResponse

class DeploymentListResponseEnveloped(EnvelopedResponse):
    data: List[DeploymentListItem]

class DeleteResponseEnveloped(EnvelopedResponse):
    data: DeleteResponse
