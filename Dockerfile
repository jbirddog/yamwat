FROM python:3.14-slim-bookworm

WORKDIR /src

RUN apt-get update && apt-get install -y --no-install-recommends \
      make \
      wabt \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements_test.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements_test.txt

CMD ["true"]
