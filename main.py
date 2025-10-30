# main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import asyncio, subprocess, os
import logging
import json
from typing import Optional

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Add 'stdio' command to start the MCP server in JSON-RPC mode
GITHUB_MCP_CMD = ["./mcp-server", "stdio"]

# Use environment variable for GitHub token
os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = os.getenv("GITHUB_PAT", "")

# Global MCP process
mcp_process: Optional[asyncio.subprocess.Process] = None
mcp_lock = asyncio.Lock()


async def get_or_create_mcp_process():
    """Get existing MCP process or create a new one"""
    global mcp_process

    async with mcp_lock:
        if mcp_process is None or mcp_process.returncode is not None:
            try:
                logger.info("Starting new MCP process")
                mcp_process = await asyncio.create_subprocess_exec(
                    *GITHUB_MCP_CMD,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                # Wait a moment for the process to start
                await asyncio.sleep(0.1)

                if mcp_process.returncode is not None:
                    logger.error("MCP process failed to start")
                    mcp_process = None
                    return None

            except Exception as e:
                logger.error(f"Failed to start MCP process: {e}")
                mcp_process = None
                return None

        return mcp_process


async def send_mcp_request(request_data: dict) -> Optional[dict]:
    """Send a request to the MCP server and get response"""
    proc = await get_or_create_mcp_process()
    if proc is None:
        raise HTTPException(status_code=500, detail="Failed to start MCP server")

    try:
        # Convert request to JSON and add newline (required for stdio mode)
        request_json = json.dumps(request_data) + "\n"
        request_bytes = request_json.encode("utf-8")

        logger.info(f"Sending to MCP: {request_json.strip()}")

        # Send the request
        proc.stdin.write(request_bytes)
        await proc.stdin.drain()

        # Check if this is a notification (no response expected)
        is_notification = "method" in request_data and (
            request_data["method"].startswith("notifications/")
            or "id" not in request_data
        )

        if is_notification:
            logger.info("Notification sent, no response expected")
            return None

        # Read the response line by line until we get a complete JSON-RPC response
        try:
            response_line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for MCP response")
            raise HTTPException(
                status_code=500, detail="Timeout waiting for MCP server response"
            )

        if not response_line:
            raise HTTPException(status_code=500, detail="No response from MCP server")

        response_text = response_line.decode("utf-8").strip()
        logger.info(f"Received from MCP: {response_text}")

        # Parse the JSON response
        try:
            response_data = json.loads(response_text)
            return response_data
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}")
            logger.error(f"Raw response: {response_text}")
            raise HTTPException(
                status_code=500, detail="Invalid JSON response from MCP server"
            )

    except Exception as e:
        logger.error(f"Error communicating with MCP server: {e}")
        # Reset the process if there's an error
        global mcp_process
        mcp_process = None
        raise HTTPException(status_code=500, detail=f"MCP communication error: {e}")


@app.post("/mcp")
async def proxy(request: Request):
    try:
        body = await request.body()
        logger.info(f"Received request with body length: {len(body)}")

        # Validate that the request body is valid JSON
        try:
            request_data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in request body: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON in request body")

        # Send request to MCP server and get response
        response_data = await send_mcp_request(request_data)

        # For notifications, return empty success response
        if response_data is None:
            return JSONResponse(content={})

        return JSONResponse(content=response_data)

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error in proxy: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "GitHub MCP Server Proxy", "endpoint": "/mcp"}


@app.get("/test-mcp")
async def test_mcp():
    """Test MCP server with a simple request"""
    try:
        # Create a simple MCP initialization request
        test_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        response = await send_mcp_request(test_request)
        return {"success": True, "request": test_request, "response": response}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/test-tools")
async def test_tools():
    """Test getting tools from MCP server"""
    try:
        # First initialize
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        init_response = await send_mcp_request(init_request)

        # Send initialized notification
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}

        await send_mcp_request(notification)

        # Now get tools
        tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

        tools_response = await send_mcp_request(tools_request)

        return {
            "success": True,
            "initialization": init_response,
            "tools": tools_response,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up MCP process on shutdown"""
    global mcp_process
    if mcp_process is not None:
        logger.info("Shutting down MCP process")
        mcp_process.terminate()
        await mcp_process.wait()
