from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class ActionType(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    READ = "READ"
    EXECUTE = "EXECUTE"

class ActionSource(str, Enum):
    HUMAN_API = "HUMAN_API"
    SYSTEM = "SYSTEM"

class ActionLogRequestStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"

class NodeType(str, Enum):
    INFERENCE = "INFERENCE"
    DEPLOYMENT = "DEPLOYMENT"
    METRICS = "METRICS"
    ADAPTER = "ADAPTER"
    SYSTEM = "SYSTEM"
