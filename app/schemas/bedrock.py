"""
Pydantic models for AWS Bedrock Converse API format.

These models represent the request and response structures for the Bedrock Converse API.
Note: These are simplified models focused on the conversion needs.
"""
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel


# Content Block Types for Bedrock
class BedrockTextContent(BaseModel):
    """Text content in Bedrock format."""
    text: str


class BedrockImageSource(BaseModel):
    """Image source in Bedrock format."""
    bytes: bytes  # Raw bytes (will be base64 decoded from Anthropic format)


class BedrockImageContent(BaseModel):
    """Image content in Bedrock format."""
    image: Dict[str, Any]  # {"format": "png", "source": {"bytes": b"..."}}


class BedrockDocumentSource(BaseModel):
    """Document source in Bedrock format."""
    bytes: bytes


class BedrockDocumentContent(BaseModel):
    """Document content in Bedrock format."""
    document: Dict[str, Any]  # {"format": "pdf", "name": "doc", "source": {"bytes": b"..."}}


class BedrockToolUseContent(BaseModel):
    """Tool use content in Bedrock format."""
    toolUse: Dict[str, Any]  # {"toolUseId": "...", "name": "...", "input": {...}}


class BedrockToolResultContent(BaseModel):
    """Tool result content in Bedrock format."""
    toolResult: Dict[str, Any]  # {"toolUseId": "...", "content": [...], "status": "success"}


# Message Structure for Bedrock
class BedrockMessage(BaseModel):
    """Message in Bedrock format."""
    role: Literal["user", "assistant"]
    content: List[Dict[str, Any]]  # List of content blocks


# Tool Configuration for Bedrock
class BedrockToolInputSchema(BaseModel):
    """Tool input schema in Bedrock format."""
    json: Dict[str, Any]  # JSON schema


class BedrockToolSpec(BaseModel):
    """Tool specification in Bedrock format."""
    name: str
    description: str
    inputSchema: BedrockToolInputSchema


class BedrockTool(BaseModel):
    """Tool definition in Bedrock format."""
    toolSpec: BedrockToolSpec


class BedrockToolConfig(BaseModel):
    """Tool configuration for Bedrock."""
    tools: List[BedrockTool]
    toolChoice: Optional[Dict[str, Any]] = None  # {"auto": {}} or {"tool": {"name": "..."}}


# Inference Configuration
class BedrockInferenceConfig(BaseModel):
    """Inference configuration for Bedrock."""
    maxTokens: int
    temperature: Optional[float] = None
    topP: Optional[float] = None
    stopSequences: Optional[List[str]] = None


# Additional Request Configuration
class BedrockAdditionalModelRequestFields(BaseModel):
    """Additional model-specific request fields."""
    top_k: Optional[int] = None


# Prompt Caching Configuration (if supported)
class BedrockPromptCachingConfig(BaseModel):
    """Configuration for prompt caching in Bedrock."""
    enabled: bool = False


# Request Model
class BedrockConverseRequest(BaseModel):
    """Bedrock Converse API request."""
    modelId: str
    messages: List[BedrockMessage]
    inferenceConfig: BedrockInferenceConfig

    # Optional parameters
    system: Optional[List[Dict[str, str]]] = None  # [{"text": "..."}]
    toolConfig: Optional[BedrockToolConfig] = None
    additionalModelRequestFields: Optional[Dict[str, Any]] = None


# Response Models
class BedrockTokenUsage(BaseModel):
    """Token usage in Bedrock format."""
    inputTokens: int
    outputTokens: int
    totalTokens: int


class BedrockConverseResponse(BaseModel):
    """Bedrock Converse API response (non-streaming)."""
    output: Dict[str, Any]  # {"message": {...}}
    stopReason: str  # "end_turn", "max_tokens", "stop_sequence", "tool_use", "content_filtered"
    usage: BedrockTokenUsage
    metrics: Optional[Dict[str, int]] = None  # {"latencyMs": ...}


# Streaming Event Models
class BedrockMessageStartEvent(BaseModel):
    """Bedrock stream event: messageStart."""
    role: Literal["assistant"] = "assistant"


class BedrockContentBlockStartEvent(BaseModel):
    """Bedrock stream event: contentBlockStart."""
    start: Dict[str, Any]  # {"toolUse": {"toolUseId": "...", "name": "..."}}
    contentBlockIndex: int


class BedrockContentBlockDeltaEvent(BaseModel):
    """Bedrock stream event: contentBlockDelta."""
    delta: Dict[str, Any]  # {"text": "..."} or {"toolUse": {"input": "..."}}
    contentBlockIndex: int


class BedrockContentBlockStopEvent(BaseModel):
    """Bedrock stream event: contentBlockStop."""
    contentBlockIndex: int


class BedrockMessageStopEvent(BaseModel):
    """Bedrock stream event: messageStop."""
    stopReason: str
    additionalModelResponseFields: Optional[Dict[str, Any]] = None


class BedrockMetadataEvent(BaseModel):
    """Bedrock stream event: metadata (usage information)."""
    usage: BedrockTokenUsage
    metrics: Optional[Dict[str, int]] = None


# Model Information
class BedrockModelSummary(BaseModel):
    """Information about a Bedrock model."""
    modelId: str
    modelName: str
    providerName: str
    inputModalities: List[str]
    outputModalities: List[str]
    responseStreamingSupported: bool
    customizationsSupported: Optional[List[str]] = None
