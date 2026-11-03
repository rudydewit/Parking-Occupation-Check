FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# YOLOX-Tiny (Apache-2.0), vastgezet op checksum
RUN python -c "import urllib.request as u; u.urlretrieve('https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx', '/app/yolox_tiny.onnx')" \
    && echo "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7  /app/yolox_tiny.onnx" | sha256sum -c -

COPY app/ /app/
WORKDIR /app
CMD ["python", "main.py"]
