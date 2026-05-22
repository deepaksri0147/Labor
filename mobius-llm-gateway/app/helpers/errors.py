
from typing import Optional, Dict, Any


class DeploymentError(Exception):
    def __init__(
        self, 
        message: str, 
        stage: str, 
        details: Optional[Dict[str, Any]] = None,
        suggestion: Optional[str] = None
    ):
        self.message = message
        self.stage = stage
        self.details = details or {}
        self.suggestion = suggestion
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        error_dict = {
            "error": "Deployment Failed",
            "stage": self.stage,
            "message": self.message,
            "details": self.details
        }
        if self.suggestion:
            error_dict["suggestion"] = self.suggestion
        return error_dict


class VaultError(DeploymentError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            stage="vault_configuration",
            details=details,
            suggestion="Check Vault connectivity and credentials"
        )


class KubernetesConnectionError(DeploymentError):
    def __init__(self, host: str, error_details: str):
        super().__init__(
            message=f"Cannot connect to Kubernetes cluster at {host}",
            stage="kubernetes_connection",
            details={
                "host": host,
                "error": error_details
            },
            suggestion=f"Check if the Kubernetes cluster at {host} is accessible and running. Verify network connectivity and kubeconfig."
        )


class NamespaceError(DeploymentError):
    def __init__(self, message: str, namespace: str, action: str = "access"):
        super().__init__(
            message=message,
            stage=f"namespace_{action}",
            details={"namespace": namespace},
            suggestion=f"Ensure namespace '{namespace}' exists and you have proper permissions"
        )


class SecretError(DeploymentError):
    def __init__(self, message: str, namespace: str, secret_name: str):
        super().__init__(
            message=message,
            stage="secret_creation",
            details={
                "namespace": namespace,
                "secret_name": secret_name
            },
            suggestion="Check if the namespace exists and you have permissions to create secrets"
        )


class ResourceCreationError(DeploymentError):
    def __init__(
        self, 
        resource_type: str, 
        resource_name: str, 
        namespace: str, 
        error_details: str
    ):
        super().__init__(
            message=f"Failed to create {resource_type} '{resource_name}'",
            stage=f"{resource_type}_creation",
            details={
                "resource_type": resource_type,
                "resource_name": resource_name,
                "namespace": namespace,
                "error": error_details
            }
        )


class APINotificationError(DeploymentError):
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[str] = None):
        details = {"message": message}
        if status_code:
            details["status_code"] = status_code
        if response:
            details["response"] = response
            
        super().__init__(
            message=message,
            stage="api_notification",
            details=details,
            suggestion="Check PI-Entity API connectivity and authentication token"
        )