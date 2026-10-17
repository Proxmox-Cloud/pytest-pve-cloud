#!/bin/bash

branch=$(git branch --show-current)

for file in images/*; do
    dockerfile=$(basename "$file")
    image_kind="${dockerfile##*.}"

    docker build -t ${PXC_INTEGRATION_REGISTRY}/${image_kind}:${branch} \
        -f images/${dockerfile} .

    docker push ${PXC_INTEGRATION_REGISTRY}/${image_kind}:${branch}
done
