"""
Ollama API client module for interfacing with Ollama services
"""


import requests
import json
from typing import Dict, Any, Optional
from app.core.config import Config


class OllamaClient:
    """Client for interacting with Ollama API"""
    
    def __init__(self, base_url: str = None, timeout: int = None):
        self.base_url = base_url or Config.OLLAMA_BASE_URL
        self.timeout = timeout or Config.REQUEST_TIMEOUT
        self.generate_url = f"{self.base_url}/api/generate"
    
    def generate(self, model: str, prompt: str, stream: bool = False, tools = None, tool_choice = None, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Generate text using Ollama API
        
        Args:
            model: The model to use for generation
            prompt: The prompt to send to the model
            stream: Whether to stream the response
            options: Additional options for the model (temperature, top_p, etc.)
        
        Returns:
            Dict containing the model response
            
        Raises:
            Exception: If the API request fails
        """
        payload_data = {
            "model": model,
            "prompt": prompt,
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": stream
        }
        
        # Add options if provided
        if options:
            payload_data["options"] = options

        headers = {
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(
                self.generate_url,
                json=payload_data,
                headers=headers,
                timeout=self.timeout
            )
            
            # Check if request was successful
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Ollama API request failed: {str(e)}")
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse Ollama API response: {str(e)}")


# Create a default client instance
ollama_client = OllamaClient()

# Legacy function for backward compatibility
def ollama_generate(model: str, prompt: str, stream: bool = False, tools = None, tool_choice = None, options = None) -> Dict[str, Any]:
    """Legacy function wrapper for backward compatibility"""
    return ollama_client.generate(model, prompt, stream, tools, tool_choice, options)


