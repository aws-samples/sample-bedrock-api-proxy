"""
Converter from Anthropic Messages API format to AWS Bedrock Converse API format.

Handles conversion of requests, including messages, tools, system prompts,
and inference parameters.
"""
import base64
import json
from typing import Any, Dict, List, Optional, Union

from app.core.config import settings
from app.schemas.anthropic import (
    ContentBlock,
    ImageContent,
    DocumentContent,
    Message,
    MessageRequest,
    RedactedThinkingContent,
    SystemMessage,
    TextContent,
    ThinkingContent,
    Tool,
    ToolReferenceContent,
    ToolResultContent,
    ToolUseContent,
    # Standalone code execution types
    BashCodeExecutionToolResult,
    BashCodeExecutionResult,
    TextEditorCodeExecutionToolResult,
    TextEditorCodeExecutionResult,
    ServerToolUseContent,
    ServerToolResultContent,
    CodeExecutionResultContent,
)
from app.schemas.web_search import WEB_SEARCH_TOOL_TYPES


class AnthropicToBedrockConverter:
    """Converts Anthropic API format to Bedrock API format."""

    def __init__(self, dynamodb_client=None):
        """Initialize converter with model mapping.

        Args:
            dynamodb_client: Optional DynamoDB client for custom mappings
        """
        self.model_mapping = settings.default_model_mapping
        self.dynamodb_client = dynamodb_client
        self._model_mapping_manager = None
        self._resolved_model_id = None  # Cache the resolved model ID

    def convert_request(
        self, request: MessageRequest, anthropic_beta: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Convert Anthropic MessageRequest to Bedrock Converse request format.

        Args:
            request: Anthropic MessageRequest object
            anthropic_beta: Optional beta header from Anthropic client (comma-separated)

        Returns:
            Dictionary in Bedrock Converse API format
        """
        try:
            print(f"[CONVERTER] Converting Anthropic request to Bedrock format")
            print(f"  - Model: {request.model}")
            print(f"  - Messages: {len(request.messages)}")

            # Convert and cache the model ID
            self._resolved_model_id = self._convert_model_id(request.model)

            bedrock_request = {
                "modelId": self._resolved_model_id,
                "messages": self._convert_messages(request.messages),
                "inferenceConfig": self._convert_inference_config(request),
            }

            print(f"[CONVERTER] Converted model ID: {bedrock_request['modelId']}")
        except Exception as e:
            print(f"\n[ERROR] Exception in convert_request")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
            raise

        # Add system messages if present
        if request.system:
            bedrock_request["system"] = self._convert_system(request.system)

        # Add tool configuration if present
        if request.tools:
            bedrock_request["toolConfig"] = self._convert_tool_config(
                request.tools, request.tool_choice
            )

        # Add additional model request fields
        additional_fields = {}
        if request.top_k is not None:
            additional_fields["top_k"] = request.top_k

        # Handle extended thinking if enabled
        if request.thinking and settings.enable_extended_thinking:
            if self._is_nova_2_model():
                # Nova 2 models use a specific reasoningConfig format
                # Extract effort level from thinking config if provided, default to "medium"
                effort = "medium"
                if isinstance(request.thinking, dict):
                    # Map budget_tokens to effort level (heuristic)
                    budget = request.thinking.get("budget_tokens", 0)
                    if budget > 10000:
                        effort = "high"
                    elif budget < 1000:
                        effort = "low"
                additional_fields["reasoningConfig"] = {
                    "type": "enabled",
                    "maxReasoningEffort": effort
                }
                # Nova 2 requires temperature and maxTokens to be unset when reasoning is enabled
                # Remove them from inferenceConfig
                if "temperature" in bedrock_request["inferenceConfig"]:
                    del bedrock_request["inferenceConfig"]["temperature"]
                    print("[CONVERTER] Removed temperature for Nova 2 reasoning mode")
                if "maxTokens" in bedrock_request["inferenceConfig"]:
                    del bedrock_request["inferenceConfig"]["maxTokens"]
                    print("[CONVERTER] Removed maxTokens for Nova 2 reasoning mode")
                print(f"[CONVERTER] Added Nova 2 reasoningConfig with effort: {effort}")
            elif self._is_kimi_model():
                # Kimi models use reasoning_effort in additionalModelRequestFields
                # Only "high" is supported
                additional_fields["reasoning_effort"] = "high"
                print("[CONVERTER] Added reasoning_effort='high' for Kimi model")
            else:
                # Map thinking configuration to Bedrock-specific format for other models
                thinking_config = self._convert_thinking_config(request.thinking)
                if thinking_config:
                    additional_fields.update(thinking_config)

        # Add anthropic_beta features for Claude models (from client-provided headers)
        if self._is_claude_model() and anthropic_beta:
            bedrock_beta = []
            beta_values = [b.strip() for b in anthropic_beta.split(",")]

            for beta_value in beta_values:
                if beta_value in settings.beta_header_mapping and self._supports_beta_header_mapping(request.model):
                    # Map Anthropic beta headers to Bedrock beta headers
                    mapped = settings.beta_header_mapping[beta_value]
                    bedrock_beta.extend(mapped)
                    print(f"[CONVERTER] Mapped beta header '{beta_value}' → {mapped}")
                elif beta_value in settings.beta_headers_passthrough:
                    # Pass through directly without mapping
                    bedrock_beta.append(beta_value)
                    print(f"[CONVERTER] Passing through beta header: {beta_value}")
                elif beta_value in settings.beta_headers_blocklist:
                    # Filter out blocked headers (not supported by Bedrock)
                    print(f"[CONVERTER] Filtering out unsupported beta header: {beta_value}")
                else:
                    # Unknown beta header - pass through as-is
                    bedrock_beta.append(beta_value)
                    print(f"[CONVERTER] Unknown beta header, passing through: {beta_value}")

            if bedrock_beta:
                additional_fields["anthropic_beta"] = bedrock_beta
                print(f"[CONVERTER] Added anthropic_beta features: {bedrock_beta}")

                # If tool-examples beta is enabled, pass tools with input_examples
                # via additionalModelRequestFields (Bedrock toolSpec doesn't support inputExamples)
                if "tool-examples-2025-10-29" in bedrock_beta and request.tools:
                    tools_with_examples = self._get_tools_with_examples(request.tools)
                    if tools_with_examples:
                        additional_fields["tools"] = tools_with_examples
                        print(f"[CONVERTER] Added {len(tools_with_examples)} tools with input_examples to additionalModelRequestFields")

        if additional_fields:
            bedrock_request["additionalModelRequestFields"] = additional_fields

        return bedrock_request

    def _supports_prompt_caching(self) -> bool:
        """
        Check if the current model supports prompt caching.

        Only Claude models (Anthropic) on Bedrock support prompt caching.

        Returns:
            True if the model supports prompt caching, False otherwise
        """
        if not self._resolved_model_id:
            return False

        model_id_lower = self._resolved_model_id.lower()

        # Check if it's a Claude/Anthropic model
        is_claude = (
            "anthropic" in model_id_lower or
            "claude" in model_id_lower
        )

        if not is_claude and settings.prompt_caching_enabled:
            # Only log once per conversion
            if not hasattr(self, '_logged_cache_skip'):
                print(f"[CONVERTER] Skipping prompt caching for non-Claude model: {self._resolved_model_id}")
                self._logged_cache_skip = True

        return is_claude

    def _is_claude_model(self) -> bool:
        """
        Check if the current model is a Claude model.

        Returns:
            True if the model is Claude/Anthropic, False otherwise
        """
        if not self._resolved_model_id:
            return False

        model_id_lower = self._resolved_model_id.lower()
        return "anthropic" in model_id_lower or "claude" in model_id_lower

    def _is_nova_2_model(self) -> bool:
        """
        Check if the current model is an Amazon Nova 2 model.

        Nova 2 models require a specific reasoning configuration format.
        Model IDs include: amazon.nova-pro-2, amazon.nova-lite-2, amazon.nova-micro-2, etc.

        Returns:
            True if the model is Amazon Nova 2, False otherwise
        """
        if not self._resolved_model_id:
            return False

        model_id_lower = self._resolved_model_id.lower()
        # Match patterns like amazon.nova-pro-2, amazon.nova-lite-2, us.amazon.nova-pro-2, etc.
        return "amazon.nova" in model_id_lower and "-2" in model_id_lower

    def _is_kimi_model(self) -> bool:
        """
        Check if the current model is a Moonshot Kimi model.

        Kimi models require reasoning_effort in additionalModelRequestFields to enable thinking.
        Model IDs include: moonshotai.kimi-k2.5, etc.

        Returns:
            True if the model is a Kimi model, False otherwise
        """
        if not self._resolved_model_id:
            return False

        model_id_lower = self._resolved_model_id.lower()
        return "kimi" in model_id_lower or "moonshotai" in model_id_lower

    def _supports_beta_header_mapping(self, original_model_id: str) -> bool:
        """
        Check if the model supports beta header mapping.

        Args:
            original_model_id: The original Anthropic model ID from the request

        Returns:
            True if the model supports beta header mapping
        """
        if not self._resolved_model_id:
            return False

        # Check both original model ID and resolved model ID against supported models
        supported_models = settings.beta_header_supported_models
        return (
            original_model_id in supported_models or
            self._resolved_model_id in supported_models
        )

    def _map_beta_headers(self, anthropic_beta: str) -> List[str]:
        """
        Map Anthropic beta headers to Bedrock beta headers.

        Args:
            anthropic_beta: Comma-separated Anthropic beta header values

        Returns:
            List of mapped Bedrock beta headers
        """
        if not anthropic_beta:
            return []

        # Split comma-separated beta values
        beta_values = [b.strip() for b in anthropic_beta.split(",")]
        mapped_headers = []

        for beta_value in beta_values:
            if beta_value in settings.beta_header_mapping:
                # Map to Bedrock beta headers
                mapped = settings.beta_header_mapping[beta_value]
                mapped_headers.extend(mapped)
                print(f"[CONVERTER] Mapped beta header '{beta_value}' → {mapped}")
            else:
                # Keep unmapped beta values as-is (they might be passthrough)
                print(f"[CONVERTER] Beta header '{beta_value}' has no mapping, skipping")

        return mapped_headers

    def _get_tools_with_examples(self, tools: List[Any]) -> List[Dict[str, Any]]:
        """
        Extract tools with input_examples in Anthropic format for additionalModelRequestFields.

        This is needed because Bedrock's standard toolSpec doesn't support inputExamples.
        When the tool-examples beta is enabled, we pass tools with input_examples via
        additionalModelRequestFields.tools in Anthropic format.

        Args:
            tools: List of Tool objects or dicts

        Returns:
            List of tools in Anthropic format with input_examples
        """
        tools_with_examples = []

        for tool in tools:
            # Handle both dict and Pydantic model tools
            if isinstance(tool, dict):
                tool_input_examples = tool.get("input_examples")
                if tool_input_examples:
                    # Return tool in Anthropic format
                    tools_with_examples.append({
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("input_schema", {}),
                        "input_examples": tool_input_examples,
                    })
            else:
                tool_input_examples = getattr(tool, "input_examples", None)
                if tool_input_examples:
                    # Return tool in Anthropic format
                    tools_with_examples.append({
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": {
                            "type": tool.input_schema.type,
                            "properties": tool.input_schema.properties,
                            "required": tool.input_schema.required,
                        } if tool.input_schema else {},
                        "input_examples": tool_input_examples,
                    })

        return tools_with_examples

    def _convert_model_id(self, anthropic_model_id: str) -> str:
        """
        Convert Anthropic model ID to Bedrock model ARN.

        Resolution priority:
        1. Custom DynamoDB mapping (highest priority)
        2. Default config mapping
        3. Pass-through (use as-is)

        Args:
            anthropic_model_id: Anthropic model identifier

        Returns:
            Bedrock model ARN

        Raises:
            ValueError: If model ID is not found in mapping
        """
        # Priority 1: Check DynamoDB for custom mapping
        if self.dynamodb_client:
            try:
                from app.db.dynamodb import ModelMappingManager
                if not self._model_mapping_manager:
                    self._model_mapping_manager = ModelMappingManager(self.dynamodb_client)

                custom_mapping = self._model_mapping_manager.get_mapping(anthropic_model_id)
                if custom_mapping:
                    print(f"[CONVERTER] Using custom DynamoDB mapping: {anthropic_model_id} → {custom_mapping}")
                    return custom_mapping
            except Exception as e:
                print(f"[CONVERTER] Failed to check DynamoDB mapping: {e}")
                # Continue to default mapping

        # Priority 2: Check default config mapping
        bedrock_model_id = self.model_mapping.get(anthropic_model_id)

        if not bedrock_model_id:
            # Priority 3: Pass-through - assume it's already a valid Bedrock model ID
            # This allows users to directly specify Bedrock model ARNs
            print(f"[CONVERTER] No mapping found, using pass-through: {anthropic_model_id}")
            return anthropic_model_id

        print(f"[CONVERTER] Using default mapping: {anthropic_model_id} → {bedrock_model_id}")
        return bedrock_model_id

    def _convert_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """
        Convert list of Anthropic messages to Bedrock format.

        Args:
            messages: List of Anthropic Message objects

        Returns:
            List of messages in Bedrock format
        """
        bedrock_messages = []

        for message in messages:
            bedrock_message = {
                "role": message.role,
                "content": self._convert_content_blocks(message.content),
            }
            bedrock_messages.append(bedrock_message)

        return bedrock_messages

    def _convert_content_blocks(
        self, content: Union[str, List[ContentBlock]]
    ) -> List[Dict[str, Any]]:
        """
        Convert Anthropic content blocks to Bedrock format.

        Args:
            content: String or list of ContentBlock objects

        Returns:
            List of content blocks in Bedrock format
        """
        if isinstance(content, str):
            return [{"text": content}] if content else []

        bedrock_content = []

        for block in content:
            if isinstance(block, TextContent):
                if not block.text:
                    continue  # Skip empty text blocks - Bedrock Converse API rejects them
                bedrock_content.append({"text": block.text})
                # Add cache point as a separate block if cache_control is present
                # and model supports prompt caching (Claude models only)
                if block.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                    bedrock_content.append({"cachePoint": {"type": "default"}})
                    print(f"[CONVERTER] Added cachePoint after text block")

            elif isinstance(block, ImageContent):
                # Convert base64 image to bytes
                image_bytes = base64.b64decode(block.source.data)
                # Extract format from media_type (e.g., "image/png" -> "png")
                image_format = block.source.media_type.split("/")[-1]

                bedrock_content.append({
                    "image": {
                        "format": image_format,
                        "source": {"bytes": image_bytes},
                    }
                })
                # Add cache point as a separate block if cache_control is present
                # and model supports prompt caching (Claude models only)
                if block.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                    bedrock_content.append({"cachePoint": {"type": "default"}})
                    print(f"[CONVERTER] Added cachePoint after image block")

            elif isinstance(block, ToolReferenceContent):
                # Convert tool_reference to text for Converse API compatibility
                bedrock_content.append({"text": f"[Tool: {block.tool_name}]"})

            elif isinstance(block, DocumentContent):
                if not settings.enable_document_support:
                    continue

                # Convert base64 document to bytes
                doc_bytes = base64.b64decode(block.source.data)
                doc_format = block.source.media_type.split("/")[-1]

                bedrock_content.append({
                    "document": {
                        "format": doc_format,
                        "name": "document",
                        "source": {"bytes": doc_bytes},
                    }
                })
                # Add cache point as a separate block if cache_control is present
                # and model supports prompt caching (Claude models only)
                if block.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                    bedrock_content.append({"cachePoint": {"type": "default"}})
                    print(f"[CONVERTER] Added cachePoint after document block")

            elif isinstance(block, ThinkingContent):
                # Convert thinking block to Bedrock reasoningContent format
                if settings.enable_extended_thinking:
                    thinking_block = {
                        "reasoningContent": {
                            "reasoningText": {
                                "text": block.thinking
                            }
                        }
                    }
                    # Include signature if present (for multi-turn conversations)
                    if block.signature:
                        thinking_block["reasoningContent"]["reasoningText"]["signature"] = block.signature
                    bedrock_content.append(thinking_block)

            elif isinstance(block, RedactedThinkingContent):
                # Convert redacted thinking block to Bedrock format
                if settings.enable_extended_thinking:
                    bedrock_content.append({
                        "reasoningContent": {
                            "redactedContent": block.data
                        }
                    })

            elif isinstance(block, ToolUseContent):
                bedrock_content.append(
                    {
                        "toolUse": {
                            "toolUseId": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    }
                )

            elif isinstance(block, ToolResultContent):
                # Convert tool result content
                tool_result_content = []
                if isinstance(block.content, str):
                    tool_result_content = [{"text": block.content}]
                else:
                    tool_result_content = self._convert_content_blocks(block.content)

                bedrock_content.append(
                    {
                        "toolResult": {
                            "toolUseId": block.tool_use_id,
                            "content": tool_result_content,
                            "status": "error" if block.is_error else "success",
                        }
                    }
                )

            # Handle server_tool_use (PTC/Standalone code execution tool invocation)
            elif isinstance(block, ServerToolUseContent):
                # Convert server tool use to standard toolUse format for Bedrock
                bedrock_content.append(
                    {
                        "toolUse": {
                            "toolUseId": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    }
                )

            # Handle server_tool_result (PTC code execution result)
            elif isinstance(block, ServerToolResultContent):
                # Convert server tool result to toolResult format
                result_text_parts = []
                for result_item in block.content:
                    # Check for execution result types (CodeExecutionResultContent, BashCodeExecutionResult)
                    if isinstance(result_item, CodeExecutionResultContent):
                        result_text_parts.append(f"stdout: {result_item.stdout}")
                        if result_item.stderr:
                            result_text_parts.append(f"stderr: {result_item.stderr}")
                        result_text_parts.append(f"return_code: {result_item.return_code}")
                    elif isinstance(result_item, BashCodeExecutionResult):
                        result_text_parts.append(f"stdout: {result_item.stdout}")
                        if result_item.stderr:
                            result_text_parts.append(f"stderr: {result_item.stderr}")
                        result_text_parts.append(f"return_code: {result_item.return_code}")
                    elif isinstance(result_item, TextEditorCodeExecutionResult):
                        # Text editor result - convert to text representation
                        if result_item.error_code:
                            result_text_parts.append(f"error: {result_item.error_code}")
                        elif result_item.content is not None:
                            result_text_parts.append(f"content: {result_item.content}")
                        elif result_item.is_file_update is not None:
                            result_text_parts.append(f"is_file_update: {result_item.is_file_update}")
                        else:
                            result_text_parts.append("operation completed")

                bedrock_content.append(
                    {
                        "toolResult": {
                            "toolUseId": block.tool_use_id,
                            "content": [{"text": "\n".join(result_text_parts)}],
                            "status": "success",
                        }
                    }
                )

            # Handle standalone bash code execution tool result
            elif isinstance(block, BashCodeExecutionToolResult):
                # Convert bash execution result to toolResult format
                result_content = block.content
                result_text = f"stdout: {result_content.stdout}"
                if result_content.stderr:
                    result_text += f"\nstderr: {result_content.stderr}"
                result_text += f"\nreturn_code: {result_content.return_code}"

                bedrock_content.append(
                    {
                        "toolResult": {
                            "toolUseId": block.tool_use_id,
                            "content": [{"text": result_text}],
                            "status": "error" if result_content.return_code != 0 else "success",
                        }
                    }
                )

            # Handle standalone text editor code execution tool result
            elif isinstance(block, TextEditorCodeExecutionToolResult):
                # Convert text editor result to toolResult format
                result_content = block.content
                if result_content.error_code:
                    result_text = f"error: {result_content.error_code}"
                    status = "error"
                elif result_content.content is not None:
                    # View command result
                    result_text = f"file_type: {result_content.file_type}\n"
                    result_text += f"content:\n{result_content.content}"
                    if result_content.num_lines is not None:
                        result_text += f"\nlines: {result_content.num_lines}/{result_content.total_lines}"
                    status = "success"
                elif result_content.is_file_update is not None:
                    # Create command result
                    result_text = f"is_file_update: {result_content.is_file_update}"
                    status = "success"
                elif result_content.old_start is not None:
                    # str_replace command result
                    result_text = f"old_start: {result_content.old_start}, old_lines: {result_content.old_lines}\n"
                    result_text += f"new_start: {result_content.new_start}, new_lines: {result_content.new_lines}"
                    if result_content.lines:
                        result_text += f"\ndiff:\n" + "\n".join(result_content.lines)
                    status = "success"
                else:
                    result_text = "operation completed"
                    status = "success"

                bedrock_content.append(
                    {
                        "toolResult": {
                            "toolUseId": block.tool_use_id,
                            "content": [{"text": result_text}],
                            "status": status,
                        }
                    }
                )

            # Handle dict blocks (from raw message data)
            elif isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    text_value = block.get("text", "")
                    if text_value:  # Skip empty text blocks - Bedrock Converse API rejects them
                        bedrock_content.append({"text": text_value})
                elif block_type == "server_tool_use":
                    bedrock_content.append(
                        {
                            "toolUse": {
                                "toolUseId": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": block.get("input", {}),
                            }
                        }
                    )
                elif block_type in ("bash_code_execution_tool_result", "text_editor_code_execution_tool_result"):
                    # Handle raw dict format of standalone tool results
                    content = block.get("content", {})
                    if block_type == "bash_code_execution_tool_result":
                        result_text = f"stdout: {content.get('stdout', '')}"
                        if content.get('stderr'):
                            result_text += f"\nstderr: {content.get('stderr')}"
                        result_text += f"\nreturn_code: {content.get('return_code', 0)}"
                        status = "error" if content.get('return_code', 0) != 0 else "success"
                    else:
                        # text_editor result
                        if content.get('error_code'):
                            result_text = f"error: {content.get('error_code')}"
                            status = "error"
                        else:
                            result_text = json.dumps(content)
                            status = "success"

                    bedrock_content.append(
                        {
                            "toolResult": {
                                "toolUseId": block.get("tool_use_id", ""),
                                "content": [{"text": result_text}],
                                "status": status,
                            }
                        }
                    )
                elif block_type == "web_search_tool_result":
                    # Handle web search results in multi-turn: convert to toolResult
                    ws_content = block.get("content", [])
                    if isinstance(ws_content, list):
                        text_parts = []
                        for sr in ws_content:
                            if sr.get("type") == "web_search_result":
                                from app.schemas.web_search import decode_content as _decode
                                title = sr.get("title", "")
                                url = sr.get("url", "")
                                enc = sr.get("encrypted_content", "")
                                try:
                                    page_content = _decode(enc) if enc else ""
                                except Exception:
                                    page_content = enc
                                text_parts.append(f"Title: {title}\nURL: {url}\nContent: {page_content}")
                        result_text = "\n\n---\n\n".join(text_parts) if text_parts else "No results"
                    elif isinstance(ws_content, dict) and ws_content.get("type") == "web_search_tool_result_error":
                        result_text = f"Error: {ws_content.get('error_code', 'unknown')}"
                    else:
                        result_text = str(ws_content)
                    bedrock_content.append(
                        {
                            "toolResult": {
                                "toolUseId": block.get("tool_use_id", ""),
                                "content": [{"text": result_text}],
                                "status": "success",
                            }
                        }
                    )
                elif block_type == "web_fetch_tool_result":
                    # Handle web fetch results in multi-turn: convert to toolResult
                    wf_content = block.get("content", {})
                    if isinstance(wf_content, dict):
                        wf_type = wf_content.get("type", "")
                        if wf_type == "web_fetch_result":
                            doc = wf_content.get("content", {})
                            source = doc.get("source", {})
                            data = source.get("data", "")
                            title = doc.get("title", "")
                            url = wf_content.get("url", "")
                            result_text = f"Title: {title}\nURL: {url}\nContent: {data}"
                        elif wf_type == "web_fetch_tool_error":
                            result_text = f"Error: {wf_content.get('error_code', 'unknown')}"
                        else:
                            result_text = str(wf_content)
                    else:
                        result_text = str(wf_content)
                    bedrock_content.append(
                        {
                            "toolResult": {
                                "toolUseId": block.get("tool_use_id", ""),
                                "content": [{"text": result_text}],
                                "status": "success",
                            }
                        }
                    )
                elif block_type == "tool_use":
                    bedrock_content.append(
                        {
                            "toolUse": {
                                "toolUseId": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": block.get("input", {}),
                            }
                        }
                    )
                elif block_type == "tool_result":
                    tool_result_content = []
                    content = block.get("content", "")
                    if isinstance(content, str):
                        tool_result_content = [{"text": content}]
                    else:
                        tool_result_content = self._convert_content_blocks(content)
                    bedrock_content.append(
                        {
                            "toolResult": {
                                "toolUseId": block.get("tool_use_id", ""),
                                "content": tool_result_content,
                                "status": "error" if block.get("is_error") else "success",
                            }
                        }
                    )

        return bedrock_content

    def _convert_system(
        self, system: Union[str, List[SystemMessage]]
    ) -> List[Dict[str, str]]:
        """
        Convert Anthropic system messages to Bedrock format.

        Args:
            system: String or list of SystemMessage objects

        Returns:
            List of system messages in Bedrock format
        """
        if isinstance(system, str):
            return [{"text": system}]

        bedrock_system = []
        for msg in system:
            bedrock_system.append({"text": msg.text})
            # Add cache point as a separate block if cache_control is present
            # and model supports prompt caching (Claude models only)
            if msg.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                bedrock_system.append({"cachePoint": {"type": "default"}})
                print(f"[CONVERTER] Added cachePoint after system message")

        return bedrock_system

    def _convert_inference_config(
        self, request: MessageRequest
    ) -> Dict[str, Any]:
        """
        Convert inference parameters to Bedrock format.

        Args:
            request: Anthropic MessageRequest object

        Returns:
            Inference config dictionary for Bedrock
        """
        config = {
            "maxTokens": request.max_tokens,
        }

        if request.temperature is not None:
            config["temperature"] = request.temperature

        if request.top_p is not None:
            config["topP"] = request.top_p

        if request.stop_sequences:
            config["stopSequences"] = request.stop_sequences

        return config

    def _convert_tool_config(
        self,
        tools: List[Tool],
        tool_choice: Optional[Union[str, Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Convert Anthropic tool definitions to Bedrock format.

        Args:
            tools: List of Tool objects
            tool_choice: Tool choice specification

        Returns:
            Tool config dictionary for Bedrock
        """
        if not settings.enable_tool_use:
            return {}

        bedrock_tools = []

        for tool in tools:
            # Handle both dict and Pydantic model tools
            if isinstance(tool, dict):
                tool_name = tool.get("name")
                tool_desc = tool.get("description", "")
                input_schema = tool.get("input_schema", {})
                tool_type = input_schema.get("type", "object")
                tool_props = input_schema.get("properties", {})
                tool_required = input_schema.get("required")
                tool_cache_control = tool.get("cache_control")
                tool_input_examples = tool.get("input_examples")
                # Skip PTC code_execution tools (they're handled separately)
                if tool.get("type") == "code_execution_20250825":
                    continue
                # Skip web search tools (handled by WebSearchService)
                if tool.get("type") in WEB_SEARCH_TOOL_TYPES:
                    continue
            else:
                tool_name = tool.name
                tool_desc = tool.description
                tool_type = tool.input_schema.type
                tool_props = tool.input_schema.properties
                tool_required = tool.input_schema.required
                tool_cache_control = tool.cache_control
                tool_input_examples = getattr(tool, "input_examples", None)
                # Skip PTC code_execution tools
                if hasattr(tool, "type") and tool.type == "code_execution_20250825":
                    continue
                # Skip web search tools
                if hasattr(tool, "type") and tool.type in WEB_SEARCH_TOOL_TYPES:
                    continue

            bedrock_tool = {
                "toolSpec": {
                    "name": tool_name,
                    "description": tool_desc,
                    "inputSchema": {
                        "json": {
                            "type": tool_type,
                            "properties": tool_props,
                        }
                    },
                }
            }

            # Add required fields if present
            if tool_required:
                bedrock_tool["toolSpec"]["inputSchema"]["json"][
                    "required"
                ] = tool_required

            # Note: input_examples is handled separately via additionalModelRequestFields
            # when the tool-examples beta is enabled (see convert_request method)

            bedrock_tools.append(bedrock_tool)

            # Add cache point as a separate tool entry if cache_control is present
            # and model supports prompt caching (Claude models only)
            # Note: For tools, cachePoint might need to be at the toolConfig level
            # or added after all tools. This follows the same pattern as content/system.
            if tool_cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                bedrock_tools.append({"cachePoint": {"type": "default"}})
                print(f"[CONVERTER] Added cachePoint after tool: {tool_name}")

        tool_config = {"tools": bedrock_tools}

        # Convert tool choice
        if tool_choice:
            if isinstance(tool_choice, str):
                if tool_choice == "auto":
                    tool_config["toolChoice"] = {"auto": {}}
                elif tool_choice == "any":
                    tool_config["toolChoice"] = {"any": {}}
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
                tool_config["toolChoice"] = {
                    "tool": {"name": tool_choice["name"]}
                }

        return tool_config

    def _convert_thinking_config(
        self, thinking: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Convert Anthropic thinking configuration to Bedrock format.

        Args:
            thinking: Thinking configuration dictionary

        Returns:
            Bedrock-compatible thinking config or None
        """
        # Bedrock may not have direct thinking support
        # This is a placeholder for future implementation
        # For now, we could add thinking instructions to system prompt
        return None

    def get_model_mapping(self, anthropic_model_id: str) -> Optional[str]:
        """
        Get Bedrock model ID for an Anthropic model ID.

        Args:
            anthropic_model_id: Anthropic model identifier

        Returns:
            Bedrock model ARN or None if not found
        """
        return self.model_mapping.get(anthropic_model_id)

    def is_streaming_supported(self, model_id: str) -> bool:
        """
        Check if a model supports streaming.

        Args:
            model_id: Bedrock model ARN

        Returns:
            True if streaming is supported
        """
        # Most Bedrock models support streaming
        # Could be enhanced to check against actual model capabilities
        return True
