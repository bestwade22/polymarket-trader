#!/usr/bin/env bash
# Apply ECR lifecycle policy to SAM Lambda image repos (cost control).
# Usage: ./scripts/apply_ecr_lifecycle.sh [name-filter]
# Default filter: polymarket (matches polymarket-trader-* repos).
set -euo pipefail

FILTER="${1:-polymarket}"
POLICY_FILE="$(cd "$(dirname "$0")/.." && pwd)/infrastructure/ecr-lifecycle-policy.json"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-east-1}}"

if [[ ! -f "$POLICY_FILE" ]]; then
  echo "Missing lifecycle policy: $POLICY_FILE" >&2
  exit 1
fi

echo "Region=$REGION filter=$FILTER"

repos=$(
  aws ecr describe-repositories \
    --region "$REGION" \
    --output json \
    --query "repositories[?contains(repositoryName, \`${FILTER}\`)].repositoryName" \
    | tr -d '[]" \n' \
    | tr ',' '\n' \
    | sed '/^$/d'
)

if [[ -z "$repos" ]]; then
  echo "No ECR repositories matching '${FILTER}' in ${REGION}"
  exit 0
fi

count=0
while IFS= read -r repo; do
  [[ -z "$repo" ]] && continue
  echo "Applying lifecycle policy → ${repo}"
  aws ecr put-lifecycle-policy \
    --region "$REGION" \
    --repository-name "$repo" \
    --lifecycle-policy-text "file://${POLICY_FILE}" \
    --output text \
    --query 'repositoryName' >/dev/null
  count=$((count + 1))
done <<< "$repos"

echo "Done (${count} repos). Untagged images expire after 1 day; keep newest 3."
