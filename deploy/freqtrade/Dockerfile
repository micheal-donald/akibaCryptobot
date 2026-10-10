FROM freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae
COPY --chown=ftuser:ftuser deploy/freqtrade /app/deploy/freqtrade
WORKDIR /app
ENV PYTHONPATH=/app
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
ENTRYPOINT ["python", "/app/deploy/freqtrade/start.py"]
