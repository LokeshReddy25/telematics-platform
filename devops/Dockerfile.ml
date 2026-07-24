# devops/Dockerfile.ml
#
# Containerises the ML training step (Phase 4/9). On container start, it
# trains the risk model IF AND ONLY IF ml/models/risk_model.pkl doesn't
# already exist -- this lets the same image be used both for a one-off
# "train the model" job and for repeated container restarts without
# needlessly retraining (and getting slightly different random results)
# every time.

FROM python:3.11-slim

WORKDIR /app

COPY ml/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY ml/ ./

# Entry script: train only if the model artifact is missing, then hand
# control to whatever CMD is passed (default: just report success and idle
# so `docker run` output is inspectable).
RUN printf '#!/bin/sh\nset -e\nif [ ! -f "/app/models/risk_model.pkl" ]; then\n  echo "No existing model found, training..."\n  python train_model.py\nelse\n  echo "Model already exists at /app/models/risk_model.pkl, skipping training."\nfi\nexec "$@"\n' > /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-c", "print('ML training container ready. risk_model.pkl is available under /app/models/.')"]
