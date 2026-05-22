"""
Tool call extraction and formatting utilities
"""

import json
import uuid
import re
from typing import Dict, Any, List, Optional, Tuple
from app.schemas.models import ToolCall
import logging

logger = logging.getLogger(__name__)


def extract_tool_calls_from_response(response_text: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Extract tool calls from AI response text.
    
    Args:
        response_text (str): The AI response text that may contain tool calls
        
    Returns:
        tuple: (has_tool_calls: bool, tool_calls: List[Dict], cleaned_content: str)
    """
    tool_calls = []
    
    logger.info(f"the response text is {response_text}")
    pattern = r'<think>\s*\n*\[?\s*(\{.*?\})\s*\]?'
    # response_text = re.sub(pattern, '', response_text, flags=re.DOTALL)
    

    try:
        matches = re.findall(pattern, response_text, re.DOTALL)

        if matches:
            tools_json = response_text.replace("<think>", "")
            logger.info(f"the response text is {response_text}")
            parsed_json = json.loads(tools_json)
            logger.info(f"the parsed json is {parsed_json}")
            if isinstance(parsed_json, dict):
                parsed_json = [parsed_json]
            if isinstance(parsed_json, list):
                for item in parsed_json:
                    if isinstance(item, dict) and ("function" in item or "name" in item):
                        function_name = item.get("function") or item.get("name")
                        parameters = item.get("parameters") or item.get("arguments")
                        
                        tool_call = {
                            "function": function_name,
                            "parameters": parameters
                        }
                        tool_calls.append(tool_call)
            
            logger.info(f"the tool calls are {tool_calls}")
            response_text = response_text.replace("<think>", "")
            response_text = response_text.replace(tools_json, "")
                # print(response_text)
            return True, tool_calls, response_text
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
    except Exception as e:
        logger.error(f"Error processing think block: {e}")
        
    try:
        # Look for JSON code blocks that contain function calls
        json_pattern = r'```json\s*(\{.*?\})\s*```'
        matches = re.findall(json_pattern, response_text, re.DOTALL)
        
        for match in matches:
            try:
                parsed_json = json.loads(match)
                # parsed_json = ast.literal_eval(match)
                # Check if this is a function call
                if isinstance(parsed_json, dict) and "function" in parsed_json:
                    function_name = parsed_json.get("function")
                    parameters = parsed_json.get("parameters", {})
                    
                    tool_call = {
                        "function": function_name,
                        "parameters": parameters
                    }
                    tool_calls.append(tool_call)
                    
                    # Remove the JSON block from content
                    json_block = f"```json\n{match}\n```"
                    cleaned_content = cleaned_content.replace(json_block, "").strip()
                    
            except json.JSONDecodeError:
                # Skip invalid JSON blocks
                continue
        
        # Also look for direct JSON objects (without code blocks)
        if not tool_calls:
            # Try to find JSON objects directly in the text
            json_direct_pattern = r'\{[^{}]*"function"[^{}]*\}'
            direct_matches = re.findall(json_direct_pattern, response_text)
            
            for match in direct_matches:
                try:
                    parsed_json = json.loads(match)
                    if isinstance(parsed_json, dict) and "function" in parsed_json:
                        function_name = parsed_json.get("function")
                        parameters = parsed_json.get("parameters", {})
                        
                        tool_call = {
                            "function": function_name,
                            "parameters": parameters
                        }
                        tool_calls.append(tool_call)
                        
                        # Remove the JSON from content
                        cleaned_content = cleaned_content.replace(match, "").strip()
                        
                except json.JSONDecodeError:
                    continue
        
        # Clean up any thinking tags or extra whitespace
        cleaned_content = re.sub(r'<think>.*?</think>', '', cleaned_content, flags=re.DOTALL)
        cleaned_content = cleaned_content.strip()
        
        return len(tool_calls) > 0, tool_calls, cleaned_content
        
    except Exception as e:
        # If extraction fails, return the original content
        return False, [], response_text


def format_tool_calls_for_openai(extracted_tool_calls: List[Dict[str, Any]]) -> List[ToolCall]:
    """
    Format extracted tool calls into OpenAI's tool call format.
    
    Args:
        extracted_tool_calls (List[Dict]): List of extracted tool calls
        
    Returns:
        List[ToolCall]: List of formatted tool calls for OpenAI
    """
    formatted_tool_calls = []
    
    for tool_call in extracted_tool_calls:
        function_name = tool_call.get("function", "")
        parameters = tool_call.get("parameters", {})
        
        # Create a unique ID for the tool call
        call_id = f"call_{uuid.uuid4().hex[:24]}"
        
        formatted_call = ToolCall(
            id=call_id,
            type="function",
            function={
                "name": function_name,
                "arguments": json.dumps(parameters) if parameters else "{}"
            }
        )
        
        formatted_tool_calls.append(formatted_call)
    
    return formatted_tool_calls


def parse_and_format_tool_calls(response_text: str) -> Tuple[bool, List[ToolCall], str]:
    """
    Complete pipeline to extract and format tool calls from response text.
    
    Args:
        response_text (str): The AI response text
        
    Returns:
        tuple: (has_tool_calls: bool, formatted_tool_calls: List[ToolCall], cleaned_content: str)
    """
    # Extract tool calls from the response
    has_tool_calls, extracted_calls, cleaned_content = extract_tool_calls_from_response(response_text)
    
    # Format for OpenAI if tool calls were found
    formatted_calls = []
    if has_tool_calls:
        formatted_calls = format_tool_calls_for_openai(extracted_calls)
    
    return has_tool_calls, formatted_calls, cleaned_content


def validate_tool_call(tool_call: Dict[str, Any], available_tools: List[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Validate a tool call against available tools.
    
    Args:
        tool_call (Dict): The tool call to validate
        available_tools (List[Dict]): List of available tools to validate against
        
    Returns:
        tuple: (is_valid: bool, error_message: str)
    """
    try:
        function_name = tool_call.get("function")
        parameters = tool_call.get("parameters", {})
        
        if not function_name:
            return False, "Missing function name in tool call"
        
        if not isinstance(parameters, dict):
            return False, "Parameters must be a dictionary"
        
        # If available tools are provided, validate against them
        if available_tools:
            tool_found = False
            for tool in available_tools:
                if (isinstance(tool, dict) and 
                    tool.get("function", {}).get("name") == function_name):
                    tool_found = True
                    break
            
            if not tool_found:
                return False, f"Function '{function_name}' not found in available tools"
        
        return True, ""
        
    except Exception as e:
        return False, f"Validation error: {str(e)}"
