#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSIONS="${PYTHON_VERSIONS:-3.10 3.11 3.12}"
REGISTRY_PREFIX="${PYTHON_BASE_REGISTRY_PREFIX:-docker.io/library}"

cd "${ROOT_DIR}"

for version in ${VERSIONS}; do
  image="audit-harness/python-basic:${version}"
  base_image="${REGISTRY_PREFIX}/python:${version}-slim"
  echo "Building ${image} from ${base_image}"
  docker build \
    --build-arg "BASE_IMAGE=${base_image}" \
    -t "${image}" \
    -f docker/sandbox/python-basic.Dockerfile .
done

