FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Mount your credentials.json (client secrets) and config dir at runtime:
#   docker run -v ~/.config/elko-mail:/root/.config/elko-mail \
#              -v ./credentials.json:/root/.config/elko-mail/credentials.json \
#              elko-mail fetch --email you@gmail.com --headless
VOLUME ["/root/.config/elko-mail"]

ENTRYPOINT ["elko-mail"]
CMD ["--help"]
