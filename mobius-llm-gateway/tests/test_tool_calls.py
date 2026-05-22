#!/usr/bin/env python3
"""
Test script to demonstrate tool call extraction and formatting
"""

from tool_calls import parse_and_format_tool_calls, extract_tool_calls_from_response, format_tool_calls_for_openai
import json

# Example response from the notebook
example_response = """<think>
Okay, the user is asking about the weather in Toronto. I need to use the get_current_weather function. Let me check the rules again. The function requires the city parameter, which is provided here as Toronto. So I should generate the correct JSON call. Make sure the city name is spelled correctly. Then, once I get the data from the tool, I can relay the information back to the user. But for now, just the function call is needed.
</think>

```json
{
    "function": "get_current_weather",
    "parameters": {
        "city": "Toronto"
    }
}
```"""

def test_tool_call_extraction():
    """Test the tool call extraction functionality"""
    print("=== Testing Tool Call Extraction ===")
    print(f"Original response:\n{example_response}\n")
    
    # Test extraction
    has_tool_calls, extracted_calls, cleaned_content = extract_tool_calls_from_response(example_response)
    
    print(f"Has tool calls: {has_tool_calls}")
    print(f"Extracted calls: {json.dumps(extracted_calls, indent=2)}")
    print(f"Cleaned content: '{cleaned_content}'\n")
    
    # Test formatting for OpenAI
    if has_tool_calls:
        formatted_calls = format_tool_calls_for_openai(extracted_calls)
        print("Formatted for OpenAI:")
        for call in formatted_calls:
            print(f"  ID: {call.id}")
            print(f"  Type: {call.type}")
            print(f"  Function: {json.dumps(call.function, indent=4)}")
            print()

def test_complete_pipeline():
    """Test the complete pipeline"""
    print("=== Testing Complete Pipeline ===")
    
    has_tool_calls, formatted_calls, cleaned_content = parse_and_format_tool_calls(example_response)
    
    print(f"Has tool calls: {has_tool_calls}")
    print(f"Number of formatted calls: {len(formatted_calls)}")
    print(f"Cleaned content: '{cleaned_content}'")
    
    if formatted_calls:
        print("\nFormatted tool calls:")
        for i, call in enumerate(formatted_calls):
            print(f"  Call {i+1}:")
            print(f"    ID: {call.id}")
            print(f"    Type: {call.type}")
            print(f"    Function Name: {call.function['name']}")
            print(f"    Arguments: {call.function['arguments']}")
            print()

def test_edge_cases():
    """Test edge cases"""
    print("=== Testing Edge Cases ===")
    
    # Test with no tool calls
    no_tool_response = "This is just a regular response with no tool calls."
    has_calls, calls, content = parse_and_format_tool_calls(no_tool_response)
    print(f"No tool calls - Has calls: {has_calls}, Content: '{content}'")
    
    # Test with malformed JSON
    malformed_response = """```json
    {
        "function": "get_weather"
        "parameters": {
            "city": "Toronto"
        }
    }
    ```"""
    has_calls, calls, content = parse_and_format_tool_calls(malformed_response)
    print(f"Malformed JSON - Has calls: {has_calls}, Content: '{content}'")
    
    # Test with multiple tool calls
    multiple_calls_response = """```json
    {
        "function": "get_weather",
        "parameters": {
            "city": "Toronto"
        }
    }
    ```

    I need to also check the weather in Vancouver.

    ```json
    {
        "function": "get_weather",
        "parameters": {
            "city": "Vancouver"
        }
    }
    ```"""
    has_calls, calls, content = parse_and_format_tool_calls(multiple_calls_response)
    print(f"Multiple calls - Has calls: {has_calls}, Number of calls: {len(calls)}, Content: '{content}'")

if __name__ == "__main__":
    test_tool_call_extraction()
    test_complete_pipeline()
    test_edge_cases()
