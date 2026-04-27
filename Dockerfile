
#Stage 1: Build MCP binary with correct Go version
FROM golang:1.24-alpine AS builder

# Install git and build tools
RUN apk add --no-cache git

# Set working directory
WORKDIR /src

# Copy all source files (your folder structure)
COPY . .

# Build the MCP server binary from cmd/github-mcp-server
RUN go build -o /mcp-server ./cmd/github-mcp-server

# Stage 2: Python wrapper for HTTP
FROM python:3.14.4-slim

# Install FastAPI and Uvicorn
RUN pip install fastapi uvicorn

WORKDIR /app

# Copy the compiled MCP binary from builder stage
COPY --from=builder /mcp-server ./mcp-server

# Copy your FastAPI wrapper
COPY main.py ./main.py

# Make binary executable
RUN chmod +x ./mcp-server

# Expose FastAPI port
EXPOSE 8000

# Start FastAPI app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
