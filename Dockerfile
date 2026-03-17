FROM debian:bookworm-slim

WORKDIR /src

RUN apt-get update && apt-get install -y --no-install-recommends \
      make \
      curl \
      ca-certificates \
      wabt \
 && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY yamwat.py .
RUN uv run yamwat.py

CMD ["make"]
