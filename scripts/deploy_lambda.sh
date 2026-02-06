#!/usr/bin/env bash
set -euo pipefail

# Deploy/update AWS Lambda for embodied-ai PWA backend.
# Usage:
#   ./scripts/deploy_lambda.sh deploy/lambda-config.json config.lambda.json

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <lambda-config.json> <config.lambda.json>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LAMBDA_CFG="$1"
APP_CFG="$2"

if [[ ! -f "$LAMBDA_CFG" ]]; then
  echo "Lambda config not found: $LAMBDA_CFG"
  exit 1
fi

if [[ ! -f "$APP_CFG" ]]; then
  echo "App config not found: $APP_CFG"
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not found"
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found"
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found"
  exit 1
fi

ensure_permission() {
  local sid="$1"
  local action="$2"
  local extra_args=("${@:3}")
  set +e
  local output
  output=$(aws lambda add-permission \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --statement-id "$sid" \
    --action "$action" \
    --principal "*" \
    "${extra_args[@]}" 2>&1)
  local rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    if grep -q "ResourceConflictException" <<<"$output"; then
      return 0
    fi
    echo "$output"
    return $rc
  fi
}

REGION="$(jq -r '.region' "$LAMBDA_CFG")"
FUNCTION_NAME="$(jq -r '.function_name' "$LAMBDA_CFG")"
ROLE_ARN="$(jq -r '.role_arn' "$LAMBDA_CFG")"
RUNTIME="$(jq -r '.runtime // "python3.12"' "$LAMBDA_CFG")"
HANDLER="$(jq -r '.handler // "embodied_ai.lambda_handler.handler"' "$LAMBDA_CFG")"
MEMORY_SIZE="$(jq -r '.memory_size // 1024' "$LAMBDA_CFG")"
TIMEOUT="$(jq -r '.timeout // 30' "$LAMBDA_CFG")"
EPHEMERAL_STORAGE_MB="$(jq -r '.ephemeral_storage_mb // 1024' "$LAMBDA_CFG")"
ARCH0="$(jq -r '.architectures[0] // "x86_64"' "$LAMBDA_CFG")"
FUNCTION_URL_AUTH_TYPE="$(jq -r '.function_url_auth_type // "NONE"' "$LAMBDA_CFG")"

ENV_JSON="$(jq -c '.environment // {}' "$LAMBDA_CFG")"
if [[ "$ENV_JSON" == "null" ]]; then
  ENV_JSON='{}'
fi

TMP_DIR="$(mktemp -d)"
ARTIFACT_DIR="$TMP_DIR/artifact"
mkdir -p "$ARTIFACT_DIR"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "Building deployment artifact..."
uv sync --frozen --directory "$REPO_ROOT"
uv export --directory "$REPO_ROOT" --format requirements-txt -o "$TMP_DIR/requirements.txt" >/dev/null
# Export includes '-e .', but code is bundled directly below.
sed -i '/^-e \.$/d' "$TMP_DIR/requirements.txt"
uv pip install \
  --target "$ARTIFACT_DIR" \
  --requirement "$TMP_DIR/requirements.txt" \
  --quiet

cp -R "$REPO_ROOT/src/embodied_ai" "$ARTIFACT_DIR/"
cp "$APP_CFG" "$ARTIFACT_DIR/config.lambda.json"
cp "$REPO_ROOT/CLAUDE.md" "$ARTIFACT_DIR/CLAUDE.md"

(cd "$ARTIFACT_DIR" && zip -r "$TMP_DIR/lambda.zip" . >/dev/null)

set +e
aws lambda get-function \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" >/dev/null 2>&1
EXISTS=$?
set -e

if [[ $EXISTS -ne 0 ]]; then
  echo "Creating Lambda function: $FUNCTION_NAME"
  aws lambda create-function \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --runtime "$RUNTIME" \
    --role "$ROLE_ARN" \
    --handler "$HANDLER" \
    --architectures "$ARCH0" \
    --memory-size "$MEMORY_SIZE" \
    --timeout "$TIMEOUT" \
    --ephemeral-storage "{\"Size\": $EPHEMERAL_STORAGE_MB}" \
    --environment "{\"Variables\": $ENV_JSON}" \
    --zip-file "fileb://$TMP_DIR/lambda.zip" \
    >/dev/null
else
  echo "Updating Lambda code: $FUNCTION_NAME"
  aws lambda update-function-code \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$TMP_DIR/lambda.zip" \
    >/dev/null

  # Wait for code update to complete before touching configuration.
  aws lambda wait function-updated \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME"

  echo "Updating Lambda configuration..."
  aws lambda update-function-configuration \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --runtime "$RUNTIME" \
    --handler "$HANDLER" \
    --memory-size "$MEMORY_SIZE" \
    --timeout "$TIMEOUT" \
    --ephemeral-storage "{\"Size\": $EPHEMERAL_STORAGE_MB}" \
    --environment "{\"Variables\": $ENV_JSON}" \
    >/dev/null
fi

echo "Waiting for function update..."
aws lambda wait function-updated \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME"

set +e
aws lambda get-function-url-config \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" >/dev/null 2>&1
HAS_URL=$?
set -e

if [[ $HAS_URL -ne 0 ]]; then
  echo "Creating function URL..."
  aws lambda create-function-url-config \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --auth-type "$FUNCTION_URL_AUTH_TYPE" \
    --cors "$(jq -c '.cors // {"AllowOrigins":["*"],"AllowMethods":["*"],"AllowHeaders":["*"]}' "$LAMBDA_CFG")" \
    >/dev/null

  if [[ "$FUNCTION_URL_AUTH_TYPE" == "NONE" ]]; then
    ensure_permission \
      "FunctionUrlPublicInvokeUrl" \
      "lambda:InvokeFunctionUrl" \
      --function-url-auth-type NONE
    ensure_permission \
      "FunctionUrlPublicInvokeFunction" \
      "lambda:InvokeFunction"
  fi
else
  echo "Updating function URL config..."
  aws lambda update-function-url-config \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --auth-type "$FUNCTION_URL_AUTH_TYPE" \
    --cors "$(jq -c '.cors // {"AllowOrigins":["*"],"AllowMethods":["*"],"AllowHeaders":["*"]}' "$LAMBDA_CFG")" \
    >/dev/null

  if [[ "$FUNCTION_URL_AUTH_TYPE" == "NONE" ]]; then
    ensure_permission \
      "FunctionUrlPublicInvokeUrl" \
      "lambda:InvokeFunctionUrl" \
      --function-url-auth-type NONE
    ensure_permission \
      "FunctionUrlPublicInvokeFunction" \
      "lambda:InvokeFunction"
  fi
fi

URL="$(aws lambda get-function-url-config \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --query 'FunctionUrl' \
  --output text)"

echo
echo "Deploy complete."
echo "Function name : $FUNCTION_NAME"
echo "Region        : $REGION"
echo "Function URL  : $URL"
