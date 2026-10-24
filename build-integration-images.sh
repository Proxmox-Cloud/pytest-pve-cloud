#!/bin/bash

# todo: this should either be moved to a public registry, or the entire collection
# needs to be refactored to be private registry first

branch=$(git branch --show-current)

for file in images/*; do
    dockerfile=$(basename "$file")
    image_kind="${dockerfile##*.}"

    docker build -t ${PXC_INTEGRATION_REGISTRY}/${image_kind}:${branch} \
        -f images/${dockerfile} .

    docker push ${PXC_INTEGRATION_REGISTRY}/${image_kind}:${branch}
done

docker build -t ${PXC_INTEGRATION_REGISTRY}/pytest-pve-cloud:${branch} .
docker push ${PXC_INTEGRATION_REGISTRY}/pytest-pve-cloud:${branch}
