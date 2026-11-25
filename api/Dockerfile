# api/Dockerfile
#
# Image for the Vehicle Telematics API. The build context is api/ itself,
# so `docker build` runs from inside api/ (or `docker compose up` from
# api/docker-compose.yml, which points its build context here).
#
# DATABASE_URL and JWT_SECRET are set at runtime (compose / environment),
# never baked into the image: the defaults in app/database.py and
# app/auth.py are local-dev fallbacks only.

FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
