FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# NVIDIA CUDA 12.4 지원 (GPU 사용 시)
# base image를 nvidia/cuda:12.4.0-runtime-ubuntu24.04 로 교체 가능

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt

COPY . .

EXPOSE 8001

CMD ["python3", "server.py"]
