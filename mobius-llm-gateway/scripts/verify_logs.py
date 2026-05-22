import os
import logging
from app.utils.logging_config import setup_logging
from app.core.config import Config

def test_logging(level: str):
    print(f"\n--- Testing with LOG_LEVEL={level} ---")
    Config.LOG_LEVEL = level
    setup_logging()
    
    logger = logging.getLogger("test_logger")
    logger.info("ACTION: This is an action log (should see in INFO and DEBUG)")
    logger.debug("DEBUG: This is a debug log (should only see in DEBUG)")
    logger.error("ERROR: This is an error log (should see in both)")

if __name__ == "__main__":
    # Test INFO level
    test_logging("INFO")
    
    # Test DEBUG level
    test_logging("DEBUG")
