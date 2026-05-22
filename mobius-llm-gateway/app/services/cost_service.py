import httpx
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import HTTPException

from app.core.settings import settings

logger = logging.getLogger(__name__)

HOLACRACY_BASE_URL = settings.HOLACRACY_BASE_URL
PI_ENTITY_BASE_URL = settings.PI_ENTITY_BASE_URL
PI_SCHEMA_ID_INFERENCE = settings.PI_SCHEMA_ID_INFERENCE
PI_SCHEMA_ID_COST = settings.PI_SCHEMA_ID_COST


class CostService:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.client = client or httpx.AsyncClient(timeout=30.0)

    async def _get_active_rate_card_id(self, agent_id: str, tenant_id: str, token: str) -> str:
        url = f"{HOLACRACY_BASE_URL}/v2.0/sub-alliances/filter?page=0&size=10"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {
            "multiLevelConditionalFilter": {
                "logicalOperator": "AND",
                "filters": [
                    {
                        "conditions": [
                            {"field": "sellerProductListingId", "operator": "EQUAL", "value": agent_id},
                            {"field": "buyerId", "operator": "EQUAL", "value": tenant_id}
                        ]
                    }
                ]
            }
        }
        try:
            response = await self.client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Sub-alliance API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to reach sub-alliance API: {str(e)}")

        content = data.get("content", [])
        if not content:
            raise HTTPException(status_code=404, detail=f"No sub-alliance found for agent_id={agent_id} and tenant_id={tenant_id}")

        active_rate_card = content[0].get("activeRateCard")
        if not active_rate_card:
            raise HTTPException(status_code=404, detail="activeRateCard not found in sub-alliance response")

        return active_rate_card

    async def _get_fixed_fee(self, rate_card_id: str, token: str) -> Dict[str, Any]:
        url = f"{HOLACRACY_BASE_URL}/v1.0/rate-cards/{rate_card_id}"
        headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
        try:
            response = await self.client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Rate card API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to reach rate-cards API: {str(e)}")

        return {
            "rate_card_id": data.get("id"),
            "rate_card_name": data.get("name"),
            "currency": data.get("currency", "USD"),
            "paas_fixed_fee": data.get("paasFee", {}).get("fixedFee", 0.0),
            "saas_fixed_fee": data.get("saasFee", {}).get("fixedFee", 0.0),
            "api_base_fee": data.get("apiCountFee", {}).get("baseFee", 0.0),
            "per_api_fee": data.get("apiCountFee", {}).get("perApiFee", 0.0),
        }

    async def _get_inference_tokens(self, agent_id: str, tenant_id: str, token: str) -> Dict[str, Any]:
        url = f"{PI_ENTITY_BASE_URL}/v2.0/schemas/{PI_SCHEMA_ID_INFERENCE}/instances/list?size=2000&page=0"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {
            "dbType": "TIDB",
            "filter": {
                "agent_id": agent_id,
                "tenantid": tenant_id
            }
        }
        try:
            response = await self.client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Inference schema API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to reach inference schema API: {str(e)}")

        records = data if isinstance(data, list) else data.get("content", data if isinstance(data, list) else [])
        if not isinstance(records, list):
            records = []

        total_input_tokens = sum(float(r.get("input_tokens") or 0) for r in records)
        total_output_tokens = sum(float(r.get("output_tokens") or 0) for r in records)
        total_tokens = sum(float(r.get("tokens") or 0) for r in records)

        return {
            "inference_count": len(records),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
        }

    async def _store_cost_record(self, cost_data: Dict[str, Any], token: str) -> None:
        clean_token = token.replace("Bearer ", "").strip()
        url = f"{PI_ENTITY_BASE_URL}/v2.0/schemas/{PI_SCHEMA_ID_COST}/instances"
        headers = {"Authorization": f"Bearer {clean_token}", "Content-Type": "application/json"}
        payload = {"data": [cost_data]}
        try:
            response = await self.client.post(url, headers=headers, content=json.dumps(payload))
            if response.status_code >= 400:
                logger.error(f"Failed to store cost record [{response.status_code}]: {response.text}")
                return
            logger.info(f"Cost record stored for agent_id={cost_data.get('agent_id')}")
        except Exception as e:
            logger.error(f"Failed to store cost record: {str(e)}")

    async def calculate_cost(self, agent_id: str, tenant_id: str, token: str) -> Dict[str, Any]:
        rate_card_id = await self._get_active_rate_card_id(agent_id, tenant_id, token)
        logger.info(f"Active rate card: {rate_card_id} for agent={agent_id}")

        fee_info = await self._get_fixed_fee(rate_card_id, token)
        logger.info(f"Rate card fees: {fee_info}")

        token_info = await self._get_inference_tokens(agent_id, tenant_id, token)
        logger.info(f"Token usage: {token_info}")

        fixed_fee = fee_info["paas_fixed_fee"]

        result = {
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "rate_card_id": fee_info["rate_card_id"],
            "rate_card_name": fee_info["rate_card_name"],
            "currency": fee_info["currency"],
            "fixed_fee": fixed_fee,
            "fee_breakdown": {
                "paas_fixed_fee": fee_info["paas_fixed_fee"],
                "saas_fixed_fee": fee_info["saas_fixed_fee"],
                "api_base_fee": fee_info["api_base_fee"],
                "per_api_fee": fee_info["per_api_fee"],
            },
            "inference_count": token_info["inference_count"],
            "total_input_tokens": token_info["total_input_tokens"],
            "total_output_tokens": token_info["total_output_tokens"],
            "total_tokens": token_info["total_tokens"],
            "cost": {
                "input_tokens_cost": round(token_info["total_input_tokens"] * fixed_fee, 6),
                "output_tokens_cost": round(token_info["total_output_tokens"] * fixed_fee, 6),
                "total_tokens_cost": round(token_info["total_tokens"] * fixed_fee, 6),
            },
            "event_timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }

        await self._store_cost_record(result, token)

        return result
