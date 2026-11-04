# Idea Machine's pipeline image (install/nas.sh). CPU only: extraction is Claude, embeddings are a small local model.
FROM python:3.13-slim

# tzdata: payloads carry local times, and the container must render them exactly as before a move (im pending).
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.12.2

# Install from a throwaway copy: the host clone's files may be root-only, which the runtime user can't read.
# Dependencies first, from the lock alone, so a code change reuses them and the model below: only a new uv.lock
# (or model revision) downloads torch and the rest again.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /src/
RUN cd /src && uv sync --frozen --no-dev --extra models --no-install-project && rm -rf /root/.cache
ENV PATH=/opt/venv/bin:$PATH

# The embedding model, pinned and baked in so runs are offline and repeatable. refs/main points at the pinned
# commit so loading it by name works offline.
ARG BGE_REVISION=5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
ENV HF_HOME=/models/hf
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-en-v1.5', revision='$BGE_REVISION')" \
    && mkdir -p /models/hf/hub/models--BAAI--bge-small-en-v1.5/refs \
    && printf '%s' "$BGE_REVISION" > /models/hf/hub/models--BAAI--bge-small-en-v1.5/refs/main \
    && HF_HUB_OFFLINE=1 python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5', device='cpu')" \
    && chmod -R a+rX /models
# GLiNER, for names said under policy C (im.names, decision 0008), the same way; it fetches its tokenizer on first load.
ARG GLINER_REVISION=40ec419335d09393f298636f471328b722c6da9e
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('urchade/gliner_medium-v2.1', revision='$GLINER_REVISION')" \
    && mkdir -p /models/hf/hub/models--urchade--gliner_medium-v2.1/refs \
    && printf '%s' "$GLINER_REVISION" > /models/hf/hub/models--urchade--gliner_medium-v2.1/refs/main \
    && python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_medium-v2.1')" \
    && HF_HUB_OFFLINE=1 python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_medium-v2.1')" \
    && chmod -R a+rX /models
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# Then Idea Machine itself, into the same environment.
COPY src /src/src
RUN cd /src && uv sync --frozen --no-dev --extra models --no-editable && rm -rf /src /root/.cache

# Last, so a new commit doesn't rebuild the layers above (a code change rebuilds only the step before).
ARG IM_COMMIT=unknown
ENV IM_COMMIT=$IM_COMMIT
USER 568:568
ENTRYPOINT ["im"]
CMD ["run"]
