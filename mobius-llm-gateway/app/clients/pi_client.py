import os
import json
import httpx
import logging
from typing import Dict, Any, List, Optional
from app.core.settings import settings

logger = logging.getLogger(__name__)

PI_ENTITY_BASE_URL = settings.PI_ENTITY_BASE_URL
PI_SCHEMA_ID_INFERENCE = settings.PI_SCHEMA_ID_INFERENCE
BASE_URL = settings.PI_ENTITY_BASE_URL


class PIClientError(Exception):
    def __init__(self, message: str, details: Dict[str, Any] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class PIClient:
    def __init__(self, deployment_schema_id, client: Optional[httpx.AsyncClient] = None):
        self.base_url = BASE_URL
        if not self.base_url:
            raise RuntimeError("PI_ENTITY_BASE_URL environment variable is not set")

        self.deployment_schema_id = deployment_schema_id
        if not self.deployment_schema_id:
            raise RuntimeError("PI_SCHEMA_ID_DEPLOYMENT environment variable is not set")

        self.inference_schema_id = PI_SCHEMA_ID_INFERENCE
        if not self.inference_schema_id:
            raise RuntimeError("PI_SCHEMA_ID_INFERENCE environment variable is not set")
        
        self.client = client or httpx.AsyncClient(timeout=30.0)

    async def get_deployment_detail(self, bearer_token: str, deployment_id: str) -> Optional[Dict[str, Any]]:
        # Reverting to v2.0 as confirmed by user
        url = f"{PI_ENTITY_BASE_URL}/v2.0/schemas/{self.deployment_schema_id}/instances/list"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}"  
        }
        
        # Increase size to 1000 for fallback search
        params = {
            "size": 1000,
            "page": 0
        }
        
        payload = {
            "dbType": "TIDB",
            "filter": {
                "deploymentId": deployment_id
            }
        }
        
        try:
            logger.info(f"🔍 [DEB] URL: {url}")
            logger.info(f"🔍 [DEB] Filter: {json.dumps(payload['filter'])}")
            
            response = await self.client.post(url, headers=headers, params=params, json=payload)
            logger.info(f"🔍 [DEB] Status: {response.status_code}")
            
            if response.status_code >= 400:
                logger.error(f"PI-Entity API Error: {response.text}")
                return None

            data = response.json()
            
            # 1. Try direct match from filter
            if data and isinstance(data, list) and len(data) > 0:
                for item in data:
                    if item.get("deploymentId") == deployment_id:
                        logger.info(f"✓ Deployment found: {item.get('serviceName', 'N/A')}")
                        return item
            
            # 2. Fallback: If filter failed, try to find it in the full list
            # Sometimes v2.0 list ignores the filter body depending on server config
            logger.warning(f"Filter approach returned {len(data) if data else 0} results. Trying fallback search...")
            
            # Fetch all to find the specific ID (in case filter was ignored)
            if data and isinstance(data, list):
                for item in data:
                    if item.get("deploymentId") == deployment_id:
                        logger.info(f"✓ Deployment found in list: {item.get('serviceName')}")
                        return item

            logger.warning(f"❌ No deployment found with ID: {deployment_id}")
            return None
            
        except httpx.TimeoutException:
            logger.error(f"Timeout while fetching deployment {deployment_id}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching deployment detail: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching deployment detail: {e}")
            return None
    
    async def save_inference_instance(self, instance_obj: Dict[str, Any], token: str) -> Dict[str, Any]:
        clean_token = token.replace("Bearer ", "").strip()
        
        if not self.inference_schema_id:
            raise PIClientError(
                message="Missing PI_SCHEMA_ID_INFERENCE configuration",
                details={
                    "schema_id_present": False,
                    "token_present": bool(token)
                }
            )
        
        if not instance_obj.get("inferid"):
            raise PIClientError(
                message="inferid is required in instance_obj",
                details={"instance_obj": instance_obj}
            )

        url = f"{self.base_url}/v2.0/schemas/{self.inference_schema_id}/instances"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {clean_token}"
        }
        payload = {
            "data": [instance_obj]
        }

        try:
            logger.info(f"Saving inference instance to PI-Entity API")
            
            response = await self.client.post(
                url, 
                headers=headers, 
                content=json.dumps(payload)
            )

            if response.status_code >= 400:
                logger.error(f"PI-Entity API Error [{response.status_code}]: {response.text}")
                raise PIClientError(
                    message="PI-Entity API request failed",
                    details={
                        "status_code": response.status_code,
                        "response": response.text,
                        "url": url
                    }
                )
            
            response_data = response.json()
            logger.info(f"✓ Inference instance saved successfully")
            return response_data
            
        except httpx.TimeoutException:
            logger.error("PI-Entity API request timed out after 30 seconds")
            raise PIClientError(
                message="PI-Entity API request timed out",
                details={"timeout": "30s", "url": url}
            )
        except PIClientError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error calling PI-Entity API: {e}")
            raise PIClientError(
                message="Failed to save inference instance to PI-Entity API",
                details={"error": str(e), "url": url}
            )
    
    async def save_pull_model_instance(self, instance_obj: Dict[str, Any], token: str) -> Dict[str, Any]:
        clean_token = token.replace("Bearer ", "").strip()
        from app.core.settings import settings
        PI_SCHEMA_ID_OLLAMA_SESSION_ID = settings.PI_SCHEMA_ID_OLLAMA_SESSION_ID
        
        if not instance_obj.get("session_id"):
            raise PIClientError(
                message="session_id is required in instance_obj",
                details={"instance_obj": instance_obj}
            )

        url = f"{self.base_url}/v2.0/schemas/{PI_SCHEMA_ID_OLLAMA_SESSION_ID}/instances"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {clean_token}"
        }
        payload = {
            "data": [instance_obj]
        }

        try:
            logger.info(f"Saving model pull instance to PI-Entity API (session: {instance_obj.get('session_id')})")
            
            response = await self.client.post(
                url, 
                headers=headers, 
                content=json.dumps(payload)
            )

            if response.status_code >= 400:
                logger.error(f"PI-Entity API Error [{response.status_code}]: {response.text}")
                raise PIClientError(
                    message="PI-Entity API request failed for model pull",
                    details={
                        "status_code": response.status_code,
                        "response": response.text,
                        "url": url
                    }
                )
            
            response_data = response.json()
            logger.info(f"✓ Model pull instance saved successfully")
            return response_data
            
        except httpx.TimeoutException:
            logger.error("PI-Entity API request timed out after 30 seconds")
            raise PIClientError(
                message="PI-Entity API request timed out for model pull",
                details={"timeout": "30s", "url": url}
            )
        except PIClientError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error calling PI-Entity API for model pull: {e}")
            raise PIClientError(
                message="Failed to save model pull instance to PI-Entity API",
                details={"error": str(e), "url": url}
            )
            
    async def execute_query(self, query: str, token: str) -> List[Dict[str, Any]]:
        clean_token = token.replace("Bearer ", "").strip()

        PI_COHORT_BASE_URL = settings.PI_COHORT_BASE_URL
        url = f"{PI_COHORT_BASE_URL}/v1.0/cohorts/adhoc"

        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {clean_token}"
        }
        
        payload = {
            "type": "TIDB",
            "definition": query
        }

        response = await self.client.post(url, headers=headers, json=payload)
        response.raise_for_status()

        result = response.json()

        if (
            isinstance(result, dict)
            and result.get("status") == "success"
            and isinstance(result.get("data"), list)
        ):
            return result["data"]

        raise PIClientError(
            message="Invalid PI Cohorts response format",
            details={"response": result}
        )

    async def save_batch_results(self, instances: List[Dict[str, Any]], token: str) -> None:
        clean_token = token.replace("Bearer ", "").strip()
        schema_id = settings.PI_SCHEMA_ID_BATCH_RESULTS
        url = f"{self.base_url}/v2.0/schemas/{schema_id}/instances"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {clean_token}",
        }
        payload = {"data": instances}
        logger.info(f"Saving batch results | url={url} rows={len(instances)}")
        try:
            response = await self.client.post(url, headers=headers, content=json.dumps(payload, default=str))
            logger.info(f"Batch results response | status={response.status_code} body={response.text[:300]}")
            if response.status_code >= 400:
                logger.error(f"PI batch results API Error [{response.status_code}]: {response.text}")
            else:
                logger.info(f"Batch results stored successfully | schema={schema_id} rows={len(instances)}")
        except Exception as e:
            logger.error(f"Failed to save batch results to PI schema: {e}")
            raise

    async def save_load_test_metrics(self, instance_obj: Dict[str, Any], token: str) -> Dict[str, Any]:
        clean_token = token.replace("Bearer ", "").strip()
        schema_id = getattr(settings, "PI_SCHEMA_ID_LOAD_TEST", "69fad1a50ce8fb76e745676e")
        
        url = f"{self.base_url}/v2.0/schemas/{schema_id}/instances"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {clean_token}"
        }
        payload = {
            "data": [instance_obj]
        }

        try:
            logger.info(f"Saving load test metrics to PI-Entity API (TestID: {instance_obj.get('test_id')})")
            response = await self.client.post(
                url,
                headers=headers,
                content=json.dumps(payload)
            )

            if response.status_code >= 400:
                logger.error(f"PI load-test API Error [{response.status_code}]: {response.text}")
                raise PIClientError(
                    message="PI-Entity API request failed for load test metrics",
                    details={"status_code": response.status_code, "response": response.text}
                )

            return response.json()

        except PIClientError:
            # Re-raise as-is so callers see the real status_code + response text,
            # not a re-wrapped version that loses those details.
            raise
        except Exception as e:
            logger.error(f"Unexpected error calling PI-Entity API for load test: {e}")
            raise PIClientError(message="Failed to save load test metrics", details={"error": str(e)})
