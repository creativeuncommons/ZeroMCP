# ZeroMCP Standalone Docker Container (Python 3.8+ stdlib, zero external dependencies)
FROM python:3.12-alpine

WORKDIR /app
COPY zeromcp-py/zeromcp.py /usr/local/bin/zeromcp
RUN chmod +x /usr/local/bin/zeromcp

# Run as non-root user (nobody:nobody)
USER 65534:65534

# Must be run interactively (-i) so standard input remains open
ENTRYPOINT ["/usr/local/bin/zeromcp"]
