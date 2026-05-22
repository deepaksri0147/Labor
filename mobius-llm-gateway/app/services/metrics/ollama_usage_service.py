import logging
from typing import List, Dict, Any
from app.clients.pi_client import PIClient
from app.schemas.ollama_usage import (
    OllamaUsageRequest,
    OllamaUsageResponse,
    UsageResult,
    UsageGroup,
    UsageMetrics
)
from app.services.metrics.ollama_usage_queries import OllamaUsageQueries
from app.core.settings import settings

logger = logging.getLogger(__name__)


class OllamaUsageService:
    """Service for Ollama token usage aggregation"""
    
    def __init__(self):
        self.pi_client = PIClient(settings.PI_SCHEMA_ID_DEPLOYMENT)
        self.queries = OllamaUsageQueries(settings.PI_SCHEMA_ID_INFERENCE)
    
    async def _execute_query(self, query: str, token: str) -> List[Dict[str, Any]]:
        """Execute SQL query via PIClient"""
        try:
            logger.info("Executing Ollama usage aggregation query")
            logger.debug(f"Query: {query}")
            result = await self.pi_client.execute_query(query, token)
            logger.info(f"Query returned {len(result)} rows")
            return result
        except Exception as e:
            logger.error(f"Error executing usage query: {e}")
            raise
    
    def _map_row_to_result(
        self,
        row: Dict[str, Any],
        group_by: List[str],
        metrics: List[str]
    ) -> Dict[str, Any]:
        """Map database row to result dictionary with only requested fields"""
        # Build group object with only requested dimensions
        group_data = {}
        for dimension in group_by:
            if dimension in row:
                group_data[dimension] = row[dimension]
        
        # Build metrics object with only requested metrics
        metrics_data = {}
        for metric in metrics:
            # Map response field names to metric names
            if metric == 'total_tokens' and 'total_tokens' in row:
                metrics_data['total_tokens'] = row['total_tokens']
            elif metric == 'input_tokens' and 'input_tokens' in row:
                metrics_data['input_tokens'] = row['input_tokens']
            elif metric == 'output_tokens' and 'output_tokens' in row:
                metrics_data['output_tokens'] = row['output_tokens']
            elif metric == 'request_count' and 'request_count' in row:
                metrics_data['request_count'] = row['request_count']
        
        return {
            "group": group_data,
            "metrics": metrics_data
        }
    
    async def aggregate_usage(
        self,
        request: OllamaUsageRequest,
        token: str
    ) -> OllamaUsageResponse:
        """
        Main method: Aggregate Ollama usage based on request parameters
        """
        try:
            logger.info(f"Aggregating Ollama usage for tenant: {request.tenant_id}")
            
            # Prepare query parameters
            filters_dict = None
            if request.filters:
                filters_dict = {
                    'user_id': request.filters.user_id,
                    'model': request.filters.model,
                    'session_id': request.filters.session_id
                }
                # Remove None values
                filters_dict = {k: v for k, v in filters_dict.items() if v is not None}
            
            time_range_dict = None
            if request.time_range:
                time_range_dict = {
                    'from_time': request.time_range.from_time,
                    'to_time': request.time_range.to_time
                }
            
            # Build and execute query
            query = self.queries.build_aggregate_query(
                tenant_id=request.tenant_id,
                metrics=request.metrics,
                filters=filters_dict,
                time_range=time_range_dict,
                group_by=request.group_by
            )
            
            logger.debug(f"Generated query:\n{query}")
            
            # Execute query
            raw_results = await self._execute_query(query, token)
            
            # Map results to response model
            results = []
            for row in raw_results:
                result = self._map_row_to_result(
                    row=row,
                    group_by=request.group_by or [],
                    metrics=request.metrics
                )
                results.append(result)
            
            logger.info(f"✓ Successfully aggregated {len(results)} result groups")
            
            return {
                "tenant_id": request.tenant_id,
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Error in aggregate_usage: {e}")
            raise
