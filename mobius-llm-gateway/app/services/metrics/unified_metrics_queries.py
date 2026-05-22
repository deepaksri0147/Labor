from typing import Optional
from datetime import datetime


class UnifiedMetricsQueries:
    """SQL query templates for unified metrics"""

    def __init__(self, schema_id: str):
        self.table_name = f"t_{schema_id}_t"

    # =====================================================================
    # HELPERS
    # =====================================================================

    def build_time_filter(self, start_time: Optional[str], end_time: Optional[str]) -> str:
        """Build time range filter from ISO 8601 datetime strings (TiDB/MySQL safe)"""
        filters = []

        if start_time:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            start_sql = start_dt.strftime('%Y-%m-%d %H:%M:%S')
            filters.append(f"`timestamp` >= '{start_sql}'")

        if end_time:
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            end_sql = end_dt.strftime('%Y-%m-%d %H:%M:%S')
            filters.append(f"`timestamp` < '{end_sql}'")  # half-open interval

        if filters:
            return " AND " + " AND ".join(filters)

        return ""

    # =====================================================================
    # OLLAMA QUERIES
    # =====================================================================

    def get_ollama_summary_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get Ollama tool-level summary"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            COUNT(*) AS total_hits,
            SUM(tokens) AS total_tokens,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
            AVG(duration_ms) AS avg_latency_ms,
            SUM(tokens) / NULLIF(SUM(duration_ms) / 1000.0, 0) AS throughput_tokens_per_sec
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'ollama'
          {time_filter}
        """

    def get_ollama_models_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get Ollama model-level breakdown"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            model,
            COUNT(*) AS total_hits,
            SUM(tokens) AS total_tokens,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
            AVG(duration_ms) AS avg_latency_ms,
            MIN(duration_ms) AS min_latency_ms,
            MAX(duration_ms) AS max_latency_ms,
            SUM(tokens) / NULLIF(SUM(duration_ms) / 1000.0, 0) AS throughput_tokens_per_sec
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'ollama'
          {time_filter}
        GROUP BY model
        ORDER BY total_hits DESC
        """

    # =====================================================================
    # LORAX QUERIES
    # =====================================================================

    def get_lorax_summary_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get LoRAX tool-level summary"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            COUNT(*) AS total_hits,
            SUM(tokens) AS total_tokens,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
            AVG(duration_ms) AS avg_latency_ms
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'lorax'
          {time_filter}
        """

    def get_lorax_service_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get LoRAX adapter performance breakdown"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            service_name,
            COUNT(*) AS total_hits,
            COALESCE(SUM(tokens), 0) AS total_tokens,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
            AVG(duration_ms) AS avg_latency_ms,
            MIN(duration_ms) AS min_latency_ms,
            MAX(duration_ms) AS max_latency_ms
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'lorax'
          {time_filter}
        GROUP BY service_name
        ORDER BY total_hits DESC
        """

    # =====================================================================
    # VLLM QUERIES
    # =====================================================================

    def get_vllm_summary_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get vLLM tool-level summary"""
        print("Building vLLM summary query...", tenant_id, user_id, start_time, end_time)
        time_filter = self.build_time_filter(start_time, end_time)
        print("Time filter:", time_filter)

        return f"""
        SELECT
            COUNT(*) AS total_hits,
            SUM(tokens) AS total_tokens,
            AVG(duration_ms) AS avg_latency_ms,
            SUM(tokens) / NULLIF(SUM(duration_ms) / 1000.0, 0) AS throughput_tokens_per_sec
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'vllm'
          {time_filter}
        """

    def get_vllm_services_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get vLLM service_name-level breakdown"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            service_name,
            COUNT(*) AS total_hits,
            SUM(tokens) AS total_tokens,
            AVG(duration_ms) AS avg_latency_ms,
            SUM(tokens) / NULLIF(SUM(duration_ms) / 1000.0, 0) AS throughput_tokens_per_sec
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'vllm'
          {time_filter}
        GROUP BY service_name
        ORDER BY total_hits DESC
        """

    # =====================================================================
    # ONNX QUERIES
    # =====================================================================

    def get_onnx_summary_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get ONNX tool-level summary"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            COUNT(*) AS total_hits,
            AVG(duration_ms) AS avg_latency_ms
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'onnx'
          {time_filter}
        """

    def get_onnx_models_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get ONNX model-level breakdown"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            model,
            COUNT(*) AS total_hits,
            AVG(duration_ms) AS avg_latency_ms,
            MIN(duration_ms) AS min_latency_ms,
            MAX(duration_ms) AS max_latency_ms
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          AND tool = 'onnx'
          {time_filter}
        GROUP BY model
        ORDER BY total_hits DESC
        """

    # =====================================================================
    # OVERALL SUMMARY (if needed in future)
    # =====================================================================

    def get_overall_summary_query(
        self,
        tenant_id: str,
        user_id: str,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> str:
        """Get overall summary across all tools"""
        time_filter = self.build_time_filter(start_time, end_time)

        return f"""
        SELECT
            COUNT(*) AS total_hits,
            SUM(tokens) AS total_tokens,
            AVG(duration_ms) AS avg_latency_ms,
            COUNT(DISTINCT tool) AS tools_used,
            COUNT(DISTINCT model) AS models_used
        FROM {self.table_name}
        WHERE tenantid = '{tenant_id}'
          AND userid = '{user_id}'
          {time_filter}
        """