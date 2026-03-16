FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      make \
      curl \
      ca-certificates \
      wabt \
 && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /src
CMD ["make"]
