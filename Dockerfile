# Runs pipeverify as an MCP server over stdio (used by Glama and other MCP hosts).
FROM python:3.12-slim
RUN pip install --no-cache-dir "pipeverify>=0.3.0"
WORKDIR /data
ENTRYPOINT ["pipeverify", "mcp"]
