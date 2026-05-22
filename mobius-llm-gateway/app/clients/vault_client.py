import os
import sys
import base64
import logging
import tempfile
import yaml
import subprocess
import stat
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any, List
import hvac
from kubernetes import client, config
from kubernetes.config import ConfigException
from dotenv import load_dotenv
from app.core.settings import settings

load_dotenv()

logger = logging.getLogger(__name__)

# ============ CONFIGURATION ============
VAULT_ADDRESS = settings.VAULT_ADDR
VAULT_TOKEN = settings.VAULT_TOKEN
VAULT_K8S_SECRET_PATH = settings.VAULT_K8S_SECRET_BASE_PATH
VAULT_BASE_PATH = VAULT_K8S_SECRET_PATH
DISABLE_VAULT = settings.DISABLE_VAULT

# SSL Verification for Vault
# Default to True for production, but allow disabling via settings
VAULT_VERIFY = getattr(settings, "VAULT_VERIFY", True)

# Track temporary files for cleanup
_CREATED_TEMP_FILES: List[str] = []

# ============ EXCEPTIONS ============
class VaultError(Exception):
    """Base exception for Vault related errors."""
    pass

class KubeConfigError(VaultError):
    """Exception raised when Kubernetes configuration fails."""
    pass

# ============ VAULT CLIENT ============
@lru_cache(maxsize=1)
def get_vault_client() -> Optional[hvac.Client]:
    """
    Create and authenticate hvac client.
    """
    if DISABLE_VAULT:
        return None

    try:
        # Suppress InsecureRequestWarning if verify is False
        if not VAULT_VERIFY:
            from urllib3.exceptions import InsecureRequestWarning
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)

        client_obj = hvac.Client(
            url=VAULT_ADDRESS,
            token=VAULT_TOKEN,
            verify=VAULT_VERIFY
        )
        
        if not client_obj.is_authenticated():
            logger.error("Vault authentication failed: Token is invalid or expired")
            return None
            
        logger.info(f"Vault client authenticated successfully to {VAULT_ADDRESS}")
        return client_obj
    except Exception as e:
        logger.error(f"Failed to create Vault client: {e}")
        return None

def get_vault_secret(path: str, key: str) -> str:
    """
    Fetch a single key from a Vault KV v1 secret.
    """
    client_obj = get_vault_client()
    if not client_obj:
        raise VaultError("Vault client not initialized or disabled")
    
    try:
        secret = client_obj.secrets.kv.v1.read_secret(path=path)
        if secret and "data" in secret and key in secret["data"]:
            return secret["data"][key]
        raise VaultError(f"Secret key '{key}' not found at path '{path}'")
    except Exception as e:
        logger.error(f"Error reading secret '{key}' from Vault path '{path}': {e}")
        raise VaultError(f"Error reading secret from Vault: {str(e)}")

def get_llm_api_key(provider: str) -> str:
    """
    Fetch a cloud LLM provider API key from Vault.
    Provider must be one of: anthropic, gemini, openai.
    Keys are stored at the same path as other secrets: secret/k8sconfig/ori-testing
    """
    _vault_key_map = {
        "anthropic": "anthropic_api_key",
        "openai":    "openai_api_key",
        "gemini":    "gemini_api_key",
    }
    vault_key = _vault_key_map.get(provider, provider)
    return get_vault_secret(VAULT_BASE_PATH, vault_key)

def get_k8s_secret_data() -> Optional[Dict[str, Any]]:
    """
    Fetch the Kubernetes configuration secret data from Vault.
    Returns the 'data' dictionary from the secret.
    """
    client_obj = get_vault_client()
    if not client_obj:
        return None


    try:
        logger.info(f"Reading secret from path: {VAULT_K8S_SECRET_PATH}")
        secret = client_obj.secrets.kv.v1.read_secret(path=VAULT_K8S_SECRET_PATH)
        
        if secret and "data" in secret:
            return secret["data"]
        else:
            logger.warning(f"No data found at path: {VAULT_K8S_SECRET_PATH}")
            return None
            
    except hvac.exceptions.InvalidPath:
        logger.warning(f"Secret path not found: {VAULT_K8S_SECRET_PATH}")
        return None
    except Exception as e:
        logger.error(f"Error reading secret from Vault: {e}")
        return None

# ============ VAULT CONNECTIVITY CHECK ============
def check_vault_connectivity() -> bool:
    """Check if Vault is reachable and authenticated"""
    if DISABLE_VAULT:
        logger.info("Vault is disabled via DISABLE_VAULT flag")
        return False
    
    client_obj = get_vault_client()
    return client_obj is not None

# ============ VAULT HEALTH CHECK ============
def vault_health_check() -> dict:
    """Check Vault health status and available configurations"""
    health_data = {
        "vault_enabled": not DISABLE_VAULT,
        "vault_connected": False,
        "vault_address": VAULT_ADDRESS,
        "k8s_secret_path": VAULT_K8S_SECRET_PATH,
        "kubeconfig_available": False,
        "hf_token_available": False,
    }
    
    if DISABLE_VAULT:
        health_data["message"] = "Vault is disabled via DISABLE_VAULT flag"
        return health_data
    
    # Check connectivity
    client_obj = get_vault_client()
    health_data["vault_connected"] = client_obj is not None
    
    if not health_data["vault_connected"]:
        health_data["message"] = "Vault is not reachable"
        return health_data
    
    # Check if secret data is available
    data = get_k8s_secret_data()
    if data:
        health_data["kubeconfig_available"] = all(
            key in data for key in ["host", "cluster_ca_certificate", "client_certificate", "client_key"]
        )
        health_data["hf_token_available"] = any(
            key in data for key in ["hf-token", "hf_token", "HF_TOKEN"]
        )
    
    return health_data

# ============ CREATE KUBECONFIG FROM VAULT ============
def create_kubeconfig_from_vault() -> Dict[str, str]:
    """
    Fetch Vault KV v1 leaf secrets and normalize them to PEM.
    Writes PEMs to ~/.kube and creates a fully inlined ~/.kube/config.
    """
    if DISABLE_VAULT:
        logger.info("Vault is disabled, skipping kubeconfig creation.")
        return {}

    try:
        logger.info("🔧 Creating kubeconfig from Vault secrets...")
        
        # Fetch raw strings from Vault (may be PEM or base64)
        ca_raw = get_vault_secret(VAULT_BASE_PATH, "cluster_ca_certificate")
        crt_raw = get_vault_secret(VAULT_BASE_PATH, "client_certificate")
        key_raw = get_vault_secret(VAULT_BASE_PATH, "client_key")
        host = get_vault_secret(VAULT_BASE_PATH, "host")

        def is_pem(s: str) -> bool:
            return isinstance(s, str) and "-----BEGIN" in s and "-----END" in s

        def b64_to_str(s: str) -> Optional[str]:
            try:
                # Strip whitespace and decode
                dec = base64.b64decode(s.encode("utf-8"), validate=True)
                return dec.decode("utf-8", errors="strict")
            except Exception:
                return None

        def to_pem(label: str, raw: str, expected_header: str) -> str:
            if is_pem(raw):
                return raw
            decoded = b64_to_str(raw)
            if decoded and expected_header in decoded:
                return decoded
            raise KubeConfigError(
                f"{label} is neither PEM nor valid base64 of PEM; cannot configure kubeconfig"
            )

        # Normalize all three to PEM text
        ca_pem = to_pem("cluster_ca_certificate", ca_raw, "-----BEGIN CERTIFICATE-----")
        crt_pem = to_pem("client_certificate", crt_raw, "-----BEGIN CERTIFICATE-----")
        key_pem = to_pem("client_key", key_raw, "-----BEGIN")

        # Create base64 for ...-data (single encode of the PEM text)
        def to_b64(pem_text: str) -> str:
            return base64.b64encode(pem_text.encode("utf-8")).decode("utf-8")

        ca_b64 = to_b64(ca_pem)
        crt_b64 = to_b64(crt_pem)
        key_b64 = to_b64(key_pem)

        # Write PEMs to ~/.kube
        home = os.path.expanduser("~")
        kube_dir = os.path.join(home, ".kube")
        os.makedirs(kube_dir, exist_ok=True)
        ca_path = os.path.join(kube_dir, "vault-ca.crt")
        crt_path = os.path.join(kube_dir, "vault-client.crt")
        key_path = os.path.join(kube_dir, "vault-client.key")
        home_cfg_path = os.path.join(kube_dir, "config")

        for path, content in [(ca_path, ca_pem), (crt_path, crt_pem), (key_path, key_pem)]:
            with open(path, "w") as f:
                f.write(content)
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass

        # Fully inlined kubeconfig
        home_cfg_inlined = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": "vault-cluster",
                    "cluster": {
                        "server": host,
                        "certificate-authority-data": ca_b64,
                    },
                }
            ],
            "users": [
                {
                    "name": "vault-user",
                    "user": {
                        "client-certificate-data": crt_b64,
                        "client-key-data": key_b64,
                    },
                }
            ],
            "contexts": [
                {
                    "name": "vault-context",
                    "context": {"cluster": "vault-cluster", "user": "vault-user"},
                }
            ],
            "current-context": "vault-context",
        }

        # Write ~/.kube/config
        with open(home_cfg_path, "w") as f:
            yaml.safe_dump(home_cfg_inlined, f, default_flow_style=False)
        try:
            os.chmod(home_cfg_path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass

        logger.info(f"Wrote self-contained kubeconfig to {home_cfg_path}")

        # Configure Python Kubernetes client
        configuration = client.Configuration()
        configuration.host = host
        configuration.ssl_ca_cert = ca_path
        configuration.cert_file = crt_path
        configuration.key_file = key_path
        configuration.verify_ssl = True
        client.Configuration.set_default(configuration)

        # App-scoped kubeconfig (temp)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as kubeconfig_file:
            yaml.safe_dump(home_cfg_inlined, kubeconfig_file, default_flow_style=False)
            kubeconfig_path = kubeconfig_file.name
        
        os.environ["KUBECONFIG"] = kubeconfig_path
        _CREATED_TEMP_FILES.append(kubeconfig_path)

        logger.info(f"✅ Kubeconfig created successfully at: {kubeconfig_path}")
        return {
            "client_cert_path": crt_path,
            "client_key_path": key_path,
            "ca_cert_path": ca_path,
            "kubeconfig_path": kubeconfig_path,
            "home_kubeconfig_path": home_cfg_path,
        }

    except Exception as e:
        logger.error(f"Failed to configure Kubernetes client with Vault secrets: {str(e)}")
        raise KubeConfigError(f"Failed to configure Kubernetes client with Vault secrets: {str(e)}")

# ============ GET HF TOKEN ============
def get_hf_token_b64() -> str:
    """
    Fetch HuggingFace token.
    """
    # Try environment variable first
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        return base64.b64encode(hf_token.encode()).decode()
    
    # Try Vault
    if not DISABLE_VAULT:
        data = get_k8s_secret_data()
        if data:
            for key in ["hf-token", "hf_token", "HF_TOKEN"]:
                if key in data:
                    token = data[key]
                    logger.info("✓ HF token retrieved from Vault")
                    try:
                        base64.b64decode(token, validate=True)
                        return token
                    except:
                        return base64.b64encode(token.encode("utf-8")).decode()
    
    logger.warning("Using dummy HF token")
    return base64.b64encode("dummy-token".encode()).decode()

# ============ INITIALIZATION ============
def initialize_vault_kubernetes():
    """
    Initialize Kubernetes configuration.
    First tries in-cluster config, then falls back to Vault KV v2.
    """
    try:
        # config.load_incluster_config()
        try:
            config.load_incluster_config()
            logger.info("Loaded IN-CLUSTER Kubernetes config")
        except ConfigException:
            logger.info("In-cluster config not found, attempting local kubeconfig...")
            config.load_kube_config()
            logger.info("Loaded local kubeconfig")


        # cfg = client.Configuration.get_default_copy() # Not strictly needed if just initializing
        logger.info("Loaded IN-CLUSTER Kubernetes config")
    except ConfigException:
        logger.info("In-cluster config not found, attempting to load from Vault...")
        if DISABLE_VAULT:
            logger.error("Vault is disabled and in-cluster config failed. Cannot initialize Kubernetes.")
            sys.exit(1)

        try:
            vault_client = get_vault_client()
            if not vault_client:
                raise VaultError("Failed to initialize Vault client")

            # Fetching kubeconfig from Vault KV store (using path 'kubernetes' and key 'config')
            # Note: The snippet assumes KV v2
            try:
                secret_response = vault_client.secrets.kv.v2.read_secret_version(path="kubernetes")
                kubeconfig_yaml = secret_response["data"]["data"]["config"]
            except Exception as e:
                logger.error(f"Failed to read secret from Vault KV v2: {e}")
                # Fallback to KV v1 if needed or just fail
                raise

            config.load_kube_config_from_dict(yaml.safe_load(kubeconfig_yaml))
            logger.info("Loaded Kubernetes config from Vault KV v2")

            # Set kubectl context (optional, don't crash if kubectl is missing)
            # We still want to write the file for subprocess calls to kubectl if they are used elsewhere
            try:
                home = os.path.expanduser("~")
                kube_dir = os.path.join(home, ".kube")
                os.makedirs(kube_dir, exist_ok=True)
                home_cfg_path = os.path.join(kube_dir, "config")
                
                with open(home_cfg_path, "w") as f:
                    f.write(kubeconfig_yaml)
                os.chmod(home_cfg_path, stat.S_IRUSR | stat.S_IWUSR)
                
                env = os.environ.copy()
                # The context name might depend on the kubeconfig content, 
                # but we'll try to use what's there or skip context switching if not needed.
                # For now, just ensuring the file exists is often enough for kubectl.
            except Exception as e:
                logger.warning(f"⚠️ Could not write ~/.kube/config for kubectl: {e}")

        except Exception as e:
            logger.error(f"❌ Failed to initialize kubeconfig from Vault: {e}")
            logger.error("💀 Application startup aborted - kubeconfig initialization failed")
            sys.exit(1)

def cleanup_temp_files():
    """Clean up any temporary files created during initialization."""
    for path in _CREATED_TEMP_FILES:
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.debug(f"Cleaned up temp file: {path}")
        except Exception as e:
            logger.warning(f"Failed to clean up temp file {path}: {e}")
    _CREATED_TEMP_FILES.clear()