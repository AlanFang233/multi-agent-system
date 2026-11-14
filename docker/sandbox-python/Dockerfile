FROM python:3.12-slim

# Build-time dependency download is allowed; runtime containers run without network.
RUN python -m pip install --no-cache-dir 'pytest==8.4.2'

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
