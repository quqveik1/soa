#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
docker build --target generated --output type=local,dest=.generated .
