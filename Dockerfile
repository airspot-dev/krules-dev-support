FROM docker:24.0.4 as static-docker-source

FROM python:3.10-slim-bullseye
# ARG CLOUD_SDK_VERSION
ENV CLOUD_SDK_VERSION=433.0.1
ENV PATH "$PATH:/opt/google-cloud-sdk/bin/"
ENV INSTALL_COMPONENTS="kubectl google-cloud-cli-skaffold google-cloud-cli-gke-gcloud-auth-plugin terraform"
COPY --from=static-docker-source /usr/local/bin/docker /usr/local/bin/docker
COPY --from=static-docker-source /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
RUN groupadd -r -g 1000 cloudsdk && \
    useradd -r -u 1000 -m -s /bin/bash -g cloudsdk cloudsdk
# ARG INSTALL_COMPONENTS
RUN mkdir -p /usr/share/man/man1/
RUN apt-get update -qqy && apt-get -qqy upgrade && apt-get install -qqy \
        curl \
        gcc \
        python3-dev \
        python3-pip \
        apt-transport-https \
        lsb-release \
        openssh-client \
        git \
        gnupg && \
    pip3 install -U crcmod && \
    export CLOUD_SDK_REPO="cloud-sdk-$(lsb_release -c -s)" && \
    echo "deb https://packages.cloud.google.com/apt $CLOUD_SDK_REPO main" > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add - && \
    curl -fsSL https://apt.releases.hashicorp.com/gpg | apt-key add - && \
    echo "deb [arch=$(dpkg --print-architecture)] https://apt.releases.hashicorp.com $(lsb_release -cs) main" > /etc/apt/sources.list.d/hashicorp.list && \
    apt-get update && apt-get install -y google-cloud-cli=${CLOUD_SDK_VERSION}-0 ${INSTALL_COMPONENTS} && \
    gcloud config set core/disable_usage_reporting true && \
    gcloud config set component_manager/disable_update_check true && \
    gcloud config set metrics/environment github_docker_image && \
    gcloud --version

RUN git config --system credential.'https://source.developers.google.com'.helper gcloud.sh

VOLUME ["/root/.config"]

RUN pip3 install --upgrade pip && pip3 install krules-dev-support==0.12.16 #pulumi
