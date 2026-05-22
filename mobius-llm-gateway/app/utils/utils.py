
import os
import json
import base64
import logging
from typing import Dict, Any
from kubernetes.client import ApiException
from datetime import datetime
from fastapi import HTTPException
logger = logging.getLogger(__name__)

def get_current_timestamp() -> str:
    now = datetime.now()

    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
    
    return formatted_time

def get_day_month() -> tuple[str, str]:
    """Return day and month strings based on current datetime"""
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m-01")
    return day, month


def parse_k8s_error(error: ApiException, namespace: str = None, resource_name: str = None) -> Dict[str, Any]:
    """
    Parse Kubernetes API exception into user-friendly error details.
    
    Args:
        error: Kubernetes API exception
        namespace: Target namespace (optional)
        resource_name: Resource name (optional)
        
    Returns:
        dict: Structured error information
    """
    error_body = {}
    try:
        error_body = json.loads(error.body) if error.body else {}
    except:
        pass
    
    error_detail = {
        "error": "Kubernetes API Error",
        "stage": "kubernetes_operation",
        "status_code": error.status,
        "message": error_body.get("message", str(error)),
        "reason": error_body.get("reason", "Unknown"),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if namespace:
        error_detail["namespace"] = namespace
    if resource_name:
        error_detail["resource_name"] = resource_name
    
    # Add specific guidance based on error type
    if error.status == 404:
        error_detail["stage"] = "resource_not_found"
        kind = error_body.get("details", {}).get("kind", "")
        name = error_body.get("details", {}).get("name", resource_name or "")
        
        if "namespaces" in kind:
            error_detail["stage"] = "namespace_validation"
            error_detail["suggestion"] = f"Namespace '{name}' does not exist. Create it first with: kubectl create namespace {name}"
        elif name:
            error_detail["suggestion"] = f"{kind.capitalize()} '{name}' not found in namespace '{namespace}'"
        else:
            error_detail["suggestion"] = "The requested resource was not found"
    
    elif error.status == 403:
        error_detail["stage"] = "authorization"
        error_detail["suggestion"] = "Insufficient permissions. Check your kubeconfig RBAC permissions for this namespace"
    
    elif error.status == 409:
        error_detail["stage"] = "resource_conflict"
        error_detail["error"] = "Resource Conflict"
        error_detail["suggestion"] = "Resource already exists. Use a different name or delete the existing resource"
    
    elif error.status == 401:
        error_detail["stage"] = "authentication"
        error_detail["suggestion"] = "Authentication failed. Check your kubeconfig credentials"
    
    elif error.status == 422:
        error_detail["stage"] = "validation_error"
        error_detail["suggestion"] = "Invalid resource specification. Check your deployment configuration"
        
        # Add more specific guidance for common 422 errors
        if "causes" in error_body.get("details", {}):
            causes = error_body["details"]["causes"]
            for cause in causes:
                field = cause.get("field", "")
                message = cause.get("message", "")
                
                if "image" in field.lower() and "required" in message.lower():
                    error_detail["suggestion"] = "Container image is required. Ensure the 'image' field is provided"
                elif "nvidia.com/gpu" in field:
                    error_detail["suggestion"] = "GPU resource requests must equal limits. Set both to the same value"
                elif "resources" in field:
                    error_detail["suggestion"] = f"Resource configuration error: {message}"
    
    else:
        error_detail["suggestion"] = "Check Kubernetes cluster status and logs for more details"
    
    # Include K8s error details if available
    if "details" in error_body:
        error_detail["k8s_details"] = error_body["details"]
    
    return error_detail


async def validate_vault_before_deployment(namespace: str) -> Dict[str, Any]:
    """
    Validate Vault connectivity and required secrets before deployment.
    """
    import asyncio
    try:
        from app.clients.vault_client import (
            check_vault_connectivity, 
            create_kubeconfig_from_vault,
            get_hf_token_b64,
            VAULT_ADDRESS,
            VAULT_K8S_SECRET_PATH,
            DISABLE_VAULT
        )
    except ImportError as e:
        logger.error(f"Failed to import vault_client: {e}")
        return {
            "success": False,
            "message": "Vault client not available",
            "details": {"error": str(e)},
            "suggestion": "Check vault_client module installation"
        }
    
    result = {
        "success": True,
        "message": "Vault validation successful",
        "details": {},
        "suggestion": ""
    }
    
    if DISABLE_VAULT:
        result["details"]["vault_disabled"] = True
        result["details"]["vault_status"] = "disabled"
        result["message"] = "Vault is disabled - using environment variables"
        logger.info("Vault is disabled via DISABLE_VAULT flag")
        return result
    
    # Check Vault connectivity (blocking hvac call)
    if not await asyncio.to_thread(check_vault_connectivity):
        result["success"] = False
        result["message"] = "Cannot connect to Vault"
        result["details"] = {
            "vault_address": VAULT_ADDRESS,
            "error": "Vault is unreachable or not responding"
        }
        result["suggestion"] = "Check if Vault service is running and VAULT_ADDR is correct"
        return result
    
    result["details"]["vault_connected"] = True
    result["details"]["vault_address"] = VAULT_ADDRESS
    
    # Check Kubeconfig availability
    try:
        kubeconfig_paths = await asyncio.to_thread(create_kubeconfig_from_vault)
        if not kubeconfig_paths:
            result["details"]["kubeconfig_warning"] = "Kubeconfig not available from Vault, using fallback"
            logger.warning("Kubeconfig not available from Vault - using fallback configuration")
        else:
            result["details"]["kubeconfig_available"] = True
            result["details"]["kubeconfig_source"] = "vault"
            result["details"]["kubeconfig_path"] = kubeconfig_paths.get("kubeconfig_path")
    except Exception as e:
        result["details"]["kubeconfig_error"] = str(e)
        logger.warning(f"Kubeconfig check failed: {e}")
    
    # Check HF Token availability
    skip_hf_validation = os.getenv("SKIP_HF_TOKEN_VALIDATION", "false").lower() == "true"
    
    if skip_hf_validation:
        result["details"]["hf_token_available"] = True
        result["details"]["hf_token_source"] = "validation_skipped"
        logger.warning("⚠️ HF token validation skipped via SKIP_HF_TOKEN_VALIDATION flag")
    else:
        try:
            token = await asyncio.to_thread(get_hf_token_b64)
            dummy_token = base64.b64encode("dummy-token".encode()).decode()
            
            if token == dummy_token:
                result["success"] = False
                result["message"] = "HuggingFace token not available"
                result["details"]["hf_token_error"] = "Using dummy token - deployment will fail"
                result["details"]["hf_token_path"] = f"{VAULT_K8S_SECRET_PATH} (hf-token key)"
                result["suggestion"] = f"Set HF_TOKEN environment variable or configure 'hf-token' key in Vault at {VAULT_K8S_SECRET_PATH}"
                return result
            
            result["details"]["hf_token_available"] = True
            result["details"]["hf_token_source"] = "vault" if not os.getenv("HF_TOKEN") else "environment"
            
        except Exception as e:
            result["success"] = False
            result["message"] = "Failed to retrieve HuggingFace token"
            result["details"]["hf_token_error"] = str(e)
            result["suggestion"] = "Check Vault HF token configuration or set HF_TOKEN env variable"
            return result
    
    logger.info(f"✓ Vault validation successful for namespace: {namespace}")
    return result


def validate_deployment_request(req) -> Dict[str, Any]:
    """
    Validate deployment request before submitting to Kubernetes.
    
    Args:
        req: DeploymentCreateRequest object
        
    Returns:
        dict: Validation result with success flag, message, and details
    """
    result = {
        "success": True,
        "message": "Request validation successful",
        "errors": []
    }
    
    # Required field validation
    if not req.name or not req.name.strip():
        result["success"] = False
        result["errors"].append({
            "field": "name",
            "message": "Deployment name is required"
        })
    
    if not req.namespace or not req.namespace.strip():
        result["success"] = False
        result["errors"].append({
            "field": "namespace",
            "message": "Namespace is required"
        })
    
    if not req.model_id or not req.model_id.strip():
        result["success"] = False
        result["errors"].append({
            "field": "model_id",
            "message": "Model ID is required"
        })
    
    if not req.image or not req.image.strip():
        result["success"] = False
        result["errors"].append({
            "field": "image",
            "message": "Container image is required"
        })
    
    # GPU validation - requests must equal limits
    if req.gpu_request != req.gpu_limit:
        result["errors"].append({
            "field": "gpu_request/gpu_limit",
            "message": f"GPU request ({req.gpu_request}) must equal GPU limit ({req.gpu_limit})",
            "suggestion": "Set both gpu_request and gpu_limit to the same value"
        })
        # This is a warning, not a failure - we'll auto-fix it
        logger.warning(f"GPU mismatch detected - will be auto-corrected to {req.gpu_limit}")
    
    # Resource format validation
    try:
        # Validate memory format (should be like "2Gi", "512Mi", etc.)
        memory_units = ["Ki", "Mi", "Gi", "Ti", "K", "M", "G", "T"]
        
        for mem_field, mem_value in [("memory_request", req.memory_request), ("memory_limit", req.memory_limit)]:
            if mem_value and not any(mem_value.endswith(unit) for unit in memory_units):
                result["errors"].append({
                    "field": mem_field,
                    "message": f"Invalid memory format: {mem_value}. Use format like '2Gi', '512Mi'",
                })
    except Exception as e:
        result["errors"].append({
            "field": "resources",
            "message": f"Resource validation error: {str(e)}"
        })
    
    if result["errors"]:
        if not any(e.get("field") in ["name", "namespace", "model_id", "image"] for e in result["errors"]):
            # Only warnings, not critical errors
            result["success"] = True
            result["message"] = "Request validation passed with warnings"
        else:
            result["success"] = False
            result["message"] = "Request validation failed"
    
    return result


def format_deployment_info(deployment) -> Dict[str, Any]:
    """
    Format Kubernetes deployment object into a clean dictionary.
    
    Args:
        deployment: Kubernetes deployment object
        
    Returns:
        dict: Formatted deployment information
    """
    return {
        "name": deployment.metadata.name,
        "namespace": deployment.metadata.namespace,
        "uid": deployment.metadata.uid,
        "createAt": deployment.metadata.creation_timestamp.isoformat() if deployment.metadata.creation_timestamp else None,
        "replicas": deployment.spec.replicas or 0,
        "ready_replicas": deployment.status.ready_replicas or 0,
        "available_replicas": deployment.status.available_replicas or 0,
        "unavailable_replicas": deployment.status.unavailable_replicas or 0,
        "image": deployment.spec.template.spec.containers[0].image if deployment.spec.template.spec.containers else None,
        "labels": deployment.metadata.labels or {},
        "status": "ready" if (deployment.status.ready_replicas or 0) == (deployment.spec.replicas or 0) else "not_ready"
    }


def get_vault_status() -> Dict[str, Any]:
    """
    Get comprehensive Vault status and configuration information.
    
    Returns:
        dict: Vault status and configuration details
    """
    try:
        from app.clients.vault_client import vault_health_check
        return vault_health_check()
    except ImportError as e:
        return {
            "error": "Vault client not available",
            "details": str(e)
        }
    except Exception as e:
        return {
            "error": "Failed to get Vault status",
            "details": str(e)
        }
    
def decode_jwt(token: str) -> dict:
    try:
        # JWT has three parts: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return {}

        # Decode payload (middle part)
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)  # pad with '='
        decoded_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(decoded_bytes)
        return payload
    except Exception as e:
        print(f"Failed to decode JWT: {e}")
        return {}
    
def get_base_url(data: dict) -> dict:
    if not data:
        return None

    service_name = data["serviceName"]
    namespace = data["namespace"]
    port = data.get("port", 80) 

    service_dns = f"{service_name}.{namespace}.svc.cluster.local"
    
    base_url = f"http://{service_dns}:{port}"
    # base_url = "http://127.0.0.1:8080"

    return {"base_url":base_url}

def extract_identity(decoded_payload: dict) -> dict:
    tenantId = decoded_payload.get("tenantId")

    userId = (
        decoded_payload.get("userId") or
        decoded_payload.get("sub") or
        decoded_payload.get("uid")
    )

    if not tenantId:
        raise HTTPException(
            status_code=401,
            detail="tenantId not found in token"
        )

    if not userId:
        raise HTTPException(
            status_code=401,
            detail="userId not found in token"
        )

    return {
        "tenantId": tenantId,
        "userId": userId
    }

