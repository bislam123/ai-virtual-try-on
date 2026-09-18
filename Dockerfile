# Production image for the backend + in-process AI inference
# (backend/app + the ai/ pipeline it imports directly -- see
# providers/selfhosted.py). Does NOT include the frontend (a static Vite
# build served by the reverse proxy or any static host/CDN -- see
# docs/DEPLOYMENT.md) or the browser extension.
#
# VERIFICATION STATUS -- read before relying on this in production: this
# Dockerfile was written by inspecting docs/DEVELOPMENT.md's own,
# already-verified install sequence (the exact commands that produced the
# real Tesla T4 benchmark this milestone records) and adapting it for a
# Linux container. It has NOT been build-tested here -- no Docker daemon
# was available in the environment that wrote it. The backend-only layers
# (Python packaging, no CUDA) are low-risk and standard. The GPU/CUDA/
# onnxruntime-gpu layer is a well-reasoned but UNVERIFIED design --
# `onnxruntime-gpu` (unlike torch's CUDA wheels, which bundle their own
# CUDA runtime) historically needs a *system* CUDA+cuDNN install whose
# version must match the resolved onnxruntime-gpu version -- hence the
# `nvidia/cuda` base image below rather than a bare `python:3.11-slim`.
# Before any real deployment: build this image, run it against a real
# GPU instance with `--gpus all`, and repeat the real generation smoke
# test that produced this project's own T4 benchmark (see
# docs/DEPLOYMENT.md's GPU deployment section) -- confirm nvidia-smi
# shows the GPU actually in use inside the container, not just that the
# image builds.
#
# Model weights (~2.3GB, ai/models/) are deliberately NOT baked into this
# image -- same "download once, gitignored, never committed" treatment
# they already get in local dev (see docs/DEVELOPMENT.md). Mount a
# persistent volume at /app/ai/models and populate it once via
# ai/inference/download_weights.py (see the build/run instructions in
# docs/DEPLOYMENT.md) -- baking multi-GB binary weights into an image
# would make every build/push/pull enormous for no benefit, and a weight
# update would otherwise require a full image rebuild.

# --- Builder stage: installs everything into one venv ------------------------
FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04 AS builder

# Pin the exact Python minor version this project already targets
# (docs/DEVELOPMENT.md: "what fashn-vton-1.5 is built and tested
# against... solid prebuilt Linux wheels for it"). Ubuntu 22.04's own
# default repos ship Python 3.10, not 3.11 -- the deadsnakes PPA
# (ppa:deadsnakes/ppa, the standard, widely-trusted source for a specific
# non-default Python version on Ubuntu) is added explicitly rather than
# assuming 3.11 happens to already be available.
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip

# Copy only what's needed to install dependencies first, so this layer is
# cached across source-only changes -- exactly the dependency manifests
# already pinned for reproducibility (see backend/requirements.txt's own
# "pinned to exact versions" comment from the earlier hygiene-cleanup
# milestone).
COPY backend/requirements.txt backend/requirements.txt
COPY ai/vendor/aitryon-bodyparser ai/vendor/aitryon-bodyparser
COPY ai/vendor/fashn-vton-1.5 ai/vendor/fashn-vton-1.5
COPY ai/preprocessing ai/preprocessing
COPY product-extractor product-extractor

# CUDA-enabled torch/torchvision -- the default PyPI index, with no
# --index-url override, already pulls the CUDA build (see
# docs/DEVELOPMENT.md's Colab GPU section) -- pinned here to an explicit
# cu121 index instead for build reproducibility, matching this image's
# CUDA 12.1 base. If the target host's driver requires a different CUDA
# minor version, change both this index and the base image tag together.
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# Same install order docs/DEVELOPMENT.md already documents and has
# verified working (aitryon-bodyparser -> fashn-vton-1.5 -> preprocessing
# -> the opencv-contrib swap -> product-extractor -> backend deps),
# adapted only for a non-interactive/non-Colab environment.
RUN pip install -e ai/vendor/aitryon-bodyparser \
    && pip install -e ai/vendor/fashn-vton-1.5 \
    && pip install -e ai/preprocessing \
    && pip uninstall -y opencv-python \
    && pip install opencv-contrib-python \
    && pip install -e product-extractor \
    && pip install -r backend/requirements.txt

# GPU-accelerated ONNX Runtime for DWPose (see
# fashn_vton.dwpose.wholebody.Wholebody, which requests
# CUDAExecutionProvider whenever device starts with "cuda" and falls back
# to CPU automatically otherwise -- docs/DEVELOPMENT.md's own Colab
# section documents this exact swap). Pinned, not left to resolve
# whatever's newest at build time.
RUN pip uninstall -y onnxruntime \
    && pip install onnxruntime-gpu==1.18.1

# --- Runtime stage: no compilers, no build-time-only packages ----------------
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS runtime

# Same deadsnakes reasoning as the builder stage above -- only the
# interpreter itself is needed here, not -venv/-dev (the venv copied in
# from the builder stage already has everything installed into it).
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
    && apt-get purge -y software-properties-common \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AITRYON_DEVICE=cuda \
    AITRYON_WEIGHTS_DIR=/app/ai/models/fashn-vton-1.5 \
    AITRYON_STORAGE_DIR=/app/backend/storage

WORKDIR /app

# Application source only -- no tests/, no docs/, no .git, no frontend/
# (a separate static build, not part of this image), no ai/outputs debug
# images, no node_modules. See .dockerignore for the full exclusion list;
# this explicit COPY list is a second, redundant layer of the same intent
# so a future .dockerignore edit can't silently widen what ships.
COPY backend/app backend/app
COPY backend/alembic backend/alembic
COPY backend/alembic.ini backend/alembic.ini
COPY backend/scripts backend/scripts
COPY ai/vendor/fashn-vton-1.5/src ai/vendor/fashn-vton-1.5/src
COPY ai/preprocessing/src ai/preprocessing/src
COPY ai/vendor/aitryon-bodyparser/src ai/vendor/aitryon-bodyparser/src

# Non-root: this process only ever needs to read its own code, write to
# its storage volume, and talk to the network -- no reason to run as root.
RUN useradd --create-home --shell /bin/false aitryon \
    && mkdir -p /app/ai/models /app/backend/storage \
    && chown -R aitryon:aitryon /app
USER aitryon

EXPOSE 8000

# No curl/wget in this slim runtime image (deliberately -- no extra attack
# surface for tools this app never otherwise needs) -- Python's own
# stdlib urllib is enough to hit the real /health endpoint (see
# docs/DEPLOYMENT.md section 6: it checks the database, not a fake
# always-200 stub).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# No secrets, no database URL, no JWT/SMTP credentials are set here --
# every AITRYON_* value that IS a secret or environment-specific must be
# injected at run time (`docker run -e ...` / your orchestrator's secret
# mechanism / an env file outside version control), never baked into this
# image. See docs/DEPLOYMENT.md's "Secrets" section for the full list.
#
# --workers 1: this application must run as exactly one process per
# model-hosting instance -- see docs/DEPLOYMENT.md section 5 for why a
# second worker would load a second full copy of the model and hold an
# independent generation lock. Not a placeholder.
#
# --proxy-headers with no --forwarded-allow-ips override: uvicorn's own
# default (trust only 127.0.0.1) is deliberately left as-is here rather
# than guessed at -- a reverse proxy running in a *different* container/
# host has a different IP depending on your deployment's own networking,
# which this Dockerfile cannot know. If your proxy isn't reachable as
# 127.0.0.1 from inside this container, override the command (e.g. in
# docker-compose.yml / your orchestrator's pod spec) to add
# --forwarded-allow-ips=<proxy IP or CIDR> -- never '*'. See
# docs/DEPLOYMENT.md's reverse-proxy section.
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
