FROM gcc:14-bookworm AS optimizer
WORKDIR /src
COPY optimizer.cpp .
RUN mkdir /artifacts && g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -static optimizer.cpp -o /artifacts/optimizer

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p /app/instance && chown 10001:10001 /app/instance
COPY --from=optimizer /artifacts/optimizer ./build/optimizer
COPY app.py history.py matching.py mcp_server.py planner.py suppliers.py workspace.py widget.html ./
COPY data ./data
COPY web ./web
USER 10001:10001
CMD ["python", "mcp_server.py"]

FROM runtime AS test
COPY test_partbasket.py test_workspace.py ./
RUN --network=none python -m unittest -v test_partbasket test_workspace

FROM runtime AS release
