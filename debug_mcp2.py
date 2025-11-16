#!/usr/bin/env python3
"""Debug script to test MCP initialization with more details."""
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Enable httpx logging
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)

async def test_mcp():
    logger.info("Environment variables:")
    logger.info(f"  MCP_REDIS_SERVER_URL={os.environ.get('MCP_REDIS_SERVER_URL')}")
    logger.info(f"  MCP_REDIS_TRANSPORT={os.environ.get('MCP_REDIS_TRANSPORT')}")
    logger.info(f"  SHARED_PATH={os.environ.get('SHARED_PATH')}")
    logger.info(f"  PERPLEXITY_API_KEY={'SET' if os.environ.get('PERPLEXITY_API_KEY') else 'NOT SET'}")
    
    try:
        logger.info("Step 1: Building MCP server config...")
        from quadracode_runtime.tools.mcp_loader import _build_server_config
        config = _build_server_config()
        logger.info(f"Step 1: Config built with {len(config)} servers: {list(config.keys())}")
        for name, server_config in config.items():
            logger.info(f"  - {name}: {server_config}")
        
        logger.info("Step 2: Creating MCP client...")
        from langchain_mcp_adapters.client import MultiServerMCPClient
        client = MultiServerMCPClient(config)
        logger.info("Step 2: Client created")
        
        logger.info("Step 3: Getting tools...")
        tools = await asyncio.wait_for(client.get_tools(), timeout=60.0)
        logger.info(f"Step 3: Got {len(tools)} tools")
        
        for tool in tools[:10]:  # Show first 10
            logger.info(f"  - {tool.name}")
        
        logger.info("SUCCESS!")
        return 0
    except Exception as e:
        logger.error(f"FAILED: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(test_mcp()))

