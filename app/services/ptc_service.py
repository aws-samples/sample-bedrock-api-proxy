"""
Programmatic Tool Calling (PTC) Service.

Orchestrates the PTC flow:
1. Detect PTC requests based on beta header and tools
2. Manage conversation with Claude via Bedrock
3. Execute code in Docker sandbox
4. Return tool calls to client for execution
5. Resume sandbox execution with tool results
"""

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from uuid import uuid4

from app.core.config import settings
from app.schemas.anthropic import MessageRequest, MessageResponse
from app.schemas.ptc import (
    PTC_BETA_HEADER,
    PTC_TOOL_TYPE,
    PTC_ALLOWED_CALLER,
    ContainerInfo,
    PTCExecutionState,
)
from app.services.ptc import (
    PTCSandboxExecutor,
    SandboxConfig,
    SandboxSession,
    ToolCallRequest,
    BatchToolCallRequest,
    ExecutionResult,
    DockerNotAvailableError,
    SandboxError,
    PendingToolCall,
)

logger = logging.getLogger(__name__)


def _filter_non_direct_tool_calls(messages: List[Any]) -> List[Any]:
    """
    Filter out non-direct tool calls and their corresponding results from messages.
    Also strips the 'caller' field from all tool_use blocks (Bedrock doesn't accept it).

    In PTC mode, tool calls with caller.type != "direct" are executed by the sandbox,
    not by Claude directly. These should NOT be included in conversation history
    sent to Claude.

    This function:
    1. Identifies tool_use blocks with caller.type != "direct" (or caller.type == "code_execution_20250825")
    2. Removes those tool_use blocks from assistant messages
    3. Removes corresponding tool_result blocks from user messages
    4. Also removes server_tool_use blocks (code_execution internal blocks)
    5. Strips the 'caller' field from all remaining tool_use blocks

    Args:
        messages: List of message dicts with role and content

    Returns:
        Filtered messages list
    """
    # First pass: collect tool_use IDs that should be filtered out
    # Also check if any tool_use blocks have 'caller' field that needs stripping
    non_direct_tool_ids = set()
    has_caller_fields = False

    for message in messages:
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content", [])
        elif hasattr(message, "role"):
            role = message.role
            content = message.content if hasattr(message, "content") else []
        else:
            continue

        if role != "assistant":
            continue

        if isinstance(content, str):
            continue

        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )

            block_type = block_dict.get("type")

            # Filter server_tool_use blocks (code_execution internal)
            if block_type == "server_tool_use":
                block_id = block_dict.get("id")
                if block_id:
                    non_direct_tool_ids.add(block_id)

            # Check tool_use blocks
            if block_type == "tool_use":
                caller = block_dict.get("caller")
                if caller:
                    has_caller_fields = True
                    caller_type = caller.get("type") if isinstance(caller, dict) else (
                        caller.type if hasattr(caller, "type") else None
                    )
                    # If caller exists and is NOT "direct", filter it out
                    if caller_type and caller_type != "direct":
                        block_id = block_dict.get("id")
                        if block_id:
                            non_direct_tool_ids.add(block_id)

    # Only return early if nothing needs to be modified
    if not non_direct_tool_ids and not has_caller_fields:
        return messages

    logger.debug(f"[PTC] Processing messages: filtering {len(non_direct_tool_ids)} non-direct tool call IDs, has_caller_fields={has_caller_fields}")

    # Second pass: filter messages
    filtered_messages = []
    logger.info(f"[_filter_non_direct_tool_calls] Processing {len(messages)} messages, filtering {len(non_direct_tool_ids)} non-direct tool IDs")

    for msg_idx, message in enumerate(messages):
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content", [])
        elif hasattr(message, "role"):
            role = message.role
            content = message.content if hasattr(message, "content") else []
        else:
            filtered_messages.append(message)
            continue

        if isinstance(content, str):
            filtered_messages.append(message)
            continue

        # Filter content blocks - separate thinking blocks to ensure correct ordering
        # Bedrock requires: if any thinking blocks exist in assistant messages, they must come first
        thinking_blocks = []
        other_blocks = []

        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )

            block_type = block_dict.get("type")

            # Skip server_tool_use blocks entirely
            if block_type == "server_tool_use":
                continue

            # Filter tool_use blocks
            if block_type == "tool_use":
                block_id = block_dict.get("id")
                if block_id in non_direct_tool_ids:
                    continue
                # Strip the 'caller' field from tool_use blocks - Bedrock doesn't accept it
                if "caller" in block_dict:
                    block_dict = {k: v for k, v in block_dict.items() if k != "caller"}
                # Use the modified block_dict for tool_use blocks
                other_blocks.append(block_dict)
                continue

            # Filter tool_result blocks for non-direct tool calls
            if block_type == "tool_result":
                tool_use_id = block_dict.get("tool_use_id")
                if tool_use_id in non_direct_tool_ids:
                    continue

            # Separate thinking blocks for assistant messages to ensure they come first
            if role == "assistant" and block_type in ("thinking", "redacted_thinking"):
                thinking_blocks.append(block_dict)
            else:
                other_blocks.append(block_dict)

        # Combine with thinking blocks first (only relevant for assistant messages)
        filtered_content = thinking_blocks + other_blocks

        # Debug: Log filtering result for assistant messages
        if role == "assistant":
            original_types = [
                b.get("type") if isinstance(b, dict) else getattr(b, "type", "?")
                for b in content if isinstance(b, (dict,)) or hasattr(b, "type")
            ]
            filtered_types = [b.get("type") if isinstance(b, dict) else "?" for b in filtered_content]
            logger.info(f"[_filter_non_direct_tool_calls] msg[{msg_idx}] assistant: {original_types} -> {filtered_types} (thinking_blocks={len(thinking_blocks)})")

        # Only add message if it has content
        if filtered_content:
            if isinstance(message, dict):
                filtered_messages.append({
                    **message,
                    "content": filtered_content
                })
            else:
                # For Pydantic models, create a new dict
                msg_dict = message.model_dump() if hasattr(message, "model_dump") else dict(message)
                msg_dict["content"] = filtered_content
                filtered_messages.append(msg_dict)

    return filtered_messages


def _filter_content_blocks_for_bedrock(content_blocks: List[Any]) -> List[Any]:
    """
    Filter content blocks to remove Bedrock-incompatible elements.

    This filters a list of content blocks (from an assistant message) to:
    1. Remove server_tool_use blocks (Bedrock only accepts specific server tools)
    2. Remove server_tool_result blocks
    3. Remove tool_use blocks with caller.type != "direct" (called from code execution)
       - These tool calls were made from sandbox code and their tool_results are being skipped
       - Including them would cause "tool_use without tool_result" validation errors
    4. Strip 'caller' field from remaining tool_use blocks
    5. Ensure thinking/redacted_thinking blocks come first (Bedrock requirement)

    Args:
        content_blocks: List of content block dicts

    Returns:
        Filtered list of content blocks with thinking blocks first
    """
    # Separate thinking blocks from other blocks to ensure correct ordering
    # Bedrock requires: if any thinking blocks exist, they must come first
    thinking_blocks = []
    other_blocks = []

    for block in content_blocks:
        block_dict = block if isinstance(block, dict) else (
            block.model_dump() if hasattr(block, "model_dump") else {}
        )

        block_type = block_dict.get("type")

        # Safety check: Log warning if we can't determine block type
        if not block_type:
            logger.warning(f"[_filter_content_blocks_for_bedrock] Block has no type: {type(block).__name__}, block={block}")

        # Skip server_tool_use blocks - Bedrock only accepts specific server tools
        # (web_search, tool_search_tool_regex, tool_search_tool_bm25)
        # Our code_execution is NOT a Bedrock server tool
        if block_type == "server_tool_use":
            logger.debug(f"[PTC] Filtering out server_tool_use block: {block_dict.get('name')}")
            continue

        # Skip server_tool_result blocks
        if block_type == "server_tool_result":
            logger.debug(f"[PTC] Filtering out server_tool_result block")
            continue

        # Handle tool_use blocks
        if block_type == "tool_use":
            caller = block_dict.get("caller")
            if caller:
                caller_type = caller.get("type") if isinstance(caller, dict) else (
                    caller.type if hasattr(caller, "type") else None
                )
                # Skip non-direct tool_use blocks (called from code execution)
                # These don't have corresponding tool_result in the messages we're building
                if caller_type and caller_type != "direct":
                    logger.debug(f"[PTC] Filtering out non-direct tool_use block: {block_dict.get('id')}")
                    continue
                # Strip 'caller' field from remaining (direct) tool_use blocks
                block_dict = {k: v for k, v in block_dict.items() if k != "caller"}
            other_blocks.append(block_dict)
            continue

        # Separate thinking blocks to ensure they come first
        if block_type in ("thinking", "redacted_thinking"):
            thinking_blocks.append(block_dict)
        else:
            other_blocks.append(block_dict)

    # Return with thinking blocks first (Bedrock requirement)
    result = thinking_blocks + other_blocks
    if thinking_blocks:
        result_types = [b.get("type") if isinstance(b, dict) else "?" for b in result]
        logger.info(f"[_filter_content_blocks_for_bedrock] Reordered with {len(thinking_blocks)} thinking blocks first: {result_types}")
    return result


class PTCService:
    """
    Service for handling Programmatic Tool Calling requests.

    This service manages the complex PTC flow where:
    - Claude generates code that calls tools
    - Code runs in a Docker sandbox
    - Tool calls are intercepted and returned to the client
    - Client executes tools and returns results
    - Sandbox continues execution with results
    """

    def __init__(self):
        self._sandbox_executor: Optional[PTCSandboxExecutor] = None
        self._execution_states: Dict[str, PTCExecutionState] = {}
        self._execution_generators: Dict[str, Any] = {}  # Store active generators

    @property
    def sandbox_executor(self) -> PTCSandboxExecutor:
        """Lazy-load sandbox executor."""
        if self._sandbox_executor is None:
            config = SandboxConfig(
                image=settings.ptc_sandbox_image,
                memory_limit=settings.ptc_memory_limit,
                timeout_seconds=settings.ptc_execution_timeout,
                network_disabled=settings.ptc_network_disabled,
                session_timeout_seconds=settings.ptc_session_timeout,
            )
            self._sandbox_executor = PTCSandboxExecutor(config)
            self._sandbox_executor.start_cleanup_task()
        return self._sandbox_executor

    def is_docker_available(self) -> bool:
        """Check if Docker is available for PTC."""
        try:
            return self.sandbox_executor.is_docker_available()
        except Exception:
            return False

    @staticmethod
    def is_ptc_request(request: MessageRequest, beta_header: Optional[str]) -> bool:
        """
        Check if request is a PTC request.

        Conditions:
        1. Beta header contains 'advanced-tool-use-2025-11-20'
        2. Tools include code_execution_20250825 type
        3. PTC is enabled in config
        """
        if not settings.enable_programmatic_tool_calling:
            return False

        # Check beta header
        if not beta_header or PTC_BETA_HEADER not in beta_header:
            return False

        # Check for code_execution tool
        if not request.tools:
            return False

        for tool in request.tools:
            # Handle both dict and Pydantic model
            if isinstance(tool, dict):
                if tool.get("type") == PTC_TOOL_TYPE:
                    return True
            elif hasattr(tool, "type") and tool.type == PTC_TOOL_TYPE:
                return True

        return False

    @staticmethod
    def get_ptc_tools(request: MessageRequest) -> Tuple[List[dict], List[dict]]:
        """
        Separate PTC tools from regular tools.

        Returns:
            Tuple of (code_execution_tools, ptc_callable_tools)
            - code_execution_tools: Tools that are code_execution type
            - ptc_callable_tools: Regular tools that can be called from code execution
        """
        code_execution_tools = []
        ptc_callable_tools = []

        for tool in (request.tools or []):
            tool_dict = tool if isinstance(tool, dict) else tool.model_dump()

            if tool_dict.get("type") == PTC_TOOL_TYPE:
                code_execution_tools.append(tool_dict)
            else:
                # Check if tool has allowed_callers
                allowed_callers = tool_dict.get("allowed_callers", ["direct"])
                if PTC_ALLOWED_CALLER in allowed_callers:
                    ptc_callable_tools.append(tool_dict)

        return code_execution_tools, ptc_callable_tools

    def _build_execute_code_tool(self, ptc_tools: List[dict]) -> dict:
        """
        Build the execute_code tool definition for Claude.

        This replaces the server-side code_execution tool with a regular
        tool that Claude can call, which we then handle in the sandbox.
        """
        # Build tool documentation
        tool_docs = []
        for tool in ptc_tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            schema = tool.get("input_schema", {})
            tool_docs.append(f"- {name}: {desc}\n  Parameters: {json.dumps(schema)}")

        tools_doc = "\n".join(tool_docs) if tool_docs else "No tools available"

        return {
            "name": "execute_code",
            "description": f"""Execute Python code in a sandboxed environment.

The code can call the following async tool functions:
{tools_doc}

Important:
- All tool calls must use `await`, e.g., `result = await query_database(sql="SELECT * FROM users")`
- Use `print()` to output results you want to see
- Code runs in an isolated environment without network access
- Only the print output will be returned

Performance optimization - PARALLEL EXECUTION:
When you need to call the same tool multiple times with different parameters (e.g., fetching data for multiple items), ALWAYS use asyncio.gather for parallel execution instead of sequential loops:

BAD (slow, sequential):
```python
results = []
for item_id in item_ids:
    result = await get_item(id=item_id)
    results.append(result)
```

GOOD (fast, parallel):
```python
import asyncio
tasks = [get_item(id=item_id) for item_id in item_ids]
results = await asyncio.gather(*tasks)
```

This significantly improves performance by executing multiple tool calls concurrently.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Use await for tool calls. Use asyncio.gather for parallel tool calls."
                    }
                },
                "required": ["code"]
            }
        }

    def prepare_bedrock_request(
        self,
        request: MessageRequest,
        ptc_callable_tools: List[dict]
    ) -> MessageRequest:
        """
        Prepare request for Bedrock by replacing PTC tools with execute_code.

        This transforms the request to remove server-side code_execution tool
        and add our own execute_code tool that we handle locally.
        """
        # Build new tools list
        new_tools = []

        # Add execute_code tool
        execute_code_tool = self._build_execute_code_tool(ptc_callable_tools)
        new_tools.append(execute_code_tool)

        # Add any "direct" callable tools
        for tool in (request.tools or []):
            tool_dict = tool if isinstance(tool, dict) else tool.model_dump()

            # Skip code_execution server tool
            if tool_dict.get("type") == PTC_TOOL_TYPE:
                continue

            # Skip execute_code tool (we add it ourselves above)
            # This prevents duplicates when request.tools already contains execute_code
            # from a previous prepare_bedrock_request() call
            if tool_dict.get("name") == "execute_code":
                continue

            # Check if tool is direct-callable
            allowed_callers = tool_dict.get("allowed_callers", ["direct"])
            if "direct" in allowed_callers:
                # Remove allowed_callers field for Bedrock
                tool_copy = {k: v for k, v in tool_dict.items() if k != "allowed_callers"}
                new_tools.append(tool_copy)

        # Create modified request
        request_dict = request.model_dump()
        request_dict["tools"] = new_tools

        # Filter messages to strip 'caller' fields from tool_use blocks
        # Bedrock doesn't accept the 'caller' field which is an Anthropic PTC extension
        request_dict["messages"] = _filter_non_direct_tool_calls(request_dict.get("messages", []))

        # Append PTC system prompt for parallel execution guidance
        ptc_system_prompt = self._build_ptc_system_prompt(ptc_callable_tools)
        existing_system = request_dict.get("system")

        if existing_system:
            if isinstance(existing_system, str):
                request_dict["system"] = existing_system + "\n\n" + ptc_system_prompt
            elif isinstance(existing_system, list):
                # System is a list of content blocks
                request_dict["system"] = existing_system + [{"type": "text", "text": ptc_system_prompt}]
        else:
            request_dict["system"] = ptc_system_prompt

        return MessageRequest(**request_dict)

    def _build_ptc_system_prompt(self, ptc_tools: List[dict]) -> str:
        """Build system prompt additions for PTC mode."""
        # Build tool documentation
        tool_docs = []
        for tool in ptc_tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            schema = tool.get("input_schema", {})
            properties = schema.get("properties", {})
            params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in properties.items())
            tool_docs.append(f"- `{name}({params})`: {desc}")

        tools_doc = "\n".join(tool_docs) if tool_docs else "No tools available"

        return f"""## Code Execution Environment

You have access to the `execute_code` tool which runs Python code in a sandboxed environment. Within your code, you can call the following async tool functions:

{tools_doc}

## Usage

When you need to execute multi-step tasks, use the `execute_code` tool to write Python code.

### Key Rules:
1. All tool calls must use `await`, for example: `result = await query_sales(region="East")`
2. Use `print()` to output results - this is the only way for you to get execution results
3. You can perform data processing, filtering, aggregation, and conditional logic in your code
4. After code execution completes, you will see the content output by print

## CRITICAL: Stateless Execution Environment

**IMPORTANT: Each `execute_code` call runs in a FRESH, ISOLATED environment.**

- Variables, data, and state from previous code executions DO NOT persist
- Each code block starts with a completely clean slate
- You CANNOT reference variables defined in previous `execute_code` calls

### What This Means:

**WRONG** - Assuming variables persist across calls:
```python
# First execute_code call
products = await get_inventory(warehouse="NYC")
print(products)

# Second execute_code call - THIS WILL FAIL!
# products does not exist here!
for item in products:  # NameError: 'products' is not defined
    details = await get_product_details(sku=item['sku'])
```

**CORRECT** - Complete all work in a SINGLE code block (STRONGLY PREFERRED):
```python
import json
import asyncio

# Do EVERYTHING in one code block
inventory_data = await get_inventory(warehouse="NYC")
products = json.loads(inventory_data)

# Continue processing in the same block
detail_tasks = [get_product_details(sku=p['sku']) for p in products]
details = await asyncio.gather(*detail_tasks)

# Analyze and print final results
for product, detail in zip(products, details):
    print(f"{{product['name']}}: {{detail}}")
```

**CORRECT** - If multiple blocks unavoidable, re-fetch data:
```python
import json

# In a NEW code block, re-fetch the data you need
inventory_data = await get_inventory(warehouse="NYC")
products = json.loads(inventory_data)

# Now continue processing
detail_tasks = [get_product_details(sku=p['sku']) for p in products]
# ...
```

## Best Practices for Coding

### 1. Complete Tasks in One Block (MOST IMPORTANT)

For multi-step tasks, write ONE code block that accomplishes everything:

```python
import json
import asyncio

# Step 1: Get all orders from the past week
orders_data = await get_recent_orders(days=7)
orders = json.loads(orders_data)
print(f"Processing {{len(orders)}} orders")

# Step 2: Get customer info for all orders in parallel
customer_ids = list(set(order['customer_id'] for order in orders))
customer_tasks = [get_customer(customer_id=cid) for cid in customer_ids]
customer_results = await asyncio.gather(*customer_tasks)
customers = {{cid: json.loads(data) for cid, data in zip(customer_ids, customer_results)}}

# Step 3: Find high-value orders from premium customers
HIGH_VALUE_THRESHOLD = 1000
premium_high_value = []
for order in orders:
    customer = customers[order['customer_id']]
    if customer['tier'] == 'premium' and order['total'] > HIGH_VALUE_THRESHOLD:
        premium_high_value.append({{
            'order_id': order['id'],
            'customer_name': customer['name'],
            'total': order['total']
        }})

# Step 4: Get shipping status for these orders
if premium_high_value:
    shipping_tasks = [get_shipping_status(order_id=o['order_id']) for o in premium_high_value]
    shipping_results = await asyncio.gather(*shipping_tasks)
  
    print("\nPremium customers with high-value orders:")
    for order_info, shipping_json in zip(premium_high_value, shipping_results):
        shipping = json.loads(shipping_json)
        print(f"  Order {{order_info['order_id']}}: ${{order_info['total']:,.2f}} - {{order_info['customer_name']}} - Status: {{shipping['status']}}")
else:
    print("No high-value orders from premium customers found")
```

### 2. Parallel Execution with asyncio.gather()

When calling the same tool for multiple items, always use parallel execution:

```python
import asyncio
import json

# Get health metrics for multiple servers in parallel
server_ids = ["srv-001", "srv-002", "srv-003", "srv-004"]
health_tasks = [check_server_health(server_id=sid) for sid in server_ids]
health_results = await asyncio.gather(*health_tasks)

# Process results
unhealthy = []
for server_id, health_json in zip(server_ids, health_results):
    health = json.loads(health_json)
    if health['cpu_usage'] > 90 or health['memory_usage'] > 85:
        unhealthy.append(f"{{server_id}}: CPU={{health['cpu_usage']}}%, MEM={{health['memory_usage']}}%")

if unhealthy:
    print("Servers needing attention:")
    for s in unhealthy:
        print(f"{{s}}")
else:
    print("All servers healthy")
```

### 3. Conditional Logic Within One Block

Handle all branching logic in a single execution:

```python
import json

# Get account status first
account_data = await get_account(account_id="ACC-12345")
account = json.loads(account_data)

if account['status'] == 'suspended':
    # Get suspension details
    suspension_info = await get_suspension_details(account_id="ACC-12345")
    print(f"Account suspended: {{json.loads(suspension_info)['reason']}}")
  
elif account['balance'] < 0:
    # Get payment history for accounts with negative balance
    payments = await get_payment_history(account_id="ACC-12345", limit=5)
    print(f"Negative balance. Recent payments: {{payments}}")
  
else:
    # Get recommendations for active accounts
    recommendations = await get_recommendations(account_id="ACC-12345")
    print(f"Account active. Recommendations: {{recommendations}}")
```

### 4. Early Termination Pattern

Stop processing once you find what you need:

```python
import json

regions = ["us-east", "us-west", "eu-central", "ap-southeast"]
available_region = None

for region in regions:
    capacity_data = await check_capacity(region=region)
    capacity = json.loads(capacity_data)
  
    if capacity['available_slots'] >= 10:
        available_region = region
        print(f"Found suitable region: {{region}} with {{capacity['available_slots']}} slots")
        break
    else:
        print(f"{{region}}: only {{capacity['available_slots']}} slots available")

if not available_region:
    print("No region with sufficient capacity found")
```

### 5. Aggregation and Analysis

Fetch data and perform complex analysis in one block:

```python
import json
import asyncio
from collections import defaultdict

# Get all transactions for the quarter
transactions_data = await get_transactions(quarter="Q3", year=2024)
transactions = json.loads(transactions_data)

# Aggregate by category
category_totals = defaultdict(float)
category_counts = defaultdict(int)

for txn in transactions:
    category_totals[txn['category']] += txn['amount']
    category_counts[txn['category']] += 1

# Find categories exceeding budget
budgets = {{'marketing': 50000, 'operations': 75000, 'travel': 20000, 'equipment': 30000}}

print("Q3 Spending Analysis:")
print("-" * 50)
for category, total in sorted(category_totals.items(), key=lambda x: -x[1]):
    budget = budgets.get(category, 0)
    status = "OVER" if total > budget else "OK"
    variance = total - budget
    print(f"{{category:15}} ${{total:>10,.2f}} / ${{budget:>10,.2f}} ({{status}}, {{variance:+,.2f}})")
```

## When Multiple Code Blocks Are Unavoidable

If a task requires user decisions between steps or is too complex for one block:

1. **Print clear, structured output** from the first block
2. **Re-fetch or reconstruct data** in subsequent blocks - never assume variables exist
3. **Prefer re-fetching** over reconstructing from printed output (more reliable)

```python
# If you need another code block, ALWAYS start fresh:
import json

# Re-fetch the data - don't assume anything exists from before
inventory_data = await get_inventory(warehouse="NYC")
products = json.loads(inventory_data)

# Now continue with your analysis...
```

## Docker Sandbox Features
- Secure, isolated execution environment
- **Each execution starts fresh with no state from previous executions**
- Network disabled for security
- Resource limits enforced (memory, CPU)
- Timeout protection

## Pre-Code Checklist

Before writing code, verify:
- [ ] I am NOT referencing variables from a previous `execute_code` call
- [ ] I have included all necessary imports (`json`, `asyncio`, etc.)
- [ ] I am using `await` for all async tool calls
- [ ] I am using `json.loads()` to parse tool return values
- [ ] I am using `print()` to output all results I need to see
- [ ] I am completing as much as possible in this single code block
"""

    async def handle_ptc_request(
        self,
        request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        container_id: Optional[str] = None,
        anthropic_beta: Optional[str] = None,
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Handle a PTC request.

        This is the main entry point for PTC requests. It:
        1. Prepares the request for Bedrock
        2. Calls Claude
        3. If Claude returns execute_code, runs code in sandbox
        4. Returns tool_use if sandbox needs external tool
        5. Otherwise returns final response

        Args:
            request: The original request
            bedrock_service: Bedrock service for calling Claude
            request_id: Request ID
            service_tier: Bedrock service tier
            container_id: Optional container ID for session reuse
            anthropic_beta: Optional beta header from Anthropic client

        Returns:
            Tuple of (response, container_info)
        """
        # Start PTC tracing span
        _ptc_span = None
        if settings.enable_tracing:
            from app.tracing.spans import start_ptc_span
            from app.tracing.provider import get_tracer
            tracer = get_tracer("app.services.ptc")
            _ptc_span = start_ptc_span(tracer, session_id=container_id or "new")

        # Check Docker availability
        if not self.is_docker_available():
            if _ptc_span is not None:
                _ptc_span.end()
            raise DockerNotAvailableError(
                "Programmatic Tool Calling requires Docker which is not available. "
                "Please ensure Docker is running."
            )

        # Debug: Log incoming messages to see what the client sent
        logger.info(f"[PTC] handle_ptc_request incoming messages ({len(request.messages)}):")
        for idx, msg in enumerate(request.messages):
            content = msg.content
            if isinstance(content, str):
                logger.info(f"[PTC]   messages[{idx}]: role={msg.role}, content=str")
            elif isinstance(content, list):
                types = [getattr(b, "type", "?") if hasattr(b, "type") else b.get("type", "?") for b in content]
                logger.info(f"[PTC]   messages[{idx}]: role={msg.role}, content_types={types}")

        # Get PTC tools
        _, ptc_callable_tools = self.get_ptc_tools(request)

        # Prepare request for Bedrock
        bedrock_request = self.prepare_bedrock_request(request, ptc_callable_tools)

        # Get or create sandbox session
        session = await self._get_or_create_session(container_id, ptc_callable_tools)

        try:
            # Call Bedrock (with beta header)
            response = await bedrock_service.invoke_model(
                bedrock_request, request_id, service_tier, anthropic_beta
            )

            # Check if Claude called execute_code
            execute_code_call = self._find_execute_code_call(response)

            if execute_code_call:
                # Execute code in sandbox
                # Pass bedrock_request (which includes PTC system prompt) instead of original request
                # This ensures the PTC system prompt is preserved in continuation requests
                result = await self._handle_code_execution(
                    execute_code_call,
                    response,
                    session,
                    bedrock_request,  # Use prepared request with PTC system prompt
                    bedrock_service,
                    request_id,
                    service_tier,
                    ptc_callable_tools,
                    anthropic_beta,
                )
                if _ptc_span is not None:
                    _ptc_span.end()
                return result
            else:
                # No code execution, return response with container info
                # Add caller: {type: "direct"} to any direct tool_use blocks
                response = self._add_direct_caller_to_tool_use(response)
                container_info = ContainerInfo(
                    id=session.session_id,
                    expires_at=session.expires_at.isoformat()
                )
                if _ptc_span is not None:
                    _ptc_span.end()
                return response, container_info

        except Exception as e:
            logger.error(f"Error handling PTC request: {e}")
            if _ptc_span is not None:
                from app.tracing.spans import set_error_on_span
                set_error_on_span(_ptc_span, e)
                _ptc_span.end()
            raise

    async def _get_or_create_session(
        self,
        container_id: Optional[str],
        tools: List[dict]
    ) -> SandboxSession:
        """Get existing session or create new one."""
        session = None

        if container_id:
            session = self.sandbox_executor.get_session(container_id)

        if session is None:
            # Create new session with tool definitions
            tool_defs = [
                {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", {})
                }
                for t in tools
            ]
            session = await self.sandbox_executor.create_session(tool_defs)

        return session

    def _find_execute_code_call(self, response: MessageResponse) -> Optional[dict]:
        """Find execute_code tool call in response."""
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                if hasattr(block, "name") and block.name == "execute_code":
                    return {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input if hasattr(block, "input") else {}
                    }
            elif isinstance(block, dict):
                if block.get("type") == "tool_use" and block.get("name") == "execute_code":
                    return block

        return None

    async def _handle_code_execution(
        self,
        execute_code_call: dict,
        claude_response: MessageResponse,
        session: SandboxSession,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        ptc_callable_tools: List[dict],
        anthropic_beta: Optional[str] = None,
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Handle code execution in sandbox.

        When Claude calls execute_code:
        1. Run code in sandbox
        2. If sandbox calls external tool, return tool_use to client
        3. If code completes, send result back to Claude
        """
        code = execute_code_call.get("input", {}).get("code", "")
        code_execution_tool_id = f"srvtoolu_{uuid4().hex[:12]}"

        # Check if there's a pending tool call for this session
        # If so, the container is waiting for a tool result - we can't send new code
        pending_state = self._execution_states.get(session.session_id)
        if pending_state or session.pending_tool_call or session.is_busy:
            reason = []
            if pending_state:
                reason.append(f"pending tool call ({pending_state.pending_tool_name})")
            if session.pending_tool_call:
                reason.append(f"session pending_tool_call ({session.pending_tool_call.tool_name})")
            if session.is_busy:
                reason.append("session is_busy")

            logger.warning(
                f"Session {session.session_id} in inconsistent state: {', '.join(reason)}. "
                "Creating new session."
            )
            # Clean up the pending state - the old execution is abandoned
            self._cleanup_execution_state(session.session_id)
            # Close the old session and create a new one - container is in inconsistent state
            await self.sandbox_executor.close_session(session.session_id)
            # Create fresh session
            tool_defs = [
                {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", {})
                }
                for t in ptc_callable_tools
            ]
            session = await self.sandbox_executor.create_session(tool_defs)
            logger.info(f"Created new session {session.session_id} after cleaning up stale state")

        logger.info(f"Executing code in sandbox:\n{code}")

        # Extract original assistant content (including thinking blocks) for later use
        # This is needed when thinking is enabled - Claude requires assistant messages to start with thinking
        original_assistant_content = []
        for block in claude_response.content:
            if hasattr(block, "model_dump"):
                original_assistant_content.append(block.model_dump())
            elif isinstance(block, dict):
                original_assistant_content.append(block)

        # Get the original execute_code tool_use ID
        original_execute_code_id = execute_code_call.get("id")

        # Execute code in sandbox (using async generator pattern)
        gen = self.sandbox_executor.execute_code(code, session)

        try:
            # Get first result (either tool call, batch of tool calls, or final result)
            result = await gen.__anext__()

            while isinstance(result, (ToolCallRequest, BatchToolCallRequest)):
                # Tool call(s) requested - return to client
                container_info = ContainerInfo(
                    id=session.session_id,
                    expires_at=session.expires_at.isoformat()
                )

                if isinstance(result, BatchToolCallRequest):
                    # Multiple parallel tool calls
                    logger.info(f"[PTC] Batch of {len(result)} tool calls")
                    first_call = result.requests[0]
                    pending_call_ids = [r.call_id for r in result.requests]

                    # Store execution state for resume (including original request context)
                    state = PTCExecutionState(
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id,
                        code=code,  # Store actual code for response
                        pending_tool_call_id=first_call.call_id,  # Track first call
                        pending_tool_name=first_call.tool_name,
                        pending_tool_input=first_call.arguments,
                        pending_batch_call_ids=pending_call_ids,  # Track all call IDs
                        # Preserve original request context for finalization
                        original_system=original_request.system,
                        original_model=original_request.model,
                        original_max_tokens=original_request.max_tokens,
                        original_temperature=original_request.temperature,
                        original_top_p=original_request.top_p,
                        original_top_k=original_request.top_k,
                        original_stop_sequences=original_request.stop_sequences,
                        original_tool_choice=original_request.tool_choice,
                        original_thinking=original_request.thinking,
                        original_anthropic_beta=anthropic_beta,
                        # Preserve original assistant content (including thinking blocks)
                        original_assistant_content=original_assistant_content,
                        original_execute_code_id=original_execute_code_id,
                    )
                    self._execution_states[session.session_id] = state
                    self._execution_generators[session.session_id] = gen

                    # Mark session as having pending tool calls
                    session.pending_tool_call = PendingToolCall(
                        call_id=first_call.call_id,
                        tool_name=first_call.tool_name,
                        arguments=first_call.arguments,
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id
                    )

                    # Build response with multiple tool_use blocks
                    tool_use_response = self._build_batch_tool_use_response(
                        result,
                        code_execution_tool_id,
                        claude_response,
                        container_info,
                        code=code
                    )

                    return tool_use_response, container_info

                else:
                    # Single tool call (original behavior)
                    # Store execution state for resume (including original request context)
                    state = PTCExecutionState(
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id,
                        code=code,  # Store actual code for response
                        pending_tool_call_id=result.call_id,
                        pending_tool_name=result.tool_name,
                        pending_tool_input=result.arguments,
                        # Preserve original request context for finalization
                        original_system=original_request.system,
                        original_model=original_request.model,
                        original_max_tokens=original_request.max_tokens,
                        original_temperature=original_request.temperature,
                        original_top_p=original_request.top_p,
                        original_top_k=original_request.top_k,
                        original_stop_sequences=original_request.stop_sequences,
                        original_tool_choice=original_request.tool_choice,
                        original_thinking=original_request.thinking,
                        original_anthropic_beta=anthropic_beta,
                        # Preserve original assistant content (including thinking blocks)
                        original_assistant_content=original_assistant_content,
                        original_execute_code_id=original_execute_code_id,
                    )
                    self._execution_states[session.session_id] = state
                    self._execution_generators[session.session_id] = gen

                    # Also mark the session itself as having a pending tool call
                    session.pending_tool_call = PendingToolCall(
                        call_id=result.call_id,
                        tool_name=result.tool_name,
                        arguments=result.arguments,
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id
                    )

                    # Build response with tool_use and caller info
                    tool_use_response = self._build_tool_use_response(
                        result,
                        code_execution_tool_id,
                        claude_response,
                        container_info,
                        code=code
                    )

                    return tool_use_response, container_info

            # Code completed - result is ExecutionResult
            if isinstance(result, ExecutionResult):
                # Close the generator to trigger its finally block (clears is_busy)
                await gen.aclose()
                session.is_busy = False  # Explicitly clear just in case
                # Send result back to Claude
                return await self._complete_code_execution(
                    result,
                    execute_code_call,
                    claude_response,
                    original_request,
                    bedrock_service,
                    request_id,
                    service_tier,
                    session,
                    ptc_callable_tools,
                    anthropic_beta,
                )

        except StopAsyncIteration:
            # Generator completed without yielding
            logger.warning("Sandbox generator completed unexpectedly")
            raise SandboxError("Code execution completed unexpectedly")

    async def resume_execution(
        self,
        session_id: str,
        tool_result: Any,
        is_error: bool = False
    ) -> Tuple[Any, bool]:
        """
        Resume code execution after tool result.

        Args:
            session_id: Session ID
            tool_result: Result from tool execution (or error message)
            is_error: Whether the result is an error

        Returns:
            Tuple of (next_result, is_complete)
            - next_result: Either ToolCallRequest or ExecutionResult
            - is_complete: True if execution is complete
        """
        state = self._execution_states.get(session_id)
        gen = self._execution_generators.get(session_id)

        if not state or not gen:
            raise ValueError(f"No pending execution for session {session_id}")

        try:
            if is_error:
                # Inject error into sandbox
                session = self.sandbox_executor.get_session(session_id)
                if session:
                    self.sandbox_executor.inject_tool_error(
                        session,
                        state.pending_tool_call_id,
                        str(tool_result)
                    )
                # Get next result
                result = await gen.__anext__()
            else:
                # Send result and get next
                result = await gen.asend(tool_result)

            if isinstance(result, ToolCallRequest):
                # Another single tool call
                state.pending_tool_call_id = result.call_id
                state.pending_tool_name = result.tool_name
                state.pending_tool_input = result.arguments
                state.pending_batch_call_ids = None  # Clear batch IDs
                state.tool_call_count += 1
                return result, False
            elif isinstance(result, BatchToolCallRequest):
                # Batch of parallel tool calls
                first_call = result.requests[0]
                state.pending_tool_call_id = first_call.call_id
                state.pending_tool_name = first_call.tool_name
                state.pending_tool_input = first_call.arguments
                state.pending_batch_call_ids = [r.call_id for r in result.requests]
                state.tool_call_count += len(result.requests)
                return result, False
            else:
                # Execution complete
                self._cleanup_execution_state(session_id)
                return result, True

        except StopAsyncIteration:
            self._cleanup_execution_state(session_id)
            return None, True

    async def handle_tool_result_continuation(
        self,
        session_id: str,
        tool_result: Any,
        is_error: bool,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Handle tool_result continuation for a pending sandbox execution.

        This is called when client sends back a tool_result for a PTC-originated
        tool call. It resumes the paused sandbox execution.

        Args:
            session_id: The session/container ID
            tool_result: The result from the client's tool execution
            is_error: Whether the tool execution resulted in an error
            original_request: The original request (for context)
            bedrock_service: Bedrock service for Claude calls
            request_id: Request ID for logging
            service_tier: Service tier for Bedrock

        Returns:
            Tuple of (response, container_info)
        """
        state = self._execution_states.get(session_id)
        if not state:
            raise ValueError(f"No pending execution for session {session_id}")

        logger.info(f"[PTC] Resuming execution for session {session_id}, tool={state.pending_tool_name}")

        # Debug: Log incoming messages during continuation to see what the client echoed back
        logger.info(f"[PTC] handle_tool_result_continuation incoming messages ({len(original_request.messages)}):")
        for idx, msg in enumerate(original_request.messages):
            content = msg.content
            if isinstance(content, str):
                logger.info(f"[PTC]   messages[{idx}]: role={msg.role}, content=str")
            elif isinstance(content, list):
                types = [getattr(b, "type", "?") if hasattr(b, "type") else b.get("type", "?") for b in content]
                logger.info(f"[PTC]   messages[{idx}]: role={msg.role}, content_types={types}")

        # Get PTC tools for potential continuation
        _, ptc_callable_tools = self.get_ptc_tools(original_request)

        # Resume sandbox execution
        result, is_complete = await self.resume_execution(session_id, tool_result, is_error)

        session = self.sandbox_executor.get_session(session_id)
        if not session:
            raise SandboxError(f"Session {session_id} not found")

        if not is_complete and isinstance(result, (ToolCallRequest, BatchToolCallRequest)):
            # Tool call(s) - return to client
            container_info = ContainerInfo(
                id=session_id,
                expires_at=session.expires_at.isoformat()
            )

            if isinstance(result, BatchToolCallRequest):
                # Multiple parallel tool calls
                logger.info(f"[PTC] Continuation yielded batch of {len(result)} tool calls")
                first_call = result.requests[0]
                pending_call_ids = [r.call_id for r in result.requests]

                # Update state for batch
                state.pending_batch_call_ids = pending_call_ids
                state.pending_tool_call_id = first_call.call_id
                state.pending_tool_name = first_call.tool_name
                state.pending_tool_input = first_call.arguments
                self._execution_states[session_id] = state

                # Update session's pending tool call
                session.pending_tool_call = PendingToolCall(
                    call_id=first_call.call_id,
                    tool_name=first_call.tool_name,
                    arguments=first_call.arguments,
                    session_id=session_id,
                    code_execution_tool_id=state.code_execution_tool_id
                )

                # Build minimal response with multiple tool_use blocks
                response = self._build_batch_tool_use_response_minimal(
                    result,
                    state.code_execution_tool_id,
                    container_info,
                    model=original_request.model,
                    code=state.code
                )

                return response, container_info

            else:
                # Single tool call
                # Update session's pending tool call
                session.pending_tool_call = PendingToolCall(
                    call_id=result.call_id,
                    tool_name=result.tool_name,
                    arguments=result.arguments,
                    session_id=session_id,
                    code_execution_tool_id=state.code_execution_tool_id
                )

                # Clear batch call IDs since this is single
                state.pending_batch_call_ids = None
                self._execution_states[session_id] = state

                # Build minimal response with tool_use
                response = self._build_tool_use_response_minimal(
                    result,
                    state.code_execution_tool_id,
                    container_info,
                    model=original_request.model,
                    code=state.code
                )

                return response, container_info

        elif is_complete and isinstance(result, ExecutionResult):
            # Execution complete - call Claude to get final response
            logger.info(f"[PTC] Sandbox execution completed: success={result.success}")

            container_info = ContainerInfo(
                id=session_id,
                expires_at=session.expires_at.isoformat()
            )

            # Call Claude with the code execution result to get final response
            # Pass the saved execution state to preserve original request context
            return await self._finalize_code_execution(
                result=result,
                code_execution_tool_id=state.code_execution_tool_id,
                original_request=original_request,
                bedrock_service=bedrock_service,
                request_id=request_id,
                service_tier=service_tier,
                session=session,
                ptc_callable_tools=ptc_callable_tools,
                code=state.code,
                execution_state=state,  # Pass saved state for original request context
            )

        else:
            # Unexpected state
            self._cleanup_execution_state(session_id)
            raise SandboxError(f"Unexpected result type: {type(result)}")

    def _build_code_execution_complete_response(
        self,
        result: ExecutionResult,
        code_execution_tool_id: str,
        model: str,
        code: str = ""
    ) -> MessageResponse:
        """Build response when code execution completes."""
        from app.schemas.anthropic import Usage

        # Build content with server_tool_use and server_tool_result blocks
        content = [
            # Server tool use block (code_execution)
            {
                "type": "server_tool_use",
                "id": code_execution_tool_id,
                "name": "code_execution",
                "input": {"code": code}  # Include actual code for client visibility
            },
            # Server tool result block (code execution output)
            {
                "type": "server_tool_result",
                "tool_use_id": code_execution_tool_id,
                "content": [
                    {
                        "type": "code_execution_result",
                        "stdout": result.stdout or "",
                        "stderr": result.stderr or "",
                        "return_code": 0 if result.success else 1
                    }
                ]
            }
        ]

        return MessageResponse(
            id=f"msg_{uuid4().hex}",
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=0, output_tokens=0)  # Continuation has minimal tokens
        )

    async def _finalize_code_execution(
        self,
        result: ExecutionResult,
        code_execution_tool_id: str,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict],
        code: str = "",
        execution_state: Optional[PTCExecutionState] = None,
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Finalize code execution by calling Claude with the result.

        This is called after sandbox code completes (in continuation flow).
        It sends the code output to Claude and returns Claude's final response.

        Args:
            execution_state: Optional saved state containing original request parameters.
                           When provided, uses saved system/model/etc. instead of original_request.
                           This is important for continuation requests where client may not
                           include original system message.
        """
        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        # Use saved state parameters if available, fall back to original_request
        # This ensures we preserve the original system message even in continuation requests
        effective_system = (
            execution_state.original_system if execution_state and execution_state.original_system is not None
            else original_request.system
        )
        effective_model = (
            execution_state.original_model if execution_state and execution_state.original_model
            else original_request.model
        )
        effective_max_tokens = (
            execution_state.original_max_tokens if execution_state and execution_state.original_max_tokens
            else original_request.max_tokens
        )
        effective_temperature = (
            execution_state.original_temperature if execution_state and execution_state.original_temperature is not None
            else original_request.temperature
        )
        effective_top_p = (
            execution_state.original_top_p if execution_state and execution_state.original_top_p is not None
            else original_request.top_p
        )
        effective_top_k = (
            execution_state.original_top_k if execution_state and execution_state.original_top_k is not None
            else original_request.top_k
        )
        effective_stop_sequences = (
            execution_state.original_stop_sequences if execution_state and execution_state.original_stop_sequences
            else original_request.stop_sequences
        )
        effective_tool_choice = (
            execution_state.original_tool_choice if execution_state and execution_state.original_tool_choice
            else original_request.tool_choice
        )
        effective_thinking = (
            execution_state.original_thinking if execution_state and execution_state.original_thinking
            else original_request.thinking
        )
        effective_anthropic_beta = (
            execution_state.original_anthropic_beta if execution_state and execution_state.original_anthropic_beta
            else None
        )

        has_system = effective_system is not None
        logger.info(f"[PTC] Finalizing code execution, sending result to Claude")
        logger.info(f"[PTC] Effective parameters - Has system: {has_system}, Model: {effective_model}, Beta: {effective_anthropic_beta}")

        # Build continuation messages
        # The original_request.messages contains the conversation history echoed by the client
        # When thinking is enabled, the client's echoed assistant message is incomplete (missing thinking blocks)
        # We need to rebuild the conversation using only user messages from the client + our stored assistant content

        # Add assistant message with execute_code tool call
        # When thinking is enabled, Claude requires assistant messages to start with thinking blocks
        # Use the stored original_assistant_content which includes thinking blocks
        if execution_state and execution_state.original_assistant_content:
            # When we have stored assistant content (with thinking blocks), we MUST rebuild messages:
            # The client's echoed messages contain:
            #   - Original conversation history (user/assistant turns) - KEEP these
            #   - The incomplete assistant message we sent back (missing thinking) - SKIP this (last assistant)
            #   - User message with tool_result for internal tool - SKIP this (not for Claude)
            #
            # We rebuild by:
            # 1. Keep all messages except the LAST assistant message and user messages with tool_result
            # 2. Append our stored original_assistant_content (has thinking blocks)
            # 3. Append tool_result for execute_code (code output)
            #
            # This avoids having an incomplete assistant message (without thinking) in the history.

            messages = []
            msg_list = list(original_request.messages)

            logger.info(f"[PTC] Input messages count: {len(msg_list)}")

            # Find the index of the last assistant message (which is the incomplete one we sent)
            last_assistant_idx = -1
            for i in range(len(msg_list) - 1, -1, -1):
                msg = msg_list[i]
                if isinstance(msg, dict):
                    role = msg.get("role")
                elif hasattr(msg, "role"):
                    role = msg.role
                else:
                    continue
                if role == "assistant":
                    last_assistant_idx = i
                    break

            logger.info(f"[PTC] Last assistant message index: {last_assistant_idx}")

            for i, msg in enumerate(msg_list):
                if isinstance(msg, dict):
                    role = msg.get("role")
                    content = msg.get("content", [])
                elif hasattr(msg, "role"):
                    role = msg.role
                    content = msg.content if hasattr(msg, "content") else []
                else:
                    continue

                # Log each message for debugging
                content_types = []
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict):
                            content_types.append(b.get("type", "unknown"))
                        elif hasattr(b, "type"):
                            content_types.append(b.type)
                logger.info(f"[PTC] Input msg[{i}]: role={role}, content_types={content_types}")

                # Skip the LAST assistant message (it's incomplete, missing thinking blocks)
                # Previous assistant messages from earlier turns are valid and should be kept
                if role == "assistant" and i == last_assistant_idx:
                    logger.info(f"[PTC] Skipping msg[{i}] (last assistant)")
                    continue

                # Skip user messages containing tool_result (those are for internal tools)
                if role == "user" and isinstance(content, list):
                    has_tool_result = any(
                        (isinstance(b, dict) and b.get("type") == "tool_result") or
                        (hasattr(b, "type") and b.type == "tool_result")
                        for b in content
                    )
                    if has_tool_result:
                        logger.info(f"[PTC] Skipping msg[{i}] (user with tool_result)")
                        continue

                msg_dict = msg if isinstance(msg, dict) else msg.model_dump()

                # Filter assistant message content blocks for Bedrock compatibility
                # Earlier assistant messages may contain server_tool_use blocks from previous code execution rounds
                if role == "assistant" and isinstance(msg_dict.get("content"), list):
                    msg_dict = dict(msg_dict)  # Make a copy to avoid mutating original
                    original_types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in msg_dict["content"]]
                    msg_dict["content"] = _filter_content_blocks_for_bedrock(msg_dict["content"])
                    filtered_types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in msg_dict["content"]]
                    logger.info(f"[PTC] Filtered msg[{i}] assistant content: {original_types} -> {filtered_types}")

                    # Skip messages that end up with empty content after filtering
                    # Bedrock rejects messages with empty content
                    if not msg_dict["content"]:
                        logger.info(f"[PTC] Skipping msg[{i}] (empty content after filtering)")
                        continue

                messages.append(msg_dict)
                logger.info(f"[PTC] Kept msg[{i}] as messages[{len(messages)-1}]")

            logger.info(f"[PTC] Kept {len(messages)} messages total")

            # Append our stored assistant content (which includes thinking blocks)
            # Filter out server_tool_use/server_tool_result blocks - they're not valid for Bedrock
            original_content_types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in execution_state.original_assistant_content]
            filtered_assistant_content = _filter_content_blocks_for_bedrock(
                execution_state.original_assistant_content
            )
            filtered_content_types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in filtered_assistant_content]
            messages.append({
                "role": "assistant",
                "content": filtered_assistant_content
            })
            logger.info(f"[PTC] Appended stored assistant content as messages[{len(messages)-1}]: {original_content_types} -> {filtered_content_types}")
            # Use the original execute_code ID for the tool_result
            execute_code_id = execution_state.original_execute_code_id or f"toolu_{code_execution_tool_id[-12:]}"
        else:
            # Fallback: no stored assistant content, use client's messages directly
            # This path is used when thinking is NOT enabled
            messages = _filter_non_direct_tool_calls(list(original_request.messages))
            execute_code_id = f"toolu_{code_execution_tool_id[-12:]}"
            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": execute_code_id,
                    "name": "execute_code",
                    "input": {"code": code}
                }]
            })

        # Add tool result for the execute_code call
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": execute_code_id,
                "content": tool_result_content
            }]
        })
        logger.info(f"[PTC] Appended tool_result as messages[{len(messages)-1}]")

        # Log final messages summary
        logger.info(f"[PTC] Final messages array ({len(messages)} messages):")
        for idx, msg in enumerate(messages):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "?")
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", [])
            if isinstance(content, list):
                types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in content]
                logger.info(f"[PTC]   messages[{idx}]: role={role}, content_types={types}")
            else:
                logger.info(f"[PTC]   messages[{idx}]: role={role}, content=str")

        # Create continuation request using effective (preserved) parameters
        continuation_request = MessageRequest(
            model=effective_model,
            messages=messages,
            max_tokens=effective_max_tokens,
            system=effective_system,
            temperature=effective_temperature,
            top_p=effective_top_p,
            top_k=effective_top_k,
            stop_sequences=effective_stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=effective_tool_choice,
            thinking=effective_thinking,
        )

        # Debug: Verify MessageRequest didn't reorder content after Pydantic validation
        logger.info(f"[PTC] After MessageRequest creation, checking messages:")
        for idx, msg in enumerate(continuation_request.messages):
            content = msg.content
            if isinstance(content, list):
                types = [getattr(b, "type", "?") if hasattr(b, "type") else b.get("type", "?") for b in content]
                logger.info(f"[PTC]   continuation_request.messages[{idx}]: role={msg.role}, content_types={types}")
                # Extra detail for messages[1] if it's assistant
                if idx == 1 and msg.role == "assistant":
                    logger.info(f"[PTC]   DETAIL messages[1].content:")
                    for i, block in enumerate(content):
                        block_type = getattr(block, "type", "?") if hasattr(block, "type") else block.get("type", "?")
                        logger.info(f"[PTC]     [{i}] type={block_type}, block={block}")

        # Call Bedrock to get Claude's final response (with preserved beta header)
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier, effective_anthropic_beta
        )

        # Check if Claude called execute_code again
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive call for multi-round code execution
            # Build request with effective (preserved) parameters
            # Use prepare_bedrock_request to filter out code_execution_20250825 tool type
            recursive_request = MessageRequest(
                model=effective_model,
                messages=messages,
                max_tokens=effective_max_tokens,
                system=effective_system,
                temperature=effective_temperature,
                top_p=effective_top_p,
                top_k=effective_top_k,
                stop_sequences=effective_stop_sequences,
                tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
                tool_choice=effective_tool_choice,
                thinking=effective_thinking,
            )
            return await self._handle_code_execution(
                next_execute_code,
                final_response,
                session,
                recursive_request,
                bedrock_service,
                request_id,
                service_tier,
                ptc_callable_tools,
                effective_anthropic_beta,  # Pass preserved beta header
            )

        # Add caller: {type: "direct"} to any direct tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        container_info = ContainerInfo(
            id=session.session_id,
            expires_at=session.expires_at.isoformat()
        )

        return final_response, container_info

    def _build_tool_use_response_minimal(
        self,
        tool_request: ToolCallRequest,
        code_execution_tool_id: str,
        _container_info: ContainerInfo,  # Unused, kept for future use
        model: str = "claude-3-sonnet",
        code: str = ""  # Unused in minimal response
    ) -> MessageResponse:
        """Build minimal response with tool_use for continuation.

        NOTE: This does NOT include server_tool_use because it's a continuation.
        The server_tool_use was already sent in the initial response.
        Continuation responses only include new tool_use blocks.
        """
        from app.schemas.anthropic import Usage

        # Only include tool_use block - server_tool_use was already sent in initial response
        content = [
            {
                "type": "tool_use",
                "id": f"toolu_{uuid4().hex[:12]}",
                "name": tool_request.tool_name,
                "input": tool_request.arguments,
                "caller": {
                    "type": PTC_ALLOWED_CALLER,
                    "tool_id": code_execution_tool_id
                }
            }
        ]

        return MessageResponse(
            id=f"msg_{uuid4().hex}",
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason="tool_use",
            stop_sequence=None,
            usage=Usage(input_tokens=0, output_tokens=0)  # Continuation has no new tokens
        )

    def _build_batch_tool_use_response_minimal(
        self,
        batch_request: BatchToolCallRequest,
        code_execution_tool_id: str,
        _container_info: ContainerInfo,  # Unused, kept for future use
        model: str = "claude-3-sonnet",
        code: str = ""  # Unused in minimal response
    ) -> MessageResponse:
        """Build minimal response with multiple tool_use blocks for batch continuation.

        NOTE: This does NOT include server_tool_use because it's a continuation.
        The server_tool_use was already sent in the initial response.
        Continuation responses only include new tool_use blocks.
        """
        from app.schemas.anthropic import Usage

        # Only include tool_use blocks - server_tool_use was already sent in initial response
        content = []

        # Add tool use block for EACH tool call in the batch
        for tool_request in batch_request.requests:
            content.append({
                "type": "tool_use",
                "id": f"toolu_{tool_request.call_id[:12]}",  # Use call_id for tracking
                "name": tool_request.tool_name,
                "input": tool_request.arguments,
                "caller": {
                    "type": PTC_ALLOWED_CALLER,
                    "tool_id": code_execution_tool_id
                }
            })

        logger.info(f"[PTC] Built batch minimal response with {len(batch_request)} tool calls (continuation, no server_tool_use)")

        return MessageResponse(
            id=f"msg_{uuid4().hex}",
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason="tool_use",
            stop_sequence=None,
            usage=Usage(input_tokens=0, output_tokens=0)  # Continuation has no new tokens
        )

    async def _continue_after_code_execution(
        self,
        result: ExecutionResult,
        code_execution_tool_id: str,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict]
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """Continue conversation with Claude after code execution completes."""
        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        # Build continuation messages
        # Filter out non-direct tool calls and their results from history
        messages = _filter_non_direct_tool_calls(list(original_request.messages))

        # Add tool result for the server_tool_use (code_execution)
        # Find the last assistant message with server_tool_use and add result
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": code_execution_tool_id,
                "content": tool_result_content
            }]
        })

        # Create continuation request
        continuation_request = MessageRequest(
            model=original_request.model,
            messages=messages,
            max_tokens=original_request.max_tokens,
            system=original_request.system,
            temperature=original_request.temperature,
            top_p=original_request.top_p,
            top_k=original_request.top_k,
            stop_sequences=original_request.stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=original_request.tool_choice,
            thinking=original_request.thinking,
        )

        # Call Bedrock again
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier
        )

        # Check if Claude called execute_code again
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive call for multi-round code execution
            # Build request with filtered tools (code_execution_20250825 removed)
            recursive_request_dict = original_request.model_dump()
            recursive_request_dict["messages"] = messages
            recursive_request_dict["tools"] = self.prepare_bedrock_request(original_request, ptc_callable_tools).tools
            return await self._handle_code_execution(
                next_execute_code,
                final_response,
                session,
                MessageRequest(**recursive_request_dict),
                bedrock_service,
                request_id,
                service_tier,
                ptc_callable_tools
            )

        # Add caller: {type: "direct"} to any direct tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        container_info = ContainerInfo(
            id=session.session_id,
            expires_at=session.expires_at.isoformat()
        )

        return final_response, container_info

    def _cleanup_execution_state(self, session_id: str) -> None:
        """Clean up execution state."""
        self._execution_states.pop(session_id, None)
        self._execution_generators.pop(session_id, None)
        # Also clear session's pending_tool_call if session exists
        session = self.sandbox_executor.get_session(session_id)
        if session:
            session.pending_tool_call = None
            session.is_busy = False

    def _build_tool_use_response(
        self,
        tool_request: ToolCallRequest,
        code_execution_tool_id: str,
        original_response: MessageResponse,
        _container_info: ContainerInfo,  # Unused, kept for future use
        code: str = ""
    ) -> MessageResponse:
        """Build response with tool_use block including caller info."""
        # Create new content with tool_use
        # IMPORTANT: Thinking blocks must come first for Bedrock compatibility
        thinking_blocks = []
        other_blocks = []

        # Separate thinking blocks from other content (text only)
        for block in original_response.content:
            if hasattr(block, "type"):
                block_type = block.type
                if block_type in ("thinking", "redacted_thinking"):
                    # Include thinking blocks for client to echo back correctly
                    if block_type == "thinking":
                        thinking_blocks.append({
                            "type": "thinking",
                            "thinking": block.thinking if hasattr(block, "thinking") else "",
                            "signature": block.signature if hasattr(block, "signature") else None
                        })
                    else:
                        thinking_blocks.append({
                            "type": "redacted_thinking",
                            "data": block.data if hasattr(block, "data") else ""
                        })
                elif block_type == "text":
                    other_blocks.append({
                        "type": "text",
                        "text": block.text if hasattr(block, "text") else ""
                    })
            elif isinstance(block, dict):
                block_type = block.get("type")
                if block_type in ("thinking", "redacted_thinking"):
                    thinking_blocks.append(block)
                elif block_type == "text":
                    other_blocks.append(block)

        # Combine: thinking first, then text
        content = thinking_blocks + other_blocks

        # Add server_tool_use for code_execution
        content.append({
            "type": "server_tool_use",
            "id": code_execution_tool_id,
            "name": "code_execution",
            "input": {"code": code}  # Include actual code for client visibility
        })

        # Add tool_use with caller info
        content.append({
            "type": "tool_use",
            "id": f"toolu_{uuid4().hex[:12]}",
            "name": tool_request.tool_name,
            "input": tool_request.arguments,
            "caller": {
                "type": PTC_ALLOWED_CALLER,
                "tool_id": code_execution_tool_id
            }
        })

        # Build response
        response_dict = {
            "id": original_response.id,
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": original_response.model,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": original_response.usage.model_dump() if hasattr(original_response.usage, "model_dump") else original_response.usage,
        }

        content_types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in content]
        logger.info(f"[PTC] Built tool_use response: {len(thinking_blocks)} thinking blocks first, content_types={content_types}")
        return MessageResponse(**response_dict)

    def _build_batch_tool_use_response(
        self,
        batch_request: BatchToolCallRequest,
        code_execution_tool_id: str,
        original_response: MessageResponse,
        _container_info: ContainerInfo,  # Unused, kept for future use
        code: str = ""
    ) -> MessageResponse:
        """Build response with multiple tool_use blocks for parallel tool calls."""
        # Create new content
        # IMPORTANT: Thinking blocks must come first for Bedrock compatibility
        thinking_blocks = []
        other_blocks = []

        # Separate thinking blocks from other content (text only)
        for block in original_response.content:
            if hasattr(block, "type"):
                block_type = block.type
                if block_type in ("thinking", "redacted_thinking"):
                    # Include thinking blocks for client to echo back correctly
                    if block_type == "thinking":
                        thinking_blocks.append({
                            "type": "thinking",
                            "thinking": block.thinking if hasattr(block, "thinking") else "",
                            "signature": block.signature if hasattr(block, "signature") else None
                        })
                    else:
                        thinking_blocks.append({
                            "type": "redacted_thinking",
                            "data": block.data if hasattr(block, "data") else ""
                        })
                elif block_type == "text":
                    other_blocks.append({
                        "type": "text",
                        "text": block.text if hasattr(block, "text") else ""
                    })
            elif isinstance(block, dict):
                block_type = block.get("type")
                if block_type in ("thinking", "redacted_thinking"):
                    thinking_blocks.append(block)
                elif block_type == "text":
                    other_blocks.append(block)

        # Combine: thinking first, then text
        content = thinking_blocks + other_blocks

        # Add server_tool_use for code_execution
        content.append({
            "type": "server_tool_use",
            "id": code_execution_tool_id,
            "name": "code_execution",
            "input": {"code": code}  # Include actual code for client visibility
        })

        # Add tool_use block for EACH tool call in the batch
        for tool_request in batch_request.requests:
            content.append({
                "type": "tool_use",
                "id": f"toolu_{tool_request.call_id[:12]}",  # Use call_id for tracking
                "name": tool_request.tool_name,
                "input": tool_request.arguments,
                "caller": {
                    "type": PTC_ALLOWED_CALLER,
                    "tool_id": code_execution_tool_id
                }
            })

        # Build response
        response_dict = {
            "id": original_response.id,
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": original_response.model,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": original_response.usage.model_dump() if hasattr(original_response.usage, "model_dump") else original_response.usage,
        }

        content_types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in content]
        logger.info(f"[PTC] Built batch tool_use response: {len(thinking_blocks)} thinking blocks first, {len(batch_request)} tool calls, content_types={content_types}")
        return MessageResponse(**response_dict)

    async def _complete_code_execution(
        self,
        result: ExecutionResult,
        execute_code_call: dict,
        claude_response: MessageResponse,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict],
        anthropic_beta: Optional[str] = None,
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Complete code execution and continue conversation with Claude.

        After code execution completes, send the result back to Claude
        as a tool_result and get the final response.
        """
        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        # Build continuation messages
        # Include original assistant response and tool result
        # Filter out non-direct tool calls and their results from history
        messages = _filter_non_direct_tool_calls(list(original_request.messages))

        # Add assistant message with execute_code call
        # Filter out server_tool_use/server_tool_result blocks - they're not valid for Bedrock
        assistant_content = []
        for block in claude_response.content:
            if hasattr(block, "model_dump"):
                assistant_content.append(block.model_dump())
            elif isinstance(block, dict):
                assistant_content.append(block)

        filtered_assistant_content = _filter_content_blocks_for_bedrock(assistant_content)
        logger.info(f"[PTC _complete] Filtered assistant content: {[b.get('type') for b in assistant_content]} -> {[b.get('type') for b in filtered_assistant_content]}")
        messages.append({
            "role": "assistant",
            "content": filtered_assistant_content
        })

        # Add tool result
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": execute_code_call["id"],
                "content": tool_result_content
            }]
        })

        # Debug: Log final messages before creating request
        logger.info(f"[PTC _complete] Final messages ({len(messages)}):")
        for idx, msg in enumerate(messages):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "?")
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", [])
            if isinstance(content, list):
                types = [b.get("type") if isinstance(b, dict) else getattr(b, "type", "?") for b in content]
                logger.info(f"[PTC _complete]   messages[{idx}]: role={role}, content_types={types}")

        # Create continuation request
        continuation_request = MessageRequest(
            model=original_request.model,
            messages=messages,
            max_tokens=original_request.max_tokens,
            system=original_request.system,
            temperature=original_request.temperature,
            top_p=original_request.top_p,
            top_k=original_request.top_k,
            stop_sequences=original_request.stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=original_request.tool_choice,
            thinking=original_request.thinking,
        )

        # Call Bedrock again (with beta header)
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier, anthropic_beta
        )

        # Check if Claude called execute_code again
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive call for multi-round code execution
            # Build request with filtered tools (code_execution_20250825 removed)
            recursive_request_dict = original_request.model_dump()
            recursive_request_dict["messages"] = messages
            recursive_request_dict["tools"] = self.prepare_bedrock_request(original_request, ptc_callable_tools).tools
            return await self._handle_code_execution(
                next_execute_code,
                final_response,
                session,
                MessageRequest(**recursive_request_dict),
                bedrock_service,
                request_id,
                service_tier,
                ptc_callable_tools,
                anthropic_beta,  # Pass beta header
            )

        # Add caller: {type: "direct"} to any direct tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        container_info = ContainerInfo(
            id=session.session_id,
            expires_at=session.expires_at.isoformat()
        )

        return final_response, container_info

    def _add_direct_caller_to_tool_use(self, response: MessageResponse) -> MessageResponse:
        """
        Add caller: {type: "direct"} to any tool_use blocks without a caller.

        When PTC is enabled, all tool_use blocks should have a caller field.
        Direct tool calls (not from code execution) get caller.type = "direct".
        """
        new_content = []
        modified = False

        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                # Check if already has caller
                if not hasattr(block, "caller") or block.caller is None:
                    # Convert to dict, add caller, and append
                    block_dict = block.model_dump() if hasattr(block, "model_dump") else dict(block)
                    block_dict["caller"] = {"type": "direct"}
                    new_content.append(block_dict)
                    modified = True
                else:
                    new_content.append(block.model_dump() if hasattr(block, "model_dump") else block)
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                if "caller" not in block or block.get("caller") is None:
                    block_copy = dict(block)
                    block_copy["caller"] = {"type": "direct"}
                    new_content.append(block_copy)
                    modified = True
                else:
                    new_content.append(block)
            else:
                # Keep other content blocks as-is
                if hasattr(block, "model_dump"):
                    new_content.append(block.model_dump())
                else:
                    new_content.append(block)

        if modified:
            response_dict = response.model_dump()
            response_dict["content"] = new_content
            return MessageResponse(**response_dict)

        return response

    def get_pending_execution(self, session_id: str) -> Optional[PTCExecutionState]:
        """Get pending execution state for a session."""
        return self._execution_states.get(session_id)

    # ========== Hybrid Streaming Support ==========

    def _format_sse_event(self, event: Dict[str, Any]) -> str:
        """Format an event dict as an SSE string."""
        event_type = event.get("type", "unknown")
        return f"event: {event_type}\ndata: {json.dumps(event)}\n\n"

    def _emit_message_start(
        self, message_id: str, model: str, input_tokens: int,
        container_info: Optional[ContainerInfo] = None
    ) -> str:
        """Generate message_start SSE event."""
        message = {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 0,
            },
        }

        # Add container info inside message if available
        if container_info:
            message["container"] = {
                "id": container_info.id,
                "expires_at": container_info.expires_at,
            }

        event_data = {
            "type": "message_start",
            "message": message,
        }

        return self._format_sse_event(event_data)

    def _emit_content_block_events(
        self, content: List[Any], start_index: int
    ) -> Tuple[List[str], int]:
        """Generate SSE events for content blocks."""
        events = []
        current_index = start_index

        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, 'model_dump') else {}
            )

            block_type = block_dict.get("type", "")

            if block_type == "text":
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": current_index,
                    "content_block": {"type": "text", "text": ""},
                }))
                text = block_dict.get("text", "")
                if text:
                    events.append(self._format_sse_event({
                        "type": "content_block_delta",
                        "index": current_index,
                        "delta": {"type": "text_delta", "text": text},
                    }))

            elif block_type == "server_tool_use":
                # Include input in content_block_start for server_tool_use
                content_block = {
                    "type": "server_tool_use",
                    "id": block_dict.get("id", ""),
                    "name": block_dict.get("name", ""),
                }
                tool_input = block_dict.get("input", {})
                if tool_input:
                    content_block["input"] = tool_input

                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": current_index,
                    "content_block": content_block,
                }))

            elif block_type == "tool_use":
                # Build content_block with caller and input
                content_block = {
                    "type": "tool_use",
                    "id": block_dict.get("id", ""),
                    "name": block_dict.get("name", ""),
                }
                tool_input = block_dict.get("input", {})
                if tool_input:
                    content_block["input"] = tool_input
                caller = block_dict.get("caller")
                if caller:
                    content_block["caller"] = caller

                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": current_index,
                    "content_block": content_block,
                }))

            elif block_type in ("thinking", "redacted_thinking"):
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": current_index,
                    "content_block": {"type": block_type, "thinking": "" if block_type == "thinking" else None},
                }))
                if block_type == "thinking":
                    thinking_text = block_dict.get("thinking", "")
                    if thinking_text:
                        events.append(self._format_sse_event({
                            "type": "content_block_delta",
                            "index": current_index,
                            "delta": {"type": "thinking_delta", "thinking": thinking_text},
                        }))

            else:
                # Handle other block types generically
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": current_index,
                    "content_block": block_dict,
                }))

            events.append(self._format_sse_event({
                "type": "content_block_stop",
                "index": current_index,
            }))

            current_index += 1

        return events, current_index

    def _emit_message_end(
        self, stop_reason: str, output_tokens: int
    ) -> List[str]:
        """Generate message_delta and message_stop events."""
        return [
            self._format_sse_event({
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                },
                "usage": {
                    "output_tokens": output_tokens,
                },
            }),
            self._format_sse_event({
                "type": "message_stop",
            }),
        ]

    async def handle_ptc_request_streaming(
        self,
        request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        container_id: Optional[str] = None,
        anthropic_beta: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Handle PTC request with hybrid streaming.

        Uses NON-STREAMING Bedrock API internally, but emits SSE events to the client.
        When sandbox needs external tool call, emits events with stop_reason="tool_use"
        and returns - client will make a new request with tool_result.

        Yields:
            SSE-formatted event strings
        """
        logger.info(f"[PTC Streaming] Handling request {request_id}")

        # Check Docker availability
        if not self.is_docker_available():
            yield self._format_sse_event({
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "Programmatic Tool Calling requires Docker which is not available.",
                }
            })
            return

        message_id = f"msg_{uuid4().hex[:24]}"
        global_index = 0
        total_input_tokens = 0
        total_output_tokens = 0

        # Get PTC tools
        _, ptc_callable_tools = self.get_ptc_tools(request)

        # Prepare request for Bedrock
        bedrock_request = self.prepare_bedrock_request(request, ptc_callable_tools)

        try:
            # Get or create sandbox session
            session = await self._get_or_create_session(container_id, ptc_callable_tools)
            logger.info(f"[PTC Streaming] Using session {session.session_id}")

            # Call Bedrock (non-streaming)
            response = await bedrock_service.invoke_model(
                bedrock_request, request_id, service_tier, anthropic_beta
            )

            # Track tokens
            if response.usage:
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

            # Build container info
            container_info = ContainerInfo(
                id=session.session_id,
                expires_at=session.expires_at.isoformat()
            )

            # Emit message_start with container info
            yield self._emit_message_start(message_id, request.model, total_input_tokens, container_info)

            # Check if Claude called execute_code
            execute_code_call = self._find_execute_code_call(response)

            if not execute_code_call:
                # No code execution - emit response and finish
                response = self._add_direct_caller_to_tool_use(response)
                content_list = []
                for block in response.content:
                    if hasattr(block, 'model_dump'):
                        content_list.append(block.model_dump())
                    else:
                        content_list.append(block)

                events, global_index = self._emit_content_block_events(content_list, global_index)
                for event in events:
                    yield event

                stop_reason = response.stop_reason or "end_turn"
                for event in self._emit_message_end(stop_reason, total_output_tokens):
                    yield event
                return

            # Execute code in sandbox
            code = execute_code_call.get("input", {}).get("code", "")
            code_execution_tool_id = f"srvtoolu_{uuid4().hex[:12]}"
            original_execute_code_id = execute_code_call.get("id")

            # Store original assistant content for continuation
            original_assistant_content = []
            for block in response.content:
                if hasattr(block, "model_dump"):
                    original_assistant_content.append(block.model_dump())
                elif isinstance(block, dict):
                    original_assistant_content.append(block)

            logger.info(f"[PTC Streaming] Executing code in sandbox")

            # Execute code
            gen = self.sandbox_executor.execute_code(code, session)

            try:
                result = await gen.__anext__()

                if isinstance(result, (ToolCallRequest, BatchToolCallRequest)):
                    # Tool call(s) requested - emit events and return for client to execute
                    container_info = ContainerInfo(
                        id=session.session_id,
                        expires_at=session.expires_at.isoformat()
                    )

                    # Build content for response
                    content_blocks = []

                    # Add text from original response (thinking blocks first)
                    thinking_blocks = []
                    text_blocks = []
                    for block in response.content:
                        if hasattr(block, "type"):
                            if block.type in ("thinking", "redacted_thinking"):
                                thinking_blocks.append(block.model_dump() if hasattr(block, "model_dump") else block)
                            elif block.type == "text":
                                text_blocks.append({"type": "text", "text": block.text if hasattr(block, "text") else ""})
                    content_blocks.extend(thinking_blocks)
                    content_blocks.extend(text_blocks)

                    # Add server_tool_use for code_execution
                    content_blocks.append({
                        "type": "server_tool_use",
                        "id": code_execution_tool_id,
                        "name": "code_execution",
                        "input": {"code": code}
                    })

                    # Add tool_use block(s) for client execution
                    if isinstance(result, BatchToolCallRequest):
                        pending_call_ids = [r.call_id for r in result.requests]
                        first_call = result.requests[0]

                        for tool_request in result.requests:
                            content_blocks.append({
                                "type": "tool_use",
                                "id": f"toolu_{tool_request.call_id[:12]}",
                                "name": tool_request.tool_name,
                                "input": tool_request.arguments,
                                "caller": {
                                    "type": PTC_ALLOWED_CALLER,
                                    "tool_id": code_execution_tool_id
                                }
                            })

                        # Store state for continuation
                        state = PTCExecutionState(
                            session_id=session.session_id,
                            code_execution_tool_id=code_execution_tool_id,
                            code=code,
                            pending_tool_call_id=first_call.call_id,
                            pending_tool_name=first_call.tool_name,
                            pending_tool_input=first_call.arguments,
                            pending_batch_call_ids=pending_call_ids,
                            original_system=bedrock_request.system,
                            original_model=bedrock_request.model,
                            original_max_tokens=bedrock_request.max_tokens,
                            original_temperature=bedrock_request.temperature,
                            original_top_p=bedrock_request.top_p,
                            original_top_k=bedrock_request.top_k,
                            original_stop_sequences=bedrock_request.stop_sequences,
                            original_tool_choice=bedrock_request.tool_choice,
                            original_thinking=bedrock_request.thinking,
                            original_anthropic_beta=anthropic_beta,
                            original_assistant_content=original_assistant_content,
                            original_execute_code_id=original_execute_code_id,
                        )
                        self._execution_states[session.session_id] = state
                        self._execution_generators[session.session_id] = gen

                        session.pending_tool_call = PendingToolCall(
                            call_id=first_call.call_id,
                            tool_name=first_call.tool_name,
                            arguments=first_call.arguments,
                            session_id=session.session_id,
                            code_execution_tool_id=code_execution_tool_id
                        )
                    else:
                        # Single tool call
                        content_blocks.append({
                            "type": "tool_use",
                            "id": f"toolu_{uuid4().hex[:12]}",
                            "name": result.tool_name,
                            "input": result.arguments,
                            "caller": {
                                "type": PTC_ALLOWED_CALLER,
                                "tool_id": code_execution_tool_id
                            }
                        })

                        # Store state for continuation
                        state = PTCExecutionState(
                            session_id=session.session_id,
                            code_execution_tool_id=code_execution_tool_id,
                            code=code,
                            pending_tool_call_id=result.call_id,
                            pending_tool_name=result.tool_name,
                            pending_tool_input=result.arguments,
                            original_system=bedrock_request.system,
                            original_model=bedrock_request.model,
                            original_max_tokens=bedrock_request.max_tokens,
                            original_temperature=bedrock_request.temperature,
                            original_top_p=bedrock_request.top_p,
                            original_top_k=bedrock_request.top_k,
                            original_stop_sequences=bedrock_request.stop_sequences,
                            original_tool_choice=bedrock_request.tool_choice,
                            original_thinking=bedrock_request.thinking,
                            original_anthropic_beta=anthropic_beta,
                            original_assistant_content=original_assistant_content,
                            original_execute_code_id=original_execute_code_id,
                        )
                        self._execution_states[session.session_id] = state
                        self._execution_generators[session.session_id] = gen

                        session.pending_tool_call = PendingToolCall(
                            call_id=result.call_id,
                            tool_name=result.tool_name,
                            arguments=result.arguments,
                            session_id=session.session_id,
                            code_execution_tool_id=code_execution_tool_id
                        )

                    # Emit content block events
                    events, global_index = self._emit_content_block_events(content_blocks, global_index)
                    for event in events:
                        yield event

                    # Emit message end with stop_reason="tool_use"
                    for event in self._emit_message_end("tool_use", total_output_tokens):
                        yield event
                    return

                elif isinstance(result, ExecutionResult):
                    # Code completed without needing external tools
                    await gen.aclose()
                    session.is_busy = False

                    # Call Claude with code result
                    async for event in self._complete_code_execution_streaming(
                        result=result,
                        execute_code_call=execute_code_call,
                        claude_response=response,
                        original_request=bedrock_request,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                        session=session,
                        ptc_callable_tools=ptc_callable_tools,
                        anthropic_beta=anthropic_beta,
                        message_id=message_id,
                        start_index=global_index,
                        initial_input_tokens=total_input_tokens,
                        initial_output_tokens=total_output_tokens,
                    ):
                        yield event
                    return

            except StopAsyncIteration:
                logger.warning("[PTC Streaming] Sandbox generator completed unexpectedly")
                yield self._format_sse_event({
                    "type": "error",
                    "error": {"type": "api_error", "message": "Code execution completed unexpectedly"}
                })
                return

        except Exception as e:
            logger.error(f"[PTC Streaming] Error: {e}")
            yield self._format_sse_event({
                "type": "error",
                "error": {"type": "api_error", "message": str(e)}
            })
            return

    async def handle_tool_result_continuation_streaming(
        self,
        session_id: str,
        tool_result: Any,
        is_error: bool,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
    ) -> AsyncGenerator[str, None]:
        """
        Handle tool_result continuation with hybrid streaming.

        Resumes sandbox execution and emits SSE events.

        Yields:
            SSE-formatted event strings
        """
        state = self._execution_states.get(session_id)
        if not state:
            yield self._format_sse_event({
                "type": "error",
                "error": {"type": "api_error", "message": f"No pending execution for session {session_id}"}
            })
            return

        logger.info(f"[PTC Streaming] Resuming execution for session {session_id}")

        message_id = f"msg_{uuid4().hex[:24]}"
        global_index = 0
        total_output_tokens = 0

        # Get PTC tools
        _, ptc_callable_tools = self.get_ptc_tools(original_request)

        try:
            # Resume sandbox execution
            result, is_complete = await self.resume_execution(session_id, tool_result, is_error)

            session = self.sandbox_executor.get_session(session_id)
            if not session:
                yield self._format_sse_event({
                    "type": "error",
                    "error": {"type": "api_error", "message": f"Session {session_id} not found"}
                })
                return

            # Build container info
            container_info = ContainerInfo(
                id=session.session_id,
                expires_at=session.expires_at.isoformat()
            )

            # Emit message_start with container info
            yield self._emit_message_start(message_id, original_request.model, 0, container_info)

            if not is_complete and isinstance(result, (ToolCallRequest, BatchToolCallRequest)):
                # Another tool call - emit events and return
                content_blocks = []

                if isinstance(result, BatchToolCallRequest):
                    pending_call_ids = [r.call_id for r in result.requests]
                    first_call = result.requests[0]

                    for tool_request in result.requests:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": f"toolu_{tool_request.call_id[:12]}",
                            "name": tool_request.tool_name,
                            "input": tool_request.arguments,
                            "caller": {
                                "type": PTC_ALLOWED_CALLER,
                                "tool_id": state.code_execution_tool_id
                            }
                        })

                    # Update state
                    state.pending_batch_call_ids = pending_call_ids
                    state.pending_tool_call_id = first_call.call_id
                    state.pending_tool_name = first_call.tool_name
                    state.pending_tool_input = first_call.arguments
                    self._execution_states[session_id] = state

                    session.pending_tool_call = PendingToolCall(
                        call_id=first_call.call_id,
                        tool_name=first_call.tool_name,
                        arguments=first_call.arguments,
                        session_id=session_id,
                        code_execution_tool_id=state.code_execution_tool_id
                    )
                else:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": f"toolu_{uuid4().hex[:12]}",
                        "name": result.tool_name,
                        "input": result.arguments,
                        "caller": {
                            "type": PTC_ALLOWED_CALLER,
                            "tool_id": state.code_execution_tool_id
                        }
                    })

                    state.pending_batch_call_ids = None
                    self._execution_states[session_id] = state

                    session.pending_tool_call = PendingToolCall(
                        call_id=result.call_id,
                        tool_name=result.tool_name,
                        arguments=result.arguments,
                        session_id=session_id,
                        code_execution_tool_id=state.code_execution_tool_id
                    )

                events, global_index = self._emit_content_block_events(content_blocks, global_index)
                for event in events:
                    yield event

                for event in self._emit_message_end("tool_use", 0):
                    yield event
                return

            elif is_complete and isinstance(result, ExecutionResult):
                # Code execution complete - call Claude for final response
                logger.info(f"[PTC Streaming] Sandbox execution completed: success={result.success}")

                async for event in self._finalize_code_execution_streaming(
                    result=result,
                    code_execution_tool_id=state.code_execution_tool_id,
                    original_request=original_request,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                    session=session,
                    ptc_callable_tools=ptc_callable_tools,
                    code=state.code,
                    execution_state=state,
                    message_id=message_id,
                    start_index=global_index,
                ):
                    yield event
                return

            else:
                self._cleanup_execution_state(session_id)
                yield self._format_sse_event({
                    "type": "error",
                    "error": {"type": "api_error", "message": f"Unexpected result type: {type(result)}"}
                })
                return

        except Exception as e:
            logger.error(f"[PTC Streaming] Error in continuation: {e}")
            yield self._format_sse_event({
                "type": "error",
                "error": {"type": "api_error", "message": str(e)}
            })
            return

    async def _complete_code_execution_streaming(
        self,
        result: ExecutionResult,
        execute_code_call: dict,
        claude_response: MessageResponse,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict],
        anthropic_beta: Optional[str],
        message_id: str,
        start_index: int,
        initial_input_tokens: int,
        initial_output_tokens: int,
    ) -> AsyncGenerator[str, None]:
        """Complete code execution and emit streaming events."""
        global_index = start_index
        total_input_tokens = initial_input_tokens
        total_output_tokens = initial_output_tokens

        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        # Build continuation messages
        messages = _filter_non_direct_tool_calls(list(original_request.messages))

        assistant_content = []
        for block in claude_response.content:
            if hasattr(block, "model_dump"):
                assistant_content.append(block.model_dump())
            elif isinstance(block, dict):
                assistant_content.append(block)

        filtered_assistant_content = _filter_content_blocks_for_bedrock(assistant_content)
        messages.append({
            "role": "assistant",
            "content": filtered_assistant_content
        })

        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": execute_code_call["id"],
                "content": tool_result_content
            }]
        })

        # Create continuation request
        continuation_request = MessageRequest(
            model=original_request.model,
            messages=messages,
            max_tokens=original_request.max_tokens,
            system=original_request.system,
            temperature=original_request.temperature,
            top_p=original_request.top_p,
            top_k=original_request.top_k,
            stop_sequences=original_request.stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=original_request.tool_choice,
            thinking=original_request.thinking,
        )

        # Call Bedrock
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier, anthropic_beta
        )

        if final_response.usage:
            total_input_tokens += final_response.usage.input_tokens
            total_output_tokens += final_response.usage.output_tokens

        # Check if Claude called execute_code again
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive handling - not implemented in streaming for simplicity
            # Fall back to emitting the response as-is
            logger.warning("[PTC Streaming] Multi-round code execution not fully supported in streaming")

        # Add direct caller to tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        # Emit content blocks
        content_list = []
        for block in final_response.content:
            if hasattr(block, 'model_dump'):
                content_list.append(block.model_dump())
            else:
                content_list.append(block)

        events, global_index = self._emit_content_block_events(content_list, global_index)
        for event in events:
            yield event

        stop_reason = final_response.stop_reason or "end_turn"
        for event in self._emit_message_end(stop_reason, total_output_tokens):
            yield event

    async def _finalize_code_execution_streaming(
        self,
        result: ExecutionResult,
        code_execution_tool_id: str,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict],
        code: str,
        execution_state: PTCExecutionState,
        message_id: str,
        start_index: int,
    ) -> AsyncGenerator[str, None]:
        """Finalize code execution in continuation flow with streaming."""
        global_index = start_index
        total_output_tokens = 0

        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        # Use saved state parameters
        effective_system = execution_state.original_system if execution_state.original_system is not None else original_request.system
        effective_model = execution_state.original_model or original_request.model
        effective_max_tokens = execution_state.original_max_tokens or original_request.max_tokens
        effective_temperature = execution_state.original_temperature if execution_state.original_temperature is not None else original_request.temperature
        effective_top_p = execution_state.original_top_p if execution_state.original_top_p is not None else original_request.top_p
        effective_top_k = execution_state.original_top_k if execution_state.original_top_k is not None else original_request.top_k
        effective_stop_sequences = execution_state.original_stop_sequences or original_request.stop_sequences
        effective_tool_choice = execution_state.original_tool_choice or original_request.tool_choice
        effective_thinking = execution_state.original_thinking or original_request.thinking
        effective_anthropic_beta = execution_state.original_anthropic_beta

        # Build messages
        # In PTC continuation flow, we skip ALL assistant messages and user messages with tool_result
        # from the client's echoed conversation. The client echoes tool_use blocks without the
        # 'caller' field (SDK strips it), so we can't distinguish PTC tool calls from direct calls.
        # We reconstruct the conversation using only the original user query and our stored state.
        messages = []
        msg_list = list(original_request.messages)

        for i, msg in enumerate(msg_list):
            if isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content", [])
            elif hasattr(msg, "role"):
                role = msg.role
                content = msg.content if hasattr(msg, "content") else []
            else:
                continue

            # Skip ALL assistant messages - we'll add our own stored content
            # This avoids issues with tool_use blocks that don't have corresponding tool_results
            if role == "assistant":
                continue

            # Skip user messages with tool_result - those are for PTC tool calls
            if role == "user" and isinstance(content, list):
                has_tool_result = any(
                    (isinstance(b, dict) and b.get("type") == "tool_result") or
                    (hasattr(b, "type") and b.type == "tool_result")
                    for b in content
                )
                if has_tool_result:
                    continue

            msg_dict = msg if isinstance(msg, dict) else msg.model_dump()
            messages.append(msg_dict)

        # Append stored assistant content
        if execution_state.original_assistant_content:
            filtered_assistant_content = _filter_content_blocks_for_bedrock(
                execution_state.original_assistant_content
            )
            messages.append({
                "role": "assistant",
                "content": filtered_assistant_content
            })
            execute_code_id = execution_state.original_execute_code_id or f"toolu_{code_execution_tool_id[-12:]}"
        else:
            execute_code_id = f"toolu_{code_execution_tool_id[-12:]}"
            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": execute_code_id,
                    "name": "execute_code",
                    "input": {"code": code}
                }]
            })

        # Add tool result
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": execute_code_id,
                "content": tool_result_content
            }]
        })

        # Create continuation request
        continuation_request = MessageRequest(
            model=effective_model,
            messages=messages,
            max_tokens=effective_max_tokens,
            system=effective_system,
            temperature=effective_temperature,
            top_p=effective_top_p,
            top_k=effective_top_k,
            stop_sequences=effective_stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=effective_tool_choice,
            thinking=effective_thinking,
        )

        # Call Bedrock
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier, effective_anthropic_beta
        )

        if final_response.usage:
            total_output_tokens += final_response.usage.output_tokens

        # Check if Claude called execute_code again (recursive code execution)
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive handling for multi-round code execution
            logger.info("[PTC Streaming] Claude requested another code execution, handling recursively")

            # Execute the new code in sandbox
            new_code = next_execute_code.get("input", {}).get("code", "")
            new_code_execution_tool_id = f"srvtoolu_{uuid4().hex[:12]}"

            # Store new assistant content for potential further continuation
            new_assistant_content = []
            for block in final_response.content:
                if hasattr(block, "model_dump"):
                    new_assistant_content.append(block.model_dump())
                elif isinstance(block, dict):
                    new_assistant_content.append(block)

            gen = self.sandbox_executor.execute_code(new_code, session)

            try:
                new_result = await gen.__anext__()

                if isinstance(new_result, (ToolCallRequest, BatchToolCallRequest)):
                    # Tool call(s) requested - emit events and return
                    content_blocks = []

                    # Add text from response
                    for block in final_response.content:
                        if hasattr(block, "type"):
                            if block.type == "text":
                                content_blocks.append({"type": "text", "text": block.text if hasattr(block, "text") else ""})

                    # Add server_tool_use for code_execution
                    content_blocks.append({
                        "type": "server_tool_use",
                        "id": new_code_execution_tool_id,
                        "name": "code_execution",
                        "input": {"code": new_code}
                    })

                    # Add tool_use block(s) for client execution
                    if isinstance(new_result, BatchToolCallRequest):
                        pending_call_ids = [r.call_id for r in new_result.requests]
                        first_call = new_result.requests[0]

                        for tool_request in new_result.requests:
                            content_blocks.append({
                                "type": "tool_use",
                                "id": f"toolu_{tool_request.call_id[:12]}",
                                "name": tool_request.tool_name,
                                "input": tool_request.arguments,
                                "caller": {
                                    "type": PTC_ALLOWED_CALLER,
                                    "tool_id": new_code_execution_tool_id
                                }
                            })

                        # Store state for continuation
                        new_state = PTCExecutionState(
                            session_id=session.session_id,
                            code_execution_tool_id=new_code_execution_tool_id,
                            code=new_code,
                            pending_tool_call_id=first_call.call_id,
                            pending_tool_name=first_call.tool_name,
                            pending_tool_input=first_call.arguments,
                            pending_batch_call_ids=pending_call_ids,
                            original_system=execution_state.original_system,
                            original_model=execution_state.original_model,
                            original_max_tokens=execution_state.original_max_tokens,
                            original_temperature=execution_state.original_temperature,
                            original_top_p=execution_state.original_top_p,
                            original_top_k=execution_state.original_top_k,
                            original_stop_sequences=execution_state.original_stop_sequences,
                            original_tool_choice=execution_state.original_tool_choice,
                            original_thinking=execution_state.original_thinking,
                            original_anthropic_beta=effective_anthropic_beta,
                            original_assistant_content=new_assistant_content,
                            original_execute_code_id=next_execute_code.get("id"),
                        )
                        self._execution_states[session.session_id] = new_state
                        self._execution_generators[session.session_id] = gen

                        session.pending_tool_call = PendingToolCall(
                            call_id=first_call.call_id,
                            tool_name=first_call.tool_name,
                            arguments=first_call.arguments,
                            session_id=session.session_id,
                            code_execution_tool_id=new_code_execution_tool_id
                        )
                    else:
                        # Single tool call
                        content_blocks.append({
                            "type": "tool_use",
                            "id": f"toolu_{uuid4().hex[:12]}",
                            "name": new_result.tool_name,
                            "input": new_result.arguments,
                            "caller": {
                                "type": PTC_ALLOWED_CALLER,
                                "tool_id": new_code_execution_tool_id
                            }
                        })

                        # Store state for continuation
                        new_state = PTCExecutionState(
                            session_id=session.session_id,
                            code_execution_tool_id=new_code_execution_tool_id,
                            code=new_code,
                            pending_tool_call_id=new_result.call_id,
                            pending_tool_name=new_result.tool_name,
                            pending_tool_input=new_result.arguments,
                            original_system=execution_state.original_system,
                            original_model=execution_state.original_model,
                            original_max_tokens=execution_state.original_max_tokens,
                            original_temperature=execution_state.original_temperature,
                            original_top_p=execution_state.original_top_p,
                            original_top_k=execution_state.original_top_k,
                            original_stop_sequences=execution_state.original_stop_sequences,
                            original_tool_choice=execution_state.original_tool_choice,
                            original_thinking=execution_state.original_thinking,
                            original_anthropic_beta=effective_anthropic_beta,
                            original_assistant_content=new_assistant_content,
                            original_execute_code_id=next_execute_code.get("id"),
                        )
                        self._execution_states[session.session_id] = new_state
                        self._execution_generators[session.session_id] = gen

                        session.pending_tool_call = PendingToolCall(
                            call_id=new_result.call_id,
                            tool_name=new_result.tool_name,
                            arguments=new_result.arguments,
                            session_id=session.session_id,
                            code_execution_tool_id=new_code_execution_tool_id
                        )

                    # Emit content block events
                    events, global_index = self._emit_content_block_events(content_blocks, global_index)
                    for event in events:
                        yield event

                    # Emit message end with stop_reason="tool_use"
                    for event in self._emit_message_end("tool_use", total_output_tokens):
                        yield event
                    return

                elif isinstance(new_result, ExecutionResult):
                    # Code completed - recursively call finalize
                    async for event in self._finalize_code_execution_streaming(
                        result=new_result,
                        code_execution_tool_id=new_code_execution_tool_id,
                        original_request=original_request,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                        session=session,
                        ptc_callable_tools=ptc_callable_tools,
                        code=new_code,
                        execution_state=PTCExecutionState(
                            session_id=session.session_id,
                            code_execution_tool_id=new_code_execution_tool_id,
                            code=new_code,
                            pending_tool_call_id="",
                            pending_tool_name="",
                            pending_tool_input={},
                            original_system=execution_state.original_system,
                            original_model=execution_state.original_model,
                            original_max_tokens=execution_state.original_max_tokens,
                            original_temperature=execution_state.original_temperature,
                            original_top_p=execution_state.original_top_p,
                            original_top_k=execution_state.original_top_k,
                            original_stop_sequences=execution_state.original_stop_sequences,
                            original_tool_choice=execution_state.original_tool_choice,
                            original_thinking=execution_state.original_thinking,
                            original_anthropic_beta=effective_anthropic_beta,
                            original_assistant_content=new_assistant_content,
                            original_execute_code_id=next_execute_code.get("id"),
                        ),
                        message_id=message_id,
                        start_index=global_index,
                    ):
                        yield event
                    return

            except StopAsyncIteration:
                # Code completed without tool calls
                pass

        # Add direct caller to tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        # Emit content blocks
        content_list = []
        for block in final_response.content:
            if hasattr(block, 'model_dump'):
                content_list.append(block.model_dump())
            else:
                content_list.append(block)

        events, global_index = self._emit_content_block_events(content_list, global_index)
        for event in events:
            yield event

        stop_reason = final_response.stop_reason or "end_turn"
        for event in self._emit_message_end(stop_reason, total_output_tokens):
            yield event

    async def shutdown(self) -> None:
        """Shutdown PTC service and cleanup resources."""
        if self._sandbox_executor:
            self._sandbox_executor.stop_cleanup_task()
            await self._sandbox_executor.close_all_sessions()


# Global PTC service instance
_ptc_service: Optional[PTCService] = None


def get_ptc_service() -> PTCService:
    """Get global PTC service instance."""
    global _ptc_service
    if _ptc_service is None:
        _ptc_service = PTCService()
    return _ptc_service
