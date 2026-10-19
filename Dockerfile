FROM python:alpine3.19

# Install build dependencies for C extensions (netifaces, etc.)
# todo: this should be refacted into a builder image?
RUN apk add --no-cache gcc musl-dev linux-headers curl

# install vcluster
RUN curl -L -o /usr/local/bin/vcluster "https://github.com/loft-sh/vcluster/releases/latest/download/vcluster-linux-amd64" && \
    chmod +x /usr/local/bin/vcluster

# install helm
RUN wget -q https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz && \
    tar -zxvf helm-v4.3.1-linux-amd64.tar.gz && \
    mv linux-amd64/helm /usr/local/bin/helm && \
    chmod +x /usr/local/bin/helm && \
    rm -rf linux-amd64 helm-v4.3.1-linux-amd64.tar.gz

# install requirements in seperate layer
COPY requirements.txt ./

RUN pip install -r requirements.txt

# install the package
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-deps .

ENV PYTHONUNBUFFERED=1

CMD ["vcluster-mcp"]
