from typing import Optional, List, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class OllamaUsageQueries:
    """SQL query builder for Ollama usage aggregation (TiDB compatible)"""

    def __init__(self, schema_id: str):
        self.table_name = f"t_{schema_id}_t"

    def _parse_datetime(self, dt_string: str) -> datetime:
        """Parse datetime string with multiple format support"""
        # Try different formats
        formats = [
            "%Y-%m-%dT%H:%M:%SZ",           # 2026-01-01T00:00:00Z
            "%Y-%m-%dT%H:%M:%S",            # 2026-01-01T00:00:00
            "%Y-%m-%d %H:%M:%S",            # 2026-01-01 00:00:00
            "%Y-%m-%dT%H:%M:%S.%fZ",        # 2026-01-01T00:00:00.000Z
            "%Y-%m-%dT%H:%M:%S.%f",         # 2026-01-01T00:00:00.000
        ]
        
        # First try ISO format with timezone
        try:
            return datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
        
        # Try other formats
        for fmt in formats:
            try:
                return datetime.strptime(dt_string, fmt)
            except ValueError:
                continue
        
        # If all fail, raise error
        raise ValueError(f"Unable to parse datetime: {dt_string}")

    def _build_select_clause(self, metrics: List[str], group_by: List[str]) -> str:
        """Build SELECT clause with requested metrics and grouping dimensions"""
        select_parts = []
        
        # Add grouping dimensions to SELECT
        for dimension in group_by:
            if dimension == 'day':
                select_parts.append("DATE(timestamp) AS day")
            elif dimension == 'month':
                select_parts.append("DATE_FORMAT(timestamp, '%Y-%m') AS month")
            else:
                select_parts.append(dimension)
        
        # Add requested metrics
        metric_mapping = {
            'input_tokens': 'COALESCE(SUM(input_tokens), 0) AS input_tokens',
            'output_tokens': 'COALESCE(SUM(output_tokens), 0) AS output_tokens',
            'total_tokens': 'COALESCE(SUM(tokens), 0) AS total_tokens',
            'request_count': 'COUNT(*) AS request_count'
        }
        
        for metric in metrics:
            if metric in metric_mapping:
                select_parts.append(metric_mapping[metric])
        
        return ",\n            ".join(select_parts)

    def _build_where_clause(
        self,
        tenant_id: str,
        filters: Optional[Dict],
        time_range: Optional[Dict]
    ) -> str:
        """Build WHERE clause with tenant, filters, and time range"""
        conditions = [
            f"tenantid = '{tenant_id}'",
            "tool = 'ollama'"
        ]
        
        # Add optional filters
        if filters:
            if filters.get('user_id'):
                conditions.append(f"userid = '{filters['user_id']}'")
            if filters.get('model'):
                conditions.append(f"model = '{filters['model']}'")
            if filters.get('session_id'):
                conditions.append(f"session_id = '{filters['session_id']}'")
        
        # Add time range filters
        if time_range:
            if time_range.get('from_time'):
                try:
                    from_dt = self._parse_datetime(time_range['from_time'])
                    from_str = from_dt.strftime('%Y-%m-%d %H:%M:%S')
                    conditions.append(f"timestamp >= '{from_str}'")
                    logger.info(f"Parsed from_time: {from_str}")
                except ValueError as e:
                    logger.error(f"Error parsing from_time: {e}")
            
            if time_range.get('to_time'):
                try:
                    to_dt = self._parse_datetime(time_range['to_time'])
                    to_str = to_dt.strftime('%Y-%m-%d %H:%M:%S')
                    conditions.append(f"timestamp <= '{to_str}'")
                    logger.info(f"Parsed to_time: {to_str}")
                except ValueError as e:
                    logger.error(f"Error parsing to_time: {e}")
        
        return "\n          AND ".join(conditions)

    def _build_group_by_clause(self, group_by: List[str]) -> str:
        """Build GROUP BY clause (TiDB compatible)"""
        if not group_by:
            return ""
        
        group_parts = []
        for dimension in group_by:
            if dimension == 'day':
                group_parts.append("DATE(timestamp)")
            elif dimension == 'month':
                group_parts.append("DATE_FORMAT(timestamp, '%Y-%m')")
            else:
                group_parts.append(dimension)
        
        return "GROUP BY " + ", ".join(group_parts)

    def _build_order_by_clause(self, group_by: List[str]) -> str:
        """Build ORDER BY clause based on grouping"""
        if not group_by:
            return ""
        
        order_parts = []
        
        if 'month' in group_by:
            order_parts.append("month DESC")
        if 'day' in group_by:
            order_parts.append("day DESC")
        
        # Add other dimensions
        for dim in group_by:
            if dim not in ['day', 'month']:
                order_parts.append(f"{dim}")
        
        if order_parts:
            return "ORDER BY " + ", ".join(order_parts)
        
        return ""

    def build_aggregate_query(
        self,
        tenant_id: str,
        metrics: List[str],
        filters: Optional[Dict] = None,
        time_range: Optional[Dict] = None,
        group_by: Optional[List[str]] = None
    ) -> str:
        """Build complete aggregation query"""
        group_by = group_by or []
        
        select_clause = self._build_select_clause(metrics, group_by)
        where_clause = self._build_where_clause(tenant_id, filters, time_range)
        group_by_clause = self._build_group_by_clause(group_by)
        order_by_clause = self._build_order_by_clause(group_by)
        
        query_parts = [
            f"SELECT",
            f"            {select_clause}",
            f"        FROM {self.table_name}",
            f"        WHERE {where_clause}"
        ]
        
        if group_by_clause:
            query_parts.append(f"        {group_by_clause}")
        
        if order_by_clause:
            query_parts.append(f"        {order_by_clause}")
        
        return "\n".join(query_parts)