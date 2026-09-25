# Общий Dockerfile для обоих ботов: сервис выбирается через --build-arg SERVICE=<имя папки>
FROM python:3.13-slim

ARG SERVICE
WORKDIR /app

COPY ${SERVICE}/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ${SERVICE}/ ./

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
