FROM python:3.13-slim

COPY . /app
WORKDIR /app

RUN pip install -r requirements.txt

HEALTHCHECK CMD ps aux | grep python | grep -v grep > /dev/null || exit 1

CMD ["python", "-u", "/app/main.py"]