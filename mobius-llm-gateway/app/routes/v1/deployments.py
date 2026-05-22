from fastapi import APIRouter, Security, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
from datetime import datetime
from typing import List
from kubernetes.client import ApiException
import httpx
import asyncio

from app.utils.utils import decode_jwt, validate_vault_before_deployment, parse_k8s_error, format_deployment_info
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, ActionSource, NodeType
from mobius_error import ApiException as MobiusApiException
from app.core.errors import GatewayErrors
from fastapi import Request
from app.schemas.k8s_deployer import (
    DeploymentCreateRequest,
    DeploymentCreateResponse,
    DeploymentListItem,
    DeleteResponse,
    ERROR_RESPONSES,
    DeploymentCreateResponseEnveloped,
    DeploymentListResponseEnveloped,
    DeleteResponseEnveloped
)
from app.services.deployment.k8s_deployer import K8sDeployer, DeploymentError
from app.core.settings import settings

logger = logging.getLogger(__name__)

# Initialize router and deployer
router = APIRouter(prefix="/deployments", tags=["deployments"])
_deployer = K8sDeployer()
auth_scheme = HTTPBearer()

@router.post(
    "",
    response_model=DeploymentCreateResponse,
    responses={
        201: {"model": DeploymentCreateResponseEnveloped},
        **ERROR_RESPONSES
    },
    status_code=status.HTTP_201_CREATED,
    summary="Create Kubernetes Deployment",
    description="Create a Kubernetes deployment and service for a model."
)
@action_log(action_type=ActionType.CREATE, node_type=NodeType.DEPLOYMENT)
async def create_deployment(
    request: Request,
    payload: DeploymentCreateRequest,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")
    token = credentials.credentials
    try:
        logger.info(f"Received deployment request for Model ID: {payload.model_id}")
        
        if not settings.DISABLE_VAULT:
            logger.info("Validating Vault configuration...")
            vault_status = await validate_vault_before_deployment(payload.namespace)
            
            if not vault_status["success"]:
                logger.error(f"Vault validation failed: {vault_status['message']}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "Vault Configuration Error",
                        "stage": "pre_deployment_validation",
                        "message": vault_status["message"],
                        "details": vault_status["details"],
                        "suggestion": vault_status["suggestion"],
                        "namespace": payload.namespace,
                        "model_id": payload.model_id,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                )
            logger.info("✓ Vault validation passed")
        
        # Execute deployment
        logger.info("Starting deployment execution...")
        result = await _deployer.create_deployment_and_service(payload, token)
        logger.info(f"✓ DEPLOYMENT COMPLETED SUCCESSFULLY: {result.get('deploymentId')}")
        return result
        
    except DeploymentError as e:
        error_detail = e.details or {}
        error_detail.update({
            "error": "Deployment Failed",
            "stage": e.stage,
            "message": e.message,
            "namespace": payload.namespace,
            "model_id": payload.model_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Specific mapping for quota exceedance
        if error_detail.get("is_quota_error"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_detail)
            
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_detail)
    except ApiException as e:
        error_detail = parse_k8s_error(e, payload.namespace)
        error_detail.update({"namespace": payload.namespace, "model_id": payload.model_id})
        raise HTTPException(status_code=e.status or status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_detail)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during deployment: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Unexpected Error",
                "message": str(e),
                "namespace": payload.namespace,
                "model_id": payload.model_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

@router.get(
    "",
    response_model=List[DeploymentListItem],
    responses={
        200: {"model": DeploymentListResponseEnveloped},
        404: ERROR_RESPONSES[404],
        403: ERROR_RESPONSES[403],
        500: ERROR_RESPONSES[500]
    },
    summary="List Deployments",
    description="List all deployments in a namespace with their current status"
)
@action_log(action_type=ActionType.READ, node_type=NodeType.DEPLOYMENT)
async def list_deployments(
    request: Request,
    namespace: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")
    try:
        logger.info(f"Fetching deployments in namespace: {namespace}")
        deployments = await asyncio.to_thread(_deployer.apps_api.list_namespaced_deployment, namespace=namespace)
        
        deployment_list = []
        for deployment in deployments.items:
            info = format_deployment_info(deployment)
            deployment_list.append(DeploymentListItem(
                deploymentId=info["uid"],
                name=info["name"],
                namespace=info["namespace"],
                replicas=info["replicas"],
                ready_replicas=info["ready_replicas"],
                timestamp=info["createAt"] or info.get("timestamp") or datetime.utcnow().isoformat(),
                image=info["image"],
                status=info["status"]
            ))
        
        logger.info(f"✓ Found {len(deployment_list)} deployments in namespace: {namespace}")
        return deployment_list
        
    except ApiException as e:
        raise HTTPException(status_code=e.status or status.HTTP_500_INTERNAL_SERVER_ERROR, detail=parse_k8s_error(e, namespace))
    except Exception as e:
        logger.error(f"Unexpected error listing deployments: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Unexpected Error",
                "message": str(e),
                "namespace": namespace,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

@router.get(
    "/{deployment_name}",
    response_model=DeploymentCreateResponse,
    responses={
        200: {"model": DeploymentCreateResponseEnveloped},
        404: ERROR_RESPONSES[404], 
        403: ERROR_RESPONSES[403]
    },
    summary="Get Deployment Details",
    description="Get detailed information about a specific deployment"
)
@action_log(action_type=ActionType.READ, node_type=NodeType.DEPLOYMENT)
async def get_deployment(
    request: Request,
    namespace: str,
    deployment_name: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")
    try:
        logger.info(f"Fetching deployment '{deployment_name}' in namespace: {namespace}")
        deployment = await asyncio.to_thread(_deployer.apps_api.read_namespaced_deployment, name=deployment_name, namespace=namespace)
        
        service_name = f"{deployment_name}-svc"
        service_metadata = None
        try:
            service = await asyncio.to_thread(_deployer.core_api.read_namespaced_service, name=service_name, namespace=namespace)
            from kubernetes.client import ApiClient
            service_metadata = ApiClient().sanitize_for_serialization(service)
        except ApiException:
            logger.warning(f"Service '{service_name}' not found for deployment")
        
        from kubernetes.client import ApiClient
        deployment_metadata = ApiClient().sanitize_for_serialization(deployment)
        
        return DeploymentCreateResponse(
            deploymentId=deployment.metadata.uid,
            serviceName=service_name if service_metadata else deployment.metadata.name,
            namespace=namespace,
            deployment_metadata=deployment_metadata,
            service_metadata=service_metadata
        )
    except ApiException as e:
        error_detail = parse_k8s_error(e, namespace, deployment_name)
        if e.status == 404:
            error_detail["message"] = f"Deployment '{deployment_name}' not found in namespace '{namespace}'"
        raise HTTPException(status_code=e.status or status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_detail)
    except Exception as e:
        logger.error(f"Unexpected error getting deployment: {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "Unexpected Error", "message": str(e)})

@router.delete(
    "/{deployment_name}",
    response_model=DeleteResponse,
    responses={
        200: {"model": DeleteResponseEnveloped},
        404: ERROR_RESPONSES[404], 
        403: ERROR_RESPONSES[403]
    },
    summary="Delete Deployment",
    description="Delete a deployment and its associated service"
)
@action_log(action_type=ActionType.DELETE, node_type=NodeType.DEPLOYMENT)
async def delete_deployment(
    namespace: str,
    deployment_name: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")
    try:
        logger.info(f"Deleting deployment '{deployment_name}' in namespace: {namespace}")
        await asyncio.to_thread(_deployer.apps_api.delete_namespaced_deployment, name=deployment_name, namespace=namespace)
        
        # Resolve model_id from deployment labels to find the shared service
        deployment = await asyncio.to_thread(_deployer.apps_api.read_namespaced_deployment, name=deployment_name, namespace=namespace)
        model_id = deployment.metadata.labels.get("model")
        
        await asyncio.to_thread(_deployer.apps_api.delete_namespaced_deployment, name=deployment_name, namespace=namespace)
        
        if model_id:
            service_name = f"{model_id.lower().replace('/', '-').replace('.', '-')}-svc"
            # Check if any other deployments for this model exist before deleting service
            label_selector = f"model={model_id.lower().replace('/', '-').replace('.', '-')}"
            other_deps = await asyncio.to_thread(_deployer.apps_api.list_namespaced_deployment, namespace=namespace, label_selector=label_selector)
            
            if len(other_deps.items) == 0:
                try:
                    await asyncio.to_thread(_deployer.core_api.delete_namespaced_service, name=service_name, namespace=namespace)
                    logger.info(f"✓ Deleted shared service '{service_name}' as it was the last deployment for model.")
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(f"Failed to delete service '{service_name}': {e}")
        
        return DeleteResponse(
            message=f"Deployment '{deployment_name}' deleted successfully",
            deployment_name=deployment_name,
            namespace=namespace
        )
    except ApiException as e:
        raise HTTPException(status_code=e.status or status.HTTP_500_INTERNAL_SERVER_ERROR, detail=parse_k8s_error(e, namespace, deployment_name))
    except Exception as e:
        logger.error(f"Unexpected error deleting deployment: {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "Unexpected Error", "message": str(e)})

@router.get(
    "/status/{deployment_id}",
    response_model=dict,
    summary="Check Deployment Status by ID",
    description="Get deployment status using deployment ID"
)
@action_log(action_type=ActionType.READ, node_type=NodeType.DEPLOYMENT)
async def check_deployment_status(
    deployment_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    token = credentials.credentials
    try:
        status_data = await _deployer.check_deployment_status_by_id(deployment_id, token)
        return status_data
    except DeploymentError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "Status Check Failed", "message": e.message, "stage": e.stage})
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "Unexpected Error", "message": str(e)})

@router.delete(
    "/delete/{deployment_id}",
    response_model=DeleteResponse,
    responses={
        200: {"model": DeleteResponseEnveloped},
        404: ERROR_RESPONSES[404], 
        403: ERROR_RESPONSES[403], 
        401: {"description": "Unauthorized"}
    },
    summary="Delete Deployment by ID",
    description="Delete a deployment and its service using deployment ID"
)
@action_log(action_type=ActionType.DELETE, node_type=NodeType.DEPLOYMENT)
async def delete_deployment_by_id(
    deployment_id: str,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    token = credentials.credentials
    try:
        deployment_details = await _deployer.get_deployment_details_from_api(deployment_id, token)
        deployment_name = deployment_details.get("deployment_name")
        namespace = deployment_details.get("namespace")
        
        if not deployment_name or not namespace:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": "Invalid Deployment Data"})
        
        await asyncio.to_thread(_deployer.apps_api.delete_namespaced_deployment, name=deployment_name, namespace=namespace)
        service_name = deployment_details.get("serviceName") or f"{deployment_name}-svc"
        try:
            await asyncio.to_thread(_deployer.core_api.delete_namespaced_service, name=service_name, namespace=namespace)
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to delete service '{service_name}': {e}")
      
        return DeleteResponse(
            message=f"Deployment with ID '{deployment_id}' deleted successfully",
            deployment_name=deployment_name,
            namespace=namespace,
            deployment_id=deployment_id
        )
    except DeploymentError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "Deletion Failed", "message": e.message, "stage": e.stage})
    except ApiException as e:
        raise HTTPException(status_code=e.status or status.HTTP_500_INTERNAL_SERVER_ERROR, detail=parse_k8s_error(e))
    except Exception as e:
        logger.error(f"Unexpected error deleting deployment: {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "Unexpected Error", "message": str(e)})

@router.get("/health/vault", summary="Ping Vault /sys/health")
@action_log(action_type=ActionType.READ, node_type=NodeType.SYSTEM)
async def check_vault_health(
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
):
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")
    url = f"{settings.VAULT_ADDR}/v1/sys/health"
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url)
            if response.status_code not in [200, 429, 472, 473, 501]:
                raise HTTPException(status_code=response.status_code, detail=response.json())
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Vault health check failed", "message": str(e)})