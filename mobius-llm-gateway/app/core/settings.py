from pydantic_settings import BaseSettings , SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME : str = "MOBIUS-LLM-WRAPPER"
    
    VAULT_HF_TOKEN_PATH:str="secret/k8sconfig/ori-testing/hf-token"
    VAULT_K8S_CONFIG_PATH:str="secret/k8sconfig/ori-testing"
    VAULT_ADDR:str="https://vault.aidtaas.in"
    # VAULT_ADDR:str ="http://localhost:8200"
    VAULT_TOKEN:str="hvs.ruTtNmSWtNje2VurG3M3uRjA"
    # VAULT_TOKEN:str="root"
    VAULT_K8S_SECRET_BASE_PATH:str="k8sconfig/ori-testing"
    DISABLE_VAULT:bool=False
    K8S_CLUSTER_NAME:str="ori-testing"
    OLLAMA_API_URL:str="http://ollama-mobius-sales.mobiusdtaas.ai"
    PI_ENTITY_BASE_URL:str="https://igs.gov-cloud.ai/pi-entity-instances-service"
    PI_SCHEMA_ID_DEPLOYMENT:str="694d4d837387204b7154ed17"
    PI_SCHEMA_ID_INFERENCE:str="69f0986999b6ab20ed9be402"
    PI_SCHEMA_ID_DEPLOYMENT_ONNX:str="69399f58c54b1e2c7a94e48d"
    PI_SCHEMA_ID_OLLAMA_SESSION_ID:str="691b1f9219be331b9bebe0a5"
    PI_SCHEMA_ID_COST:str="69f2094499b6ab20ed9be863"
    PI_SCHEMA_ID_LOAD_TEST:str="69fad1a50ce8fb76e745676e"
    PI_SCHEMA_ID_BATCH_RESULTS:str="6a0d7c68f6fcc9581c739720"
    PI_SCHEMA_ID_SYSTEM_PROMPTS:str="6a0f39f743095771fab8770f"
    PI_INSTANCE_URL:str="https://igs.gov-cloud.ai/pi-entity-instances-service"
    KUBEFLOW_INFER_BASE_URL:str="http://kubeflow-dev.mobiusdtaas.ai:8080"
    ADAPTER_LIST_SCHMEMA_ID:str="68f2513a323206451163512d"
    PI_COHORT_BASE_URL:str="https://igs.gov-cloud.ai/pi-cohorts-service-dbaas"
    HOLACRACY_BASE_URL:str="https://igs.gov-cloud.ai/holacracy"


    # Celery broker (RabbitMQ) — results stored via rpc:// on the same broker
    RABBITMQ_URL: str = "amqp://guest:guest@185.35.69.70:5672//"

    # Anthropic Batch resilience controls
    ANTHROPIC_BATCH_MAX_HTTP_RETRIES: int = 3
    ANTHROPIC_BATCH_BACKOFF_BASE_SECONDS: float = 1.0
    ANTHROPIC_BATCH_BACKOFF_MAX_SECONDS: float = 8.0
    ANTHROPIC_BATCH_CIRCUIT_BREAKER_THRESHOLD: int = 5
    ANTHROPIC_BATCH_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 30

    @property
    def _MAX_HTTP_RETRIES(self) -> int:
        return self.ANTHROPIC_BATCH_MAX_HTTP_RETRIES

    @property
    def _BACKOFF_BASE_SECONDS(self) -> float:
        return self.ANTHROPIC_BATCH_BACKOFF_BASE_SECONDS

    @property
    def _BACKOFF_MAX_SECONDS(self) -> float:
        return self.ANTHROPIC_BATCH_BACKOFF_MAX_SECONDS

    @property
    def _CIRCUIT_BREAKER_THRESHOLD(self) -> int:
        return self.ANTHROPIC_BATCH_CIRCUIT_BREAKER_THRESHOLD

    @property
    def _CIRCUIT_BREAKER_COOLDOWN_SECONDS(self) -> int:
        return self.ANTHROPIC_BATCH_CIRCUIT_BREAKER_COOLDOWN_SECONDS

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        protected_namespaces=()
    )
    
settings = Settings()
    
