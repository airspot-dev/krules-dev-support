FROM google/cloud-sdk:latest

RUN pip3 install --upgrade pip && pip3 install krules-dev-support==0.12.2
