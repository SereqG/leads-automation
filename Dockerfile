FROM python:3.12-slim

WORKDIR /backend

# Corporate network performs TLS inspection (FortiGate); trust its root CA
# at the OS level so both build-time (pip) and runtime HTTPS calls verify.
COPY docker/certs/corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt
RUN update-ca-certificates

RUN adduser --system --no-create-home --uid 10001 appuser

COPY requirements.txt /backend/requirements.txt
RUN pip install --no-cache-dir -r /backend/requirements.txt

COPY backend/ /backend/

USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
