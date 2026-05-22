from mobius_error import Error, CommonErrors

class GatewayErrors:
    """Standardized error definitions for the LLM Gateway"""
    
    # Deployment Errors
    DEPLOYMENT_NOT_FOUND = Error(404, 404001, "Deployment not found", "Verify the deployment ID and ensure it is active.")
    INSUFFICIENT_RESOURCES = Error(400, 400002, "Insufficient cluster resources", "Scale down other deployments or add nodes.")
    VAULT_CONNECTION_FAILED = Error(500, 500001, "Vault connection failure", "Check VAULT_ADDR and VAULT_TOKEN configuration.")
    K8S_API_ERROR = Error(500, 500002, "Kubernetes API error", "Check cluster connectivity and permissions.")
    QUOTA_EXCEEDED = Error(403, 403001, "Resource quota exceeded", "Clean up unused resources or request a quota increase.")
    
    # Inference Errors
    MODEL_NOT_READY = Error(503, 503001, "Model not ready", "The model is still loading or the backend service is unavailable.")
    INVALID_INFERENCE_PARAMS = Error(400, 400003, "Invalid inference parameters", "Review the request payload for schema violations.")
    
    # PI Engine Errors
    PI_CLIENT_ERROR = Error(502, 502001, "PI Engine communication failure", "The PI Entity API returned an error or is unreachable.")
    
    # Generic Reuse from CommonErrors
    UNEXPECTED_ERROR = CommonErrors.UNEXPECTED_ERROR
    UNAUTHORIZED = Error(401, 401001, "Unauthorized access", "Ensure a valid Bearer token is provided in the Authorization header.")
