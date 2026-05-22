from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Security, Body
from typing import List, Dict, Any, Optional
import logging
from app.clients.pi_client import PIClient
from app.core.settings import settings
from app.utils.decorators import action_log
from app.schemas.action_log import ActionType, NodeType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/adapters", tags=["Adapters"])

from app.core.settings import settings
ADAPTER_LIST_SCHMEMA_ID = settings.ADAPTER_LIST_SCHMEMA_ID
auth_scheme = HTTPBearer()

pi_client = PIClient(deployment_schema_id=settings.PI_SCHEMA_ID_DEPLOYMENT)

@router.post("/list")
@action_log(action_type=ActionType.READ, node_type=NodeType.ADAPTER)
async def get_adapter_list(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(auth_scheme),
) -> List[Dict[str, Any]]:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authorization token is required")

    token = credentials.credentials
    try:
        
        # Build the query to fetch all records from the schema table
        query = f"""
        SELECT 
        adapterhf_id,
        modelhf_id
            FROM t_{ADAPTER_LIST_SCHMEMA_ID}_t
            WHERE adapterhf_id IS NOT NULL
            ORDER BY `timestamp` DESC;
        """
        
        logger.info(f"Fetching adapter list for schema_id: {ADAPTER_LIST_SCHMEMA_ID}")
        
        # Execute the query using PI Client
        adapters = pi_client.execute_query(query=query, token=token)
        
        logger.info(f"✓ Retrieved {len(adapters)} adapter records")
        
        return adapters
        
    except Exception as e:
        logger.error(f"Error fetching adapter list: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch adapter list: {str(e)}"
        )
