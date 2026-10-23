FROM python:alpine3.19

# Install build dependencies for C extensions (netifaces, etc.)
# todo: this should be refacted into a builder image?
RUN apk add --no-cache gcc musl-dev linux-headers curl

# install kubectl
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
RUN install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

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

