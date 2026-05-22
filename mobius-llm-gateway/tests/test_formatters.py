#!/usr/bin/env python3
"""
Test script to demonstrate the updated input formatter with tools
"""

from formatters import InputFormatter
from models import Message, Tool, Function, ChatCompletionRequest
import json

def test_input_formatter_with_tools():
    """Test the input formatter with tools"""
    print("=== Testing Input Formatter with Tools ===")
    
    # Create sample messages
    messages = [
        Message(role="system", content="You are a helpful AI assistant that can check weather information. You have access to a weather tool."),
        Message(role="user", content="What is the weather in Toronto?")
    ]
    
    # Create sample tools (matching the notebook format)
    tools = [
        {
            'type': 'function',
            'function': {
                'name': 'get_current_weather',
                'description': 'Get the current weather for a city',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'city': {
                            'type': 'string',
                            'description': 'The name of the city',
                        },
                    },
                    'required': ['city'],
                },
            },
        }
    ]
    
    # Test formatting with tools
    formatted_prompt = InputFormatter.format_messages_to_prompt(messages, tools)
    
    print("Formatted prompt with tools:")
    print("=" * 50)
    print(formatted_prompt)
    print("=" * 50)

def test_input_formatter_without_tools():
    """Test the input formatter without tools"""
    print("\n=== Testing Input Formatter without Tools ===")
    
    # Create sample messages
    messages = [
        Message(role="system", content="You are a helpful AI assistant."),
        Message(role="user", content="Hello, how are you?")
    ]
    
    # Test formatting without tools
    formatted_prompt = InputFormatter.format_messages_to_prompt(messages)
    
    print("Formatted prompt without tools:")
    print("=" * 50)
    print(formatted_prompt)
    print("=" * 50)

def test_format_for_ollama():
    """Test the complete format_for_ollama method"""
    print("\n=== Testing format_for_ollama Method ===")
    
    # Create a ChatCompletionRequest with tools
    function = Function(
        name="get_current_weather",
        description="Get the current weather for a city",
        parameters={
            'type': 'object',
            'properties': {
                'city': {
                    'type': 'string',
                    'description': 'The name of the city',
                },
            },
            'required': ['city'],
        }
    )
    
    tool = Tool(type="function", function=function)
    
    request = ChatCompletionRequest(
        model="qwen3:14b",
        messages=[
            Message(role="system", content="You are a helpful AI assistant that can check weather information."),
            Message(role="user", content="What is the weather in Toronto?")
        ],
        tools=[tool],
        temperature=0.7,
        max_tokens=1024
    )
    
    # Format for Ollama
    ollama_request = InputFormatter.format_for_ollama(request)
    
    print("Ollama formatted request:")
    print("=" * 50)
    print(f"Model: {ollama_request['model']}")
    print(f"Temperature: {ollama_request['options']['temperature']}")
    print(f"Max tokens: {ollama_request['options']['num_predict']}")
    print("\nPrompt:")
    print(ollama_request['prompt'])
    print("=" * 50)

if __name__ == "__main__":
    test_input_formatter_with_tools()
    test_input_formatter_without_tools()
    test_format_for_ollama()
