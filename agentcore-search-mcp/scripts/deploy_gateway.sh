#!/usr/bin/env bash
# Deploy the AgentCore Gateway + Web Search target that this MCP server talks to.
#
# Creates (idempotently): a gateway service role, an MCP gateway with AWS_IAM
# auth, and a web-search connector target. Prints AGENTCORE_GATEWAY_URL and a
# ready-to-run `claude mcp add` command on success. Safe to re-run: existing
# role/gateway/target with the same names are reused, not recreated.
#
# Usage:
#   cd agentcore-search-mcp && uv sync        # once, for botocore
#   bash scripts/deploy_gateway.sh
#
# Overridables: AWS_REGION (must be us-east-1), GATEWAY_NAME, TARGET_NAME, ROLE_NAME
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
GATEWAY_NAME="${GATEWAY_NAME:-agentcore-search-mcp}"
TARGET_NAME="${TARGET_NAME:-web-search-tool}"
ROLE_NAME="${ROLE_NAME:-${GATEWAY_NAME}-service-role}"
CLIENT_TOKEN="${CLIENT_TOKEN:-${GATEWAY_NAME}-web-search-setup-token}"
TARGET_CLIENT_TOKEN="${TARGET_CLIENT_TOKEN:-${GATEWAY_NAME}-web-search-target-token}"

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${REGION}" != "us-east-1" ]]; then
  echo "The AgentCore Web Search connector is currently available only in us-east-1." >&2
  echo "Set AWS_REGION=us-east-1 and rerun this script." >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI is required." >&2
  exit 1
fi

# Python (with botocore, for JSON parsing + SigV4 target creation): prefer the
# module venv, fall back to system python3.
if [[ -x "${MODULE_DIR}/.venv/bin/python" ]] \
    && "${MODULE_DIR}/.venv/bin/python" -c "import botocore" >/dev/null 2>&1; then
  PYTHON="${MODULE_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 -c "import botocore" >/dev/null 2>&1; then
  PYTHON="python3"
else
  echo "python with botocore is required. Run 'uv sync' in ${MODULE_DIR} first." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

TRUST_POLICY="${TMP_DIR}/trust-policy.json"
PERMISSIONS_POLICY="${TMP_DIR}/web-search-policy.json"
GATEWAY_RESPONSE="${TMP_DIR}/gateway.json"
TARGET_RESPONSE="${TMP_DIR}/target.json"

cat > "${TRUST_POLICY}" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

cat > "${PERMISSIONS_POLICY}" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeGateway",
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeGateway",
      "Resource": "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:gateway/*"
    },
    {
      "Sid": "InvokeWebSearch",
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeWebSearch",
      "Resource": "arn:aws:bedrock-agentcore:${REGION}:aws:tool/web-search.v1"
    }
  ]
}
JSON

extract_item_by_name() {
  "${PYTHON}" - "$1" "$2" <<'PY'
import json
import sys

name, path = sys.argv[1:3]
data = json.load(open(path))
for item in data.get("items", []):
    if item.get("name") == name:
        print(json.dumps(item))
        break
PY
}

json_field() {
  "${PYTHON}" - "$1" "$2" <<'PY'
import json
import sys

field, path = sys.argv[1:3]
data = json.load(open(path))
print(data.get(field, ""))
PY
}

gateway_url_from() {
  "${PYTHON}" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("gatewayUrl") or d.get("gatewayEndpoint") or d.get("endpoint") or "")' "$1"
}

# --- 1. Service role (assumed by the gateway to invoke the web-search tool) ---
ROLE_ARN="$(aws iam get-role --role-name "${ROLE_NAME}" --query 'Role.Arn' --output text 2>/dev/null || true)"
if [[ -z "${ROLE_ARN}" || "${ROLE_ARN}" == "None" ]]; then
  echo "Creating gateway service role: ${ROLE_NAME}"
  ROLE_ARN="$(aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "file://${TRUST_POLICY}" \
    --query 'Role.Arn' \
    --output text)"
  ROLE_CREATED=1
else
  echo "Using existing gateway service role: ${ROLE_NAME}"
  ROLE_CREATED=0
fi

# Attach the inline policy only when the role is new or the policy is missing,
# so re-runs never overwrite a policy someone tuned by hand.
if [[ "${ROLE_CREATED}" == "1" ]] || ! aws iam get-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-name AgentCoreWebSearchGatewayPolicy >/dev/null 2>&1; then
  echo "Attaching AgentCoreWebSearchGatewayPolicy to ${ROLE_NAME}"
  aws iam put-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-name AgentCoreWebSearchGatewayPolicy \
    --policy-document "file://${PERMISSIONS_POLICY}"
  # IAM policy propagation is eventually consistent.
  sleep 10
fi

# --- 2. Gateway (MCP protocol, AWS_IAM authorizer) ---
aws bedrock-agentcore-control list-gateways \
  --region "${REGION}" \
  --output json > "${GATEWAY_RESPONSE}.list"
EXISTING_GATEWAY="$(extract_item_by_name "${GATEWAY_NAME}" "${GATEWAY_RESPONSE}.list")"

if [[ -n "${EXISTING_GATEWAY}" ]]; then
  echo "Using existing AgentCore Gateway: ${GATEWAY_NAME}"
  printf '%s\n' "${EXISTING_GATEWAY}" > "${GATEWAY_RESPONSE}"
else
  echo "Creating AgentCore Gateway: ${GATEWAY_NAME}"
  aws bedrock-agentcore-control create-gateway \
    --name "${GATEWAY_NAME}" \
    --role-arn "${ROLE_ARN}" \
    --protocol-type MCP \
    --authorizer-type AWS_IAM \
    --client-token "${CLIENT_TOKEN}" \
    --region "${REGION}" \
    --output json > "${GATEWAY_RESPONSE}"
fi

GATEWAY_ID="$("${PYTHON}" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("gatewayId") or d.get("gatewayIdentifier") or d.get("id") or d.get("gatewayArn", "").rstrip("/").split("/")[-1])' "${GATEWAY_RESPONSE}")"
GATEWAY_URL="$(gateway_url_from "${GATEWAY_RESPONSE}")"

if [[ -z "${GATEWAY_ID}" ]]; then
  echo "Unable to determine Gateway ID from create-gateway response:" >&2
  cat "${GATEWAY_RESPONSE}" >&2
  exit 1
fi

STATUS=""
for attempt in {1..30}; do
  aws bedrock-agentcore-control get-gateway \
    --gateway-identifier "${GATEWAY_ID}" \
    --region "${REGION}" \
    --output json > "${GATEWAY_RESPONSE}.ready"
  STATUS="$(json_field status "${GATEWAY_RESPONSE}.ready")"
  if [[ "${STATUS}" == "READY" ]]; then
    cp "${GATEWAY_RESPONSE}.ready" "${GATEWAY_RESPONSE}"
    GATEWAY_URL="$(gateway_url_from "${GATEWAY_RESPONSE}")"
    break
  fi
  if [[ "${STATUS}" == "FAILED" ]]; then
    echo "Gateway ${GATEWAY_ID} failed to become ready:" >&2
    cat "${GATEWAY_RESPONSE}.ready" >&2
    exit 1
  fi
  echo "Waiting for Gateway ${GATEWAY_ID} to become READY (current: ${STATUS:-unknown})"
  sleep 5
done

if [[ "${STATUS}" != "READY" ]]; then
  echo "Timed out waiting for Gateway ${GATEWAY_ID} to become READY." >&2
  exit 1
fi

# --- 3. Web Search connector target ---
# The connector target configuration is not yet expressible via the aws CLI,
# so the create call goes through the control-plane REST API with SigV4.
aws bedrock-agentcore-control list-gateway-targets \
  --gateway-identifier "${GATEWAY_ID}" \
  --region "${REGION}" \
  --output json > "${TARGET_RESPONSE}.list"
EXISTING_TARGET="$(extract_item_by_name "${TARGET_NAME}" "${TARGET_RESPONSE}.list")"

if [[ -n "${EXISTING_TARGET}" ]]; then
  echo "Using existing Web Search target: ${TARGET_NAME}"
  printf '%s\n' "${EXISTING_TARGET}" > "${TARGET_RESPONSE}"
else
  echo "Creating Web Search target: ${TARGET_NAME}"
  "${PYTHON}" - "${REGION}" "${GATEWAY_ID}" "${TARGET_NAME}" "${TARGET_CLIENT_TOKEN}" > "${TARGET_RESPONSE}" <<'PYTARGET'
import json
import sys
import urllib.error
import urllib.request

import botocore.session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

region, gateway_id, target_name, client_token = sys.argv[1:5]
url = f"https://bedrock-agentcore-control.{region}.amazonaws.com/gateways/{gateway_id}/targets/"
body = {
    "clientToken": client_token,
    "name": target_name,
    "targetConfiguration": {
        "mcp": {
            "connector": {
                "source": {"connectorId": "web-search"},
                "configurations": [
                    {"name": "WebSearch", "parameterValues": {}},
                ],
            }
        }
    },
    "credentialProviderConfigurations": [
        {"credentialProviderType": "GATEWAY_IAM_ROLE"},
    ],
}
payload = json.dumps(body).encode("utf-8")
session = botocore.session.get_session()
credentials = session.get_credentials()
if credentials is None:
    raise SystemExit("AWS credentials are required")
request = AWSRequest(
    method="POST",
    url=url,
    data=payload,
    headers={"Content-Type": "application/json", "Accept": "application/json"},
)
SigV4Auth(credentials.get_frozen_credentials(), "bedrock-agentcore", region).add_auth(request)
http_request = urllib.request.Request(
    url,
    data=payload,
    headers=dict(request.headers.items()),
    method="POST",
)
try:
    with urllib.request.urlopen(http_request, timeout=60) as response:
        sys.stdout.write(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    sys.stderr.write(exc.read().decode("utf-8"))
    sys.stderr.write("\n")
    raise
PYTARGET
fi

TARGET_STATUS=""
for attempt in {1..30}; do
  aws bedrock-agentcore-control list-gateway-targets \
    --gateway-identifier "${GATEWAY_ID}" \
    --region "${REGION}" \
    --output json > "${TARGET_RESPONSE}.ready"
  TARGET_JSON="$(extract_item_by_name "${TARGET_NAME}" "${TARGET_RESPONSE}.ready")"
  if [[ -n "${TARGET_JSON}" ]]; then
    printf '%s\n' "${TARGET_JSON}" > "${TARGET_RESPONSE}"
    TARGET_STATUS="$(json_field status "${TARGET_RESPONSE}")"
    if [[ "${TARGET_STATUS}" == "READY" ]]; then
      break
    fi
    if [[ "${TARGET_STATUS}" == "FAILED" ]]; then
      echo "Target ${TARGET_NAME} failed to become ready:" >&2
      cat "${TARGET_RESPONSE}" >&2
      exit 1
    fi
  else
    TARGET_STATUS="missing"
  fi
  echo "Waiting for target ${TARGET_NAME} to become READY (current: ${TARGET_STATUS:-unknown})"
  sleep 5
done

if [[ "${TARGET_STATUS}" != "READY" ]]; then
  echo "Timed out waiting for target ${TARGET_NAME} to become READY." >&2
  exit 1
fi

if [[ -z "${GATEWAY_URL}" ]]; then
  GATEWAY_URL="https://${GATEWAY_ID}.gateway.bedrock-agentcore.${REGION}.amazonaws.com/mcp"
fi

cat <<EOF

AgentCore Gateway Web Search is ready.

  Gateway ID:   ${GATEWAY_ID}
  Gateway URL:  ${GATEWAY_URL}
  Service role: ${ROLE_ARN}

Configure this MCP server with:

  export AGENTCORE_GATEWAY_URL=${GATEWAY_URL}
  export AGENTCORE_GATEWAY_REGION=${REGION}

Register with Claude Code (uvx runs the published PyPI package; use
'uv --directory ${MODULE_DIR} run agentcore-search-mcp' instead to run this checkout):

  claude mcp add agentcore-search --scope project \\
    --env AGENTCORE_GATEWAY_URL=${GATEWAY_URL} \\
    --env AGENTCORE_GATEWAY_REGION=${REGION} \\
    -- uvx agentcore-search-mcp

Smoke test:

  AGENTCORE_GATEWAY_URL=${GATEWAY_URL} uv --directory ${MODULE_DIR} run python scripts/live_smoke.py

EOF
