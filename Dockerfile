FROM python:3.12-slim

WORKDIR /backend

COPY requirements.txt /backend/requirements.txt
RUN pip install --no-cache-dir -r /backend/requirements.txt

COPY backend/ /backend/

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
