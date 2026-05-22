import logging
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))

from app.clients.vault_client import get_vault_client
from app.core.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diagnose_vault")

def diagnose():
    client = get_vault_client()
    if not client:
        logger.error("Failed to initialize Vault client. Check VAULT_ADDR and VAULT_TOKEN.")
        return

    logger.info("Listing secret engine mounts:")
    try:
        mounts = client.sys.list_mounted_secrets_engines()
        for path, config in mounts.get('data', mounts).items():
            logger.info(f" - {path} (type: {config.get('type')})")
    except Exception as e:
        logger.error(f"Failed to list mounts: {e}")

    try:
        res = client.secrets.kv.v2.list_secrets(path="k8sconfig/ori-testing", mount_point="secret")
        logger.info(f"Children of secret/k8sconfig/ori-testing: {res['data']['keys']}")
    except Exception as e:
        logger.info(f"Could not list children of secret/k8sconfig/ori-testing: {e}")

    mounts = ["kv", "secret"]
    for mount in mounts:
        logger.info(f"\n>>>> Exhaustive list for mount: {mount}/ <<<<")
        try:
            res = client.secrets.kv.v2.list_secrets(path="", mount_point=mount)
            keys = res.get('data', {}).get('keys', [])
            logger.info(f"Keys at {mount}/: {keys}")
            for key in keys:
                if key.endswith('/'):
                    try:
                        sub = client.secrets.kv.v2.list_secrets(path=key.rstrip('/'), mount_point=mount)
                        logger.info(f" - {mount}/{key}: {sub.get('data', {}).get('keys', [])}")
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Failed v2 list for {mount}: {e}")

if __name__ == "__main__":
    diagnose()
