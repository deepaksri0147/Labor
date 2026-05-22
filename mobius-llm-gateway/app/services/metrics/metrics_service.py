from datetime import datetime
from typing import List, Optional, Dict
from app.services.metrics.unified_metrics_queries import UnifiedMetricsQueries
from app.schemas.metrics_model import (
    OllamaMetrics, OllamaModelMetrics, 
    LoraxMetrics, LoraxServiceMetrics, 
    ONNXMetrics, ONNXModelMetrics, 
    VLLMMetrics, VLLMServiceMetrics,
    MetricsRequest, MetricsResponse
)
from app.core.settings import settings


class UnifiedMetricsService:
    """Service to fetch and aggregate metrics for all tools"""
    
    def __init__(self, pi_client):
        PI_INFERENCE = settings.PI_SCHEMA_ID_INFERENCE
        self.pi_client = pi_client
        self.queries = UnifiedMetricsQueries(PI_INFERENCE)
    
    async def _execute_query(self, query: str, token: str) -> List[Dict]:
        """Execute SQL query"""
        return await self.pi_client.execute_query(query, token)
    
    def _should_include_tool(self, tool: str, tool_list: Optional[List[str]]) -> bool:
        """Check if tool should be included based on filter"""
        if tool_list is None or len(tool_list) == 0:
            return True
        return tool in tool_list
    
    async def get_ollama_metrics(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
        token: str
    ) -> Optional[OllamaMetrics]:
        """Fetch complete Ollama metrics"""
        try:
            summary_query = self.queries.get_ollama_summary_query(
                tenant_id, user_id, start_time, end_time
            )
            summary_result = self._execute_query(summary_query, token)
            
            if not summary_result or summary_result[0]['total_hits'] == 0:
                return None
            
            summary = summary_result[0]
            
            models_query = self.queries.get_ollama_models_query(
                tenant_id, user_id, start_time, end_time
            )
            
            models_result = self._execute_query(models_query, token)
            
            models = [
                OllamaModelMetrics(**model) 
                for model in models_result
            ]
            
            return OllamaMetrics(
                total_hits=summary['total_hits'],
                total_tokens=summary['total_tokens'],
                total_input_tokens=summary['total_input_tokens'],
                total_output_tokens=summary['total_output_tokens'],
                avg_latency_ms=round(summary['avg_latency_ms'], 2),
                throughput_tokens_per_sec=round(summary['throughput_tokens_per_sec'], 2),
                models=models
            )
        except Exception as e:
            print(f"Error fetching Ollama metrics: {e}")
            return None
    
    async def get_lorax_metrics(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
        token: str
    ) -> Optional[LoraxMetrics]:
        """Fetch complete LoRAX metrics"""
        try:
            summary_query = self.queries.get_lorax_summary_query(
                tenant_id, user_id, start_time, end_time
            )
            summary_result = self._execute_query(summary_query, token)
            
            if not summary_result or summary_result[0]['total_hits'] == 0:
                return None
            
            summary = summary_result[0]
            
            services_query = self.queries.get_lorax_service_query(
                tenant_id, user_id, start_time, end_time
            )

            
            services_result = self._execute_query(services_query, token)
            

            services = [
                LoraxServiceMetrics(**service)
                for service in services_result
                if service.get('service_name')
            ]
            
            return LoraxMetrics(
                total_hits=summary['total_hits'],
                total_tokens=summary['total_tokens'],
                total_input_tokens=summary['total_input_tokens'],
                total_output_tokens=summary['total_output_tokens'],
                avg_latency_ms=round(summary['avg_latency_ms'], 2),
                services=services
            )
        except Exception as e:
            print(f"Error fetching LoRAX metrics: {e}")
            return None
    
    async def get_vllm_metrics(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
        token: str
    ) -> Optional[VLLMMetrics]:
        """Fetch complete vLLM metrics"""
        try:
            print("Fetching vLLM metrics...")
            summary_query = self.queries.get_vllm_summary_query(
                tenant_id, user_id, start_time, end_time
            )
            print("vLLM services result:", summary_query)
            
            summary_result = self._execute_query(summary_query, token)
            
            
            if not summary_result or summary_result[0]['total_hits'] == 0:
                return None
            
            summary = summary_result[0]
            
            services_query = self.queries.get_vllm_services_query(
                tenant_id, user_id, start_time, end_time
            )
            services_result = self._execute_query(services_query, token)
            
            
            services = [
                VLLMServiceMetrics(**service)
                for service in services_result
            ]
            
            return VLLMMetrics(
                total_hits=summary['total_hits'],
                total_tokens=summary['total_tokens'],
                avg_latency_ms=round(summary['avg_latency_ms'], 2),
                throughput_tokens_per_sec=round(summary['throughput_tokens_per_sec'], 2),
                services=services
            )
        except Exception as e:
            print(f"Error fetching vLLM metrics: {e}")
            return None
    
    async def get_onnx_metrics(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
        token: str
    ) -> Optional[ONNXMetrics]:
        """Fetch complete ONNX metrics"""
        try:
            summary_query = self.queries.get_onnx_summary_query(
                tenant_id, user_id, start_time, end_time
            )
            summary_result = self._execute_query(summary_query, token)
            
            if not summary_result or summary_result[0]['total_hits'] == 0:
                return None
            
            summary = summary_result[0]
            
            models_query = self.queries.get_onnx_models_query(
                tenant_id, user_id, start_time, end_time
            )
            models_result = self._execute_query(models_query, token)
            
            models = [
                ONNXModelMetrics(**model)
                for model in models_result
            ]
            
            return ONNXMetrics(
                total_hits=summary['total_hits'],
                avg_latency_ms=round(summary['avg_latency_ms'], 2),
                models=models
            )
        except Exception as e:
            print(f"Error fetching ONNX metrics: {e}")
            return None
    
    async def get_unified_metrics(
        self,
        tenant_id: str,
        user_id: str,
        request: MetricsRequest,
        token: str
    ) -> MetricsResponse:
        """
        Main method: Get unified metrics for all requested tools
        
        Returns metrics for all tools by default, or only requested tools
        """
        start_time = request.start_time
        end_time = request.end_time if request.end_time else datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        
        ollama_metrics = None
        lorax_metrics = None
        vllm_metrics = None
        onnx_metrics = None
        
        if self._should_include_tool('ollama', request.toolList):
            ollama_metrics = await self.get_ollama_metrics(
                tenant_id, user_id, start_time, end_time, token
            )
        
        if self._should_include_tool('lorax', request.toolList):
            lorax_metrics = await self.get_lorax_metrics(
                tenant_id, user_id, start_time, end_time, token
            )
        
        if self._should_include_tool('vllm', request.toolList):
            vllm_metrics = await self.get_vllm_metrics(
                tenant_id, user_id, start_time, end_time, token
            )
        
        if self._should_include_tool('onnx', request.toolList):
            onnx_metrics = await self.get_onnx_metrics(
                tenant_id, user_id, start_time, end_time, token
            )
        
        return MetricsResponse(
            tenantId=tenant_id,
            userId=user_id,
            timeRange={
                "start": start_time,
                "end": end_time
            },
            ollama=ollama_metrics,
            lorax=lorax_metrics,
            vllm=vllm_metrics,
            onnx=onnx_metrics
        )