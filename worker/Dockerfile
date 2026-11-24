# Build từ thư mục gốc project:  docker compose build worker
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    INSIGHTFACE_HOME=/opt/insightface

# build-essential: insightface có phần Cython cần biên dịch; libgomp1: ONNX Runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ghim numpy 1.x và opencv cho mọi lệnh pip bên dưới (xem worker/constraints.txt).
COPY worker/constraints.txt /tmp/constraints.txt
ENV PIP_CONSTRAINT=/tmp/constraints.txt

# insightface cần numpy + cython có sẵn khi build, nên cài trước và tắt build isolation.
RUN pip install numpy==1.26.4 cython==3.0.10 setuptools wheel
COPY worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-build-isolation insightface==0.7.3 \
    && pip install -r /tmp/requirements.txt

# insightface kéo theo opencv-python (bản có giao diện, cần libGL mà image slim không có):
# gỡ nó và chỉ giữ bản headless. Rồi kiểm tra ngay lúc build để lỗi môi trường hiện ra sớm, rõ ràng.
RUN pip uninstall -y opencv-python opencv-contrib-python \
    && pip install --force-reinstall --no-deps opencv-python-headless==4.9.0.80 \
    && python -c "import numpy, cv2, onnxruntime, insightface; \
assert numpy.__version__.startswith('1.'), 'numpy bi nang len ' + numpy.__version__; \
print('OK numpy', numpy.__version__, '| cv2', cv2.__version__, '| onnxruntime', onnxruntime.__version__, '| insightface', insightface.__version__)"

# Tải sẵn model buffalo_l (SCRFD + ArcFace) vào image để N worker không phải tải lại.
RUN python -c "from insightface.app import FaceAnalysis; \
FaceAnalysis(name='buffalo_l', root='/opt/insightface', allowed_modules=['detection','recognition'], providers=['CPUExecutionProvider'])"

COPY common /app/common
COPY worker /app/worker
COPY client /app/client
COPY tools /app/tools

# Thư mục ảnh: chỉ user ứng dụng được đọc/ghi (dữ liệu sinh trắc học)
RUN useradd -r -u 10001 coffee && mkdir -p /data/photos && chown -R coffee /data /opt/insightface
USER coffee

CMD ["python", "-m", "worker.worker"]
