FROM python:3.14-slim-bookworm

WORKDIR /src

RUN apt-get update && apt-get install -y --no-install-recommends \
      make \
      wabt \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

CMD ["make"]
