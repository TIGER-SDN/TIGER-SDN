FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e . pytest

COPY . .

CMD ["bash"]
