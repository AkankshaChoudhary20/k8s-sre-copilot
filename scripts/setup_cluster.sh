#!/usr/bin/env bash
# Create the local Kind cluster and seed it with intentionally broken
# workloads so `detect` (and later rca/fix) have real problems to find.
set -euo pipefail

cd "$(dirname "$0")/.."

CLUSTER_NAME="sre-copilot"
KIND="./bin/kind"

if ! docker info >/dev/null 2>&1; then
  echo "error: Docker daemon is not running. Start Docker Desktop and re-run this script." >&2
  exit 1
fi

if ! "$KIND" get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "Creating Kind cluster '$CLUSTER_NAME'..."
  "$KIND" create cluster --name "$CLUSTER_NAME"
else
  echo "Kind cluster '$CLUSTER_NAME' already exists, reusing it."
fi

kubectl config use-context "kind-$CLUSTER_NAME"

echo "Applying seeded-failure manifests..."
kubectl apply -f manifests/

echo "Done. Give pods ~30-60s to actually fail (crashloop backoff, OOM restarts), then run:"
echo "  python3 src/cli.py detect"
