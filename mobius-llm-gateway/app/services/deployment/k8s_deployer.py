import os
import yaml
import logging
import httpx
import asyncio
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from kubernetes import client
from kubernetes.client import ApiException, ApiClient
from app.schemas.k8s_deployer import DeploymentCreateRequest
from app.core.settings import settings

serializer = ApiClient()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DeploymentError(Exception):
    def __init__(self, message: str, stage: str, details: dict = None):
        self.message = message
        self.stage = stage
        self.details = details or {}
        super().__init__(self.message)

class K8sDeployer:
    def __init__(self) -> None:
        try:
            from app.clients.vault_client import initialize_vault_kubernetes
            
            # Ensure Kubernetes is configured (blocking, but usually done at startup)
            initialize_vault_kubernetes()
            config_source = "vault"
            
            templates_dir = Path(__file__).resolve().parents[2] / "templates"
            self.jinja_env = Environment(loader=FileSystemLoader(str(templates_dir)))
            logger.info(f"Jinja loading templates from: {templates_dir}")

            self.apps_api = client.AppsV1Api()
            self.core_api = client.CoreV1Api()
            
            self.config_source = config_source
            
            # Cache for tracking which namespaces have been initialized
            self._initialized_namespaces = set()
            
            logger.info(f"✓ K8sDeployer initialized successfully (config source: {config_source})")
            
        except Exception as e:
            logger.error(f"Failed to initialize K8sDeployer: {e}", exc_info=True)
            raise DeploymentError(
                message=f"K8sDeployer initialization failed: {str(e)}",
                stage="initialization",
                details={
                    "error": str(e),
                    "kubeconfig_env": os.getenv("KUBECONFIG")
                }
            )

    async def _ensure_namespace(self, namespace: str) -> None:
        """Ensure namespace exists and is in Active state."""
        try:
            ns = await asyncio.to_thread(self.core_api.read_namespace, name=namespace)
            
            if ns.status.phase == "Terminating":
                logger.error(f"Namespace '{namespace}' is being terminated")
                raise DeploymentError(
                    message=f"Namespace '{namespace}' is being terminated and cannot accept new resources",
                    stage="namespace_validation",
                    details={"namespace": namespace, "status": ns.status.phase}
                )
            
            if ns.status.phase != "Active":
                raise DeploymentError(
                    message=f"Namespace '{namespace}' is not in Active state",
                    stage="namespace_validation",
                    details={"namespace": namespace, "status": ns.status.phase}
                )
            
            logger.info(f"✓ Namespace '{namespace}' exists and is Active")
            
        except ApiException as e:
            logger.error(f"Namespace '{namespace}' validation failed: {e}")
            raise DeploymentError(
                message=f"Namespace '{namespace}' does not exist or is not accessible",
                stage="namespace_validation",
                details={
                    "namespace": namespace,
                    "status_code": e.status,
                    "reason": e.reason
                }
            )

    async def _ensure_namespace_initialized(self, namespace: str) -> None:
        """Ensure namespace is initialized with required secrets."""
        if namespace in self._initialized_namespaces:
            return
        
        logger.info(f"Initializing namespace '{namespace}' with required secrets...")
        await self._ensure_hf_secret(namespace)
        self._initialized_namespaces.add(namespace)

    async def _ensure_hf_secret(self, namespace: str) -> None:
        secret_name = "hf-secret"
        try:
            # Check if secret already exists
            try:
                await asyncio.to_thread(self.core_api.read_namespaced_secret, name=secret_name, namespace=namespace)
                logger.info(f"✓ HuggingFace secret already exists in namespace '{namespace}'")
                return
            except ApiException as e:
                if e.status != 404:
                    raise
            
            from app.clients.vault_client import get_hf_token_b64
            token_b64 = await asyncio.to_thread(get_hf_token_b64)
            
            # Use base64 for validation
            import base64
            if token_b64 == base64.b64encode("dummy-token".encode()).decode():
                raise DeploymentError(message="HuggingFace token not configured", stage="hf_token_validation")
            
            template = self.jinja_env.get_template("lorax_secret.yaml.j2")
            rendered = template.render(namespace=namespace, token_b64=token_b64)
            body = yaml.safe_load(rendered)

            await asyncio.to_thread(self.core_api.create_namespaced_secret, namespace=namespace, body=body)
            logger.info(f"✓ HuggingFace secret created in namespace: {namespace}")
                    
        except DeploymentError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error ensuring HF secret: {e}")
            raise DeploymentError(
                message="Failed to ensure HuggingFace secret",
                stage="secret_validation",
                details={"namespace": namespace, "error": str(e)}
            )

    def _select_deployment_template(self, image: str) -> str:
        image_lower = image.lower()
        if "lorax" in image_lower:
            return "lorax_deployment_custom.yaml.j2"
        if "vllm" in image_lower:
            return "vllm_deployment.yaml.j2"
        if "onnx" in image_lower:
            return "onnx_deployment.yaml.j2"
        return "deployment.yaml.j2"
    
    def _select_service_template(self, image: str) -> str:
        image_lower = image.lower()
        if "lorax" in image_lower:
            return "lorax_service.yaml.j2"
        if "vllm" in image_lower:
            return "vllm_service.yaml.j2"
        return "lorax_service.yaml.j2"

    async def _create_deployment(self, req: DeploymentCreateRequest) -> dict:
        try:
            # Arctic Inference takes priority over image-based template selection
            arctic = req.arctic_config
            if arctic and arctic.enabled:
                template_name = "vllm_arctic_deployment.yaml.j2"
                logger.info(f"Arctic Inference enabled — using template: {template_name}")
            else:
                template_name = self._select_deployment_template(req.image)

            template = self.jinja_env.get_template(template_name)

            logger.info(f"Rendering deployment template for: {req.name}")

            # Ensure GPU values are valid
            gpu_request = str(req.gpu_request) if req.gpu_request else "0"
            gpu_limit = str(req.gpu_limit) if req.gpu_limit else "0"
            if gpu_request != gpu_limit:
                gpu_request = gpu_limit

            render_vars = dict(
                name=req.name,
                namespace=req.namespace,
                model_id=req.model_id,
                image=req.image,
                container_port=req.container_port,
                cpu_request=req.cpu_request,
                memory_request=req.memory_request,
                gpu_request=gpu_request,
                cpu_limit=req.cpu_limit,
                memory_limit=req.memory_limit,
                gpu_limit=gpu_limit,
            )

            if arctic and arctic.enabled:
                render_vars.update(
                    speculative_method=arctic.speculative_method,
                    speculator_model=arctic.speculator_model,
                    num_speculative_tokens=arctic.num_speculative_tokens,
                    tensor_parallel_size=arctic.tensor_parallel_size,
                    ulysses_sequence_parallel_size=arctic.ulysses_sequence_parallel_size,
                    vllm_device=req.vllm_device or "cuda",
                )

            rendered = template.render(**render_vars)
            
            body = yaml.safe_load(rendered)

            try:
                deployment = await asyncio.to_thread(self.apps_api.create_namespaced_deployment, namespace=req.namespace, body=body)
                return ApiClient().sanitize_for_serialization(deployment)
            except ApiException as e:
                if e.status == 409:
                    logger.warning(f"Deployment '{req.name}' already exists. Patching...")
                    deployment = await asyncio.to_thread(self.apps_api.patch_namespaced_deployment, name=req.name, namespace=req.namespace, body=body)
                    return ApiClient().sanitize_for_serialization(deployment)
                else:
                    raise DeploymentError(
                        message=f"Failed to create/patch deployment '{req.name}'",
                        stage="deployment_creation",
                        details={"status_code": e.status, "reason": e.reason}
                    )
        except DeploymentError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating deployment: {e}")
            raise DeploymentError(message="Deployment creation failed", stage="deployment_rendering", details={"error": str(e)})

    async def _create_service(self, req: DeploymentCreateRequest) -> dict:
        try:
            # Model-based service naming for scalability - must be RFC 1123 compliant
            model_slug = req.model_id.lower().replace("/", "-").replace(".", "-").replace(":", "-").replace("_", "-")
            service_name = f"{model_slug}-svc"
            
            template_name = self._select_service_template(req.image)
            template = self.jinja_env.get_template(template_name)
            
            # Note: name in template now refers to the model_id-based service name
            rendered = template.render(
                name=service_name, 
                namespace=req.namespace,
                model_id=req.model_id,
                deployment_name=req.name # Used for selectors
            )
            body = yaml.safe_load(rendered)

            try:
                svc = await asyncio.to_thread(self.core_api.create_namespaced_service, namespace=req.namespace, body=body)
                logger.info(f"✓ Service '{svc.metadata.name}' created")
                return ApiClient().sanitize_for_serialization(svc)
            except ApiException as e:
                # 1. Handle quota exceedance
                if e.status == 403 and "exceeded quota" in str(e.body).lower():
                    logger.error(f"Quota exceeded: {e.body}")
                    raise DeploymentError(
                        message="Kubernetes ResourceQuota exceeded. The maximum number of services in this namespace has been reached.",
                        stage="service_creation",
                        details={"error": "QuotaExceeded", "reason": str(e.body), "is_quota_error": True}
                    )
                # 2. Handle conflict (Update existing service)
                elif e.status == 409:
                    logger.info(f"Service '{service_name}' already exists. Patching...")
                    svc = await asyncio.to_thread(self.core_api.patch_namespaced_service, name=service_name, namespace=req.namespace, body=body)
                    return ApiClient().sanitize_for_serialization(svc)
                # 3. Other API errors
                else:
                    logger.error(f"K8s API error creating service: {e}")
                    raise DeploymentError(
                        message=f"Failed to create service: {e.reason}", 
                        stage="service_creation", 
                        details={"status_code": e.status}
                    )
        except DeploymentError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating service: {e}")
            raise DeploymentError(message=f"Service creation failed: {str(e)}", stage="service_rendering")

    async def _wait_for_service_ready(self, namespace: str, service_name: str, timeout: int = 30) -> dict:
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                service = await asyncio.to_thread(self.core_api.read_namespaced_service, name=service_name, namespace=namespace)
                
                if service.spec.type == "LoadBalancer":
                    if service.status.load_balancer.ingress:
                        ingress = service.status.load_balancer.ingress[0]
                        external_ip = ingress.ip or ingress.hostname
                        return {"service_type": "LoadBalancer", "external_ip": external_ip, "ready": True}
                else:
                    return {"service_type": service.spec.type, "cluster_ip": service.spec.cluster_ip, "ready": True}
                    
            except ApiException as e:
                if e.status != 404:
                    raise DeploymentError(message="Error checking service status", stage="service_validation", details={"error": str(e)})
            
            await asyncio.sleep(2)
        
        return {"service_type": "Unknown", "ready": False, "timeout": True}

    async def send_instance_to_api(self, instanceObj: dict, token: str) -> dict:
        clean_token = token.replace("Bearer ", "")
        base_url = settings.PI_ENTITY_BASE_URL
        schema_id = settings.PI_SCHEMA_ID_DEPLOYMENT
        
        if not schema_id or not token:
            raise DeploymentError(message="Missing PI_ENTITY configuration", stage="api_configuration")

        url = f"{base_url}/v2.0/schemas/{schema_id}/instances"
        headers = {"Authorization": f"Bearer {clean_token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, headers=headers, json=instanceObj)
                if response.status_code >= 400:
                    raise DeploymentError(message="PI-Entity API request failed", stage="api_notification", details={"status_code": response.status_code, "response": response.text})
                return response.json()
            except httpx.TimeoutException:
                raise DeploymentError(message="PI-Entity API request timed out", stage="api_notification")
            except Exception as e:
                raise DeploymentError(message="Failed to notify PI-Entity API", stage="api_notification", details={"error": str(e)})

    async def create_deployment_and_service(self, req: DeploymentCreateRequest, token) -> dict:
        token = token.replace("Bearer ", "")
        try:
            logger.info("[1/5] Validating namespace...")
            await self._ensure_namespace(namespace=req.namespace)

            logger.info("[2/5] Initializing namespace...")
            await self._ensure_namespace_initialized(namespace=req.namespace)

            logger.info("[3/5] Creating deployment...")
            deployment_metadata = await self._create_deployment(req)
            deployment_uid = deployment_metadata["metadata"]["uid"]

            logger.info("[4/5] Creating service...")
            try:
                service_metadata = await self._create_service(req)
                service_name = service_metadata["metadata"]["name"]
            except Exception as e:
                logger.warning(f"Service creation failed. Rolling back deployment '{req.name}' to prevent orphan resources.")
                try:
                    await asyncio.to_thread(self.apps_api.delete_namespaced_deployment, name=req.name, namespace=req.namespace)
                    logger.info(f"✓ Rollback successful: Deployment '{req.name}' deleted.")
                except Exception as rollback_err:
                    logger.error(f"Rollback failed: {rollback_err}")
                raise e

            service_details = await self._wait_for_service_ready(namespace=req.namespace, service_name=service_name)
            
            external_ip = service_details.get("external_ip")
            cluster_ip = service_details.get("cluster_ip")
            service_type = service_details.get("service_type")

            logger.info("[5/5] Notifying PI-Entity API...")
            instanceObj = {
                "data": [{
                    "deploymentId": deployment_uid,
                    "serviceName": service_name,
                    "namespace": req.namespace,
                    "modelId": req.model_id,
                    "image": req.image,
                    "serviceType": service_type,
                    "externalIp": external_ip,
                    "clusterIp": cluster_ip,
                    "deployment_name": req.name
                }]
            }
            try:
                await self.send_instance_to_api(instanceObj, token)
            except Exception as e:
                logger.error(f"PI-Entity API notification failed: {e}")
                logger.warning("Proceeding with deployment despite API notification failure.")

            return {
                "deploymentId": deployment_uid,
                "serviceName": service_name,
                "namespace": req.namespace,
                "modelId": req.model_id,
                "image": req.image,
                "serviceType": service_type,
                "externalIp": external_ip,
                "clusterIp": cluster_ip,
                "deployment_name": req.name
            }
            
        except Exception as e:
            logger.error(f"Deployment failed: {e}", exc_info=True)
            if isinstance(e, DeploymentError):
                raise
            raise DeploymentError(message=f"Unexpected error: {str(e)}", stage="unknown")
        
    async def _get_deployment_status(self, namespace: str, deployment_name: str) -> dict:
        """Get detailed status of a deployment including pod information."""
        try:
            deployment = await asyncio.to_thread(self.apps_api.read_namespaced_deployment, name=deployment_name, namespace=namespace)
            label_selector = f"app={deployment_name}"
            pods = await asyncio.to_thread(self.core_api.list_namespaced_pod, namespace=namespace, label_selector=label_selector)
            
            pod_statuses = []
            running_count = 0
            pending_count = 0
            failed_count = 0
            succeeded_count = 0
            unknown_count = 0
            
            for pod in pods.items:
                pod_info = {"name": pod.metadata.name, "phase": pod.status.phase, "ready": False, "restart_count": 0, "containers": []}
                if pod.status.container_statuses:
                    for container_status in pod.status.container_statuses:
                        container_info = {
                            "name": container_status.name,
                            "ready": container_status.ready,
                            "restart_count": container_status.restart_count,
                            "state": {}
                        }
                        pod_info["restart_count"] += container_status.restart_count
                        if container_status.state.running:
                            container_info["state"] = {"status": "running", "started_at": container_status.state.running.started_at.isoformat() if container_status.state.running.started_at else None}
                        elif container_status.state.waiting:
                            container_info["state"] = {"status": "waiting", "reason": container_status.state.waiting.reason, "message": container_status.state.waiting.message}
                        elif container_status.state.terminated:
                            container_info["state"] = {"status": "terminated", "reason": container_status.state.terminated.reason, "exit_code": container_status.state.terminated.exit_code, "message": container_status.state.terminated.message}
                        pod_info["containers"].append(container_info)
                        if container_status.ready:
                            pod_info["ready"] = True
                
                if pod.status.phase == "Running": running_count += 1
                elif pod.status.phase == "Pending": pending_count += 1
                elif pod.status.phase == "Failed": failed_count += 1
                elif pod.status.phase == "Succeeded": succeeded_count += 1
                else: unknown_count += 1
                pod_statuses.append(pod_info)
            
            desired_replicas = deployment.spec.replicas or 0
            ready_replicas = deployment.status.ready_replicas or 0
            
            if ready_replicas == desired_replicas and desired_replicas > 0: overall_status = "healthy"
            elif running_count > 0 and pending_count > 0: overall_status = "deploying"
            elif pending_count > 0 and running_count == 0: overall_status = "pending"
            elif failed_count > 0: overall_status = "failed"
            elif running_count > 0 and ready_replicas < desired_replicas: overall_status = "degraded"
            else: overall_status = "unknown"
            
            return {
                "deployment_name": deployment_name,
                "namespace": namespace,
                "overall_status": overall_status,
                "desired_replicas": desired_replicas,
                "ready_replicas": ready_replicas,
                "available_replicas": deployment.status.available_replicas or 0,
                "updated_replicas": deployment.status.updated_replicas or 0,
                "pod_summary": {"total": len(pods.items), "running": running_count, "pending": pending_count, "failed": failed_count, "succeeded": succeeded_count, "unknown": unknown_count},
                "pods": pod_statuses,
                "conditions": [{"type": c.type, "status": c.status, "reason": c.reason, "message": c.message, "last_update_time": c.last_update_time.isoformat() if c.last_update_time else None} for c in (deployment.status.conditions or [])]
            }
        except ApiException as e:
            logger.error(f"Error getting deployment status: {e}")
            raise DeploymentError(message=f"Failed to get deployment status", stage="status_check", details={"namespace": namespace, "deployment_name": deployment_name, "error": str(e)})

    async def get_deployment_details_from_api(self, deployment_id: str, token: str) -> dict:
        """Fetch deployment details from PI-Entity API using deployment ID"""
        clean_token = token.replace("Bearer ", "")
        base_url = settings.PI_ENTITY_BASE_URL
        schema_id = settings.PI_SCHEMA_ID_DEPLOYMENT
        url = f"{base_url}/v3.0/schemas/{schema_id}/instances/list"
        headers = {"Authorization": f"Bearer {clean_token}"}
        payload = {"dbType": "TIDB", "entityId": "", "entityIds": [], "ownedOnly": False, "projections": [], "filter": {"deploymentId": deployment_id}}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                raise DeploymentError(message="Failed to fetch deployment details from PI-Entity", stage="api_fetch", details={"status_code": response.status_code, "response": response.text})
            data = response.json()
            if not data.get("content") or len(data["content"]) == 0:
                raise DeploymentError(message=f"Deployment not found with ID: {deployment_id}", stage="api_fetch")
            return data["content"][0]

    async def check_deployment_status_by_id(self, deployment_id: str, token: str) -> dict:
        """Check deployment status using deployment ID."""
        deployment_details = await self.get_deployment_details_from_api(deployment_id, token)
        deployment_name = deployment_details.get("deployment_name")
        namespace = deployment_details.get("namespace")
        if not deployment_name or not namespace:
            raise DeploymentError(message="Deployment details incomplete", stage="status_check")
        
        status_info = await self._get_deployment_status(namespace, deployment_name)
        status_info["modelId"] = deployment_details.get("modelId")
        return status_info

    async def cleanup_orphaned_services(self, namespace: str) -> dict:
        """
        Identify and delete services that don't have a corresponding deployment.
        This helps clear quota when partial deployments occur.
        """
        try:
            logger.info(f"Starting cleanup of orphaned services in namespace: {namespace}")
            
            # List all deployments and services
            deployments = await asyncio.to_thread(self.apps_api.list_namespaced_deployment, namespace=namespace)
            services = await asyncio.to_thread(self.core_api.list_namespaced_service, namespace=namespace)
            
            deployment_names = {d.metadata.name for d in deployments.items}
            
            cleaned_up = []
            failed_cleanup = []
            
            for svc in services.items:
                svc_name = svc.metadata.name
                
                # Check if this service belongs to an LLM gateway deployment
                # Typically they end with -svc or match the deployment name
                # We also check the 'app' label
                app_label = svc.metadata.labels.get("app") if svc.metadata.labels else None
                
                is_managed_service = False
                base_name = None
                
                if svc_name.endswith("-svc"):
                    base_name = svc_name[:-4]
                    is_managed_service = True
                elif app_label:
                    base_name = app_label
                    is_managed_service = True
                
                if is_managed_service and base_name not in deployment_names:
                    logger.info(f"Cleanup: Found orphaned service '{svc_name}' (No deployment '{base_name}')")
                    try:
                        await asyncio.to_thread(self.core_api.delete_namespaced_service, name=svc_name, namespace=namespace)
                        cleaned_up.append(svc_name)
                    except Exception as e:
                        logger.error(f"Cleanup: Failed to delete service '{svc_name}': {e}")
                        failed_cleanup.append({"name": svc_name, "error": str(e)})
            
            return {
                "status": "success",
                "namespace": namespace,
                "cleaned_up_count": len(cleaned_up),
                "cleaned_up_services": cleaned_up,
                "failed_count": len(failed_cleanup),
                "failed_cleanup": failed_cleanup
            }
            
        except ApiException as e:
            logger.error(f"Cleanup: K8s API error: {e}")
            raise DeploymentError(message="Failed to list resources for cleanup", stage="cleanup", details={"error": str(e)})
        except Exception as e:
            logger.error(f"Cleanup: Unexpected error: {e}")
            raise DeploymentError(message=f"Cleanup failed: {str(e)}", stage="cleanup")