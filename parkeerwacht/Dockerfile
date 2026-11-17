FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2

ARG BUILD_ARCH
# ffmpeg zet voor go2rtc één videobeeld om naar JPEG
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# go2rtc (MIT), vastgezet op versie en checksum
RUN case "${BUILD_ARCH}" in \
      aarch64) GOARCH=arm64; SUM=af416f22b21a24ab83b8d78642190b4932ae695b4e386853f11d1b0319781763 ;; \
      amd64) GOARCH=amd64; SUM=20b46c6910734cb05ecefb234066f36868ff19b42ba1418a7a32e13a3e035712 ;; \
      *) echo "onbekende architectuur: ${BUILD_ARCH}" >&2; exit 1 ;; \
    esac \
    && python -c "import sys, urllib.request as u; u.urlretrieve(sys.argv[1], '/usr/local/bin/go2rtc')" \
       "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.15/go2rtc_linux_${GOARCH}" \
    && echo "${SUM}  /usr/local/bin/go2rtc" | sha256sum -c - \
    && chmod +x /usr/local/bin/go2rtc

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# YOLOX-Tiny (Apache-2.0), vastgezet op checksum
RUN python -c "import urllib.request as u; u.urlretrieve('https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx', '/app/yolox_tiny.onnx')" \
    && echo "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7  /app/yolox_tiny.onnx" | sha256sum -c -

COPY app/ /app/
WORKDIR /app
CMD ["python", "main.py"]
