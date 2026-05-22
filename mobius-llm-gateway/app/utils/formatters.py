"""
Input and Output Formatters for converting between OpenAI and model-specific formats
"""

import time
import uuid
from typing import List, Dict, Any
from app.schemas.models import Message, ChatCompletionRequest, ChatCompletionResponse, Choice, Usage
from app.utils.tool_calls import parse_and_format_tool_calls


class InputFormatter:
    """Formats OpenAI input to model-specific format"""
    
    @staticmethod
    def format_messages_to_prompt(messages: List[Message], tools: List[Dict[str, Any]] = None) -> str:
        """Convert OpenAI messages format to a single prompt string with optional tools"""
        prompt_parts = []
        
        # Add system instructions with tools if available
        system_found = False
        for message in messages:
            if message.role == "system":
                system_content = message.content
                
                # If tools are available, add them to the system message
                if tools:
                    tools_section = InputFormatter._format_tools_section(tools)
                    system_content = f"{system_content}\n\n{tools_section}"
                
                prompt_parts.append(f"System Instructions : {system_content}")
                system_found = True
            elif message.role == "user":
                prompt_parts.append(f"User : {message.content}")
            elif message.role == "assistant":
                prompt_parts.append(f"Assistant : {message.content}")
        
        # If no system message was found but tools are available, add a default system message with tools
        if not system_found and tools:
            tools_section = InputFormatter._format_tools_section(tools)
            default_system = f"System Instructions : You are a helpful AI assistant that has access to tools.\n\n{tools_section}"
            prompt_parts.insert(0, default_system)

        # Add final assistant prompt
        prompt_parts.append("Assistant : ")
        
        return "\n\n".join(prompt_parts)
    
    @staticmethod
    def _format_tools_section(tools: List[Dict[str, Any]]) -> str:
        """Format tools section for the prompt"""
        if not tools:
            return ""
        
        tools_section = "AVAILABLE TOOLS:\n["
        
        formatted_tools = []
        for tool in tools:
            if isinstance(tool, dict) and 'function' in tool:
                function = tool['function']
                tool_str = "{\n"
                tool_str += f"  'type': 'function',\n"
                tool_str += f"  'function': {{\n"
                tool_str += f"    'name': '{function.get('name', '')}',\n"
                tool_str += f"    'description': '{function.get('description', '')}',\n"
                
                if 'parameters' in function:
                    tool_str += f"    'parameters': {{\n"
                    params = function['parameters']
                    if isinstance(params, dict):
                        tool_str += f"      'type': '{params.get('type', 'object')}',\n"
                        if 'properties' in params:
                            tool_str += f"      'properties': {{\n"
                            for prop_name, prop_def in params['properties'].items():
                                tool_str += f"        '{prop_name}': {{\n"
                                tool_str += f"          'type': '{prop_def.get('type', 'string')}',\n"
                                tool_str += f"          'description': '{prop_def.get('description', '')}',\n"
                                tool_str += f"        }},\n"
                            tool_str += f"      }},\n"
                        if 'required' in params:
                            required_fields = params['required']
                            tool_str += f"      'required': {required_fields},\n"
                    tool_str += f"    }},\n"
                
                tool_str += f"  }},\n"
                tool_str += "}"
                formatted_tools.append(tool_str)
        
        tools_section += ",\n".join(formatted_tools)
        tools_section += "]\n\n"
        
        # Add function calling rules
        tools_section += "FUNCTION CALLING RULES:\n"
        tools_section += "1. When users request actions that match available tools, use the appropriate function\n"
        tools_section += "2. Use this EXACT JSON format for function calls:\n\n"
        tools_section += "```json\n"
        tools_section += "{\n"
        tools_section += '    "function": "function_name",\n'
        tools_section += "    \"parameters\": {\n"
        tools_section += '        "param_name": "param_value"\n'
        tools_section += "    }\n"
        tools_section += "}\n"
        tools_section += "```\n\n"
        tools_section += "3. Always validate that required parameters are provided\n"
        tools_section += "4. Provide helpful information using the available tools\n\n"
        tools_section += "Be precise and helpful when using tools."
        
        return tools_section
    
    @staticmethod
    def format_for_ollama(request: ChatCompletionRequest) -> Dict[str, Any]:
        """Format OpenAI request for Ollama API"""
        
        # Convert tools to dictionary format for JSON serialization
        formatted_tools = None
        if request.tools:
            formatted_tools = [tool.dict() for tool in request.tools]
        
        # Generate prompt with tools embedded in the format
        prompt = InputFormatter.format_messages_to_prompt(request.messages, formatted_tools)
        
        # Convert tool_choice to dictionary format if it's an object
        formatted_tool_choice = request.tool_choice
        if hasattr(request.tool_choice, 'dict'):
            formatted_tool_choice = request.tool_choice.dict()
        
        return {
            "model": request.model,
            "prompt": prompt,
            "stream": request.stream,
            "tools": formatted_tools,
            "tool_choice": formatted_tool_choice,
            "options": {
                "temperature": request.temperature,
                "top_p": request.top_p,
                "num_predict": request.max_tokens,
            }
        }


class OutputFormatter:
    """Formats model output to OpenAI compatible format"""
    
    @staticmethod
    def format_ollama_response(ollama_response: Dict[str, Any], request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Convert Ollama response to OpenAI ChatCompletion format"""
        
        # Extract response text from Ollama response
        response_text = ollama_response.get("response", "")
        
        # Parse and extract tool calls from the response
        has_tool_calls, formatted_tool_calls, cleaned_content = parse_and_format_tool_calls(response_text)
        
        # Determine finish reason based on whether tool calls were found
        finish_reason = "tool_calls" if has_tool_calls else "stop"
        
        # Create the message with appropriate content and tool calls
        message = Message(
            role="assistant",
            content=cleaned_content if cleaned_content else None,
            tool_calls=formatted_tool_calls if formatted_tool_calls else None
        )
        
        # Create the choice object
        choice = Choice(
            index=0,
            message=message,
            finish_reason=finish_reason
        )
        
        # Estimate token usage (simple approximation)
        prompt_tokens = sum(len(msg.content.split()) for msg in request.messages if msg.content)
        completion_tokens = len(response_text.split())
        
        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens
        )
        
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:29]}",
            created=int(time.time()),
            model=request.model,
            choices=[choice],
            usage=usage
        )
