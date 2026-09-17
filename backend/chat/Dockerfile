# backend/chat/Dockerfile
FROM python:3.12-slim AS build
WORKDIR /app
COPY backend/chat/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
COPY --from=build /install /usr/local
WORKDIR /app
COPY backend/shared/shared ./shared
COPY backend/chat/chat ./chat
RUN useradd -u 10001 -m appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "8000"]
