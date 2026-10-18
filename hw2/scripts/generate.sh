#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
rm -rf .generated

set -- generate \
    -i openapi/marketplace.yaml \
    -g python-fastapi \
    -c openapi/generator-config.yaml \
    -t openapi/templates \
    -o .generated \
    --global-property apiTests=false,modelTests=false,apiDocs=false,modelDocs=false

if [ -n "${OPENAPI_GENERATOR_JAR:-}" ]; then
    java -jar "$OPENAPI_GENERATOR_JAR" "$@"
else
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -v "$project_dir:/workspace" \
        -w /workspace \
        openapitools/openapi-generator-cli:v7.25.0 "$@"
fi
