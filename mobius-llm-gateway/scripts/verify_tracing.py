import os
import sys
import logging

# Add current directory to sys.path to allow imports
sys.path.append(os.getcwd())

from app.utils.logging_config import setup_logging, setup_tracing
from app.core.config import Config

def test_tracing_init():
    print("\n--- Testing Tracing Initialization ---")
    setup_logging()
    
    # Mocking Jaeger settings for local test if needed
    Config.JAEGER_URL = "localhost"
    Config.JAEGER_PORT = 14268
    
    try:
        setup_tracing()
        print("✓ Tracing initialization completed (check logs above for confirmation)")
    except Exception as e:
        print(f"✗ Tracing initialization failed: {e}")

if __name__ == "__main__":
    test_tracing_init()
