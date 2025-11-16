#!/usr/bin/env python3
"""Debug script to test MCP initialization."""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

async def test_mcp():
    logger.info("Starting MCP initialization test...")
    
    try:
        logger.info("Step 1: Importing quadracode_runtime.tools.mcp_loader...")
        from quadracode_runtime.tools.mcp_loader import aget_mcp_tools
        logger.info("Step 1: Import successful")
        
        logger.info("Step 2: Calling aget_mcp_tools()...")
        tools = await asyncio.wait_for(aget_mcp_tools(), timeout=30.0)
        logger.info(f"Step 2: Got {len(tools)} tools")
        
        for tool in tools:
            logger.info(f"  - {tool.name}")
        
        logger.info("MCP initialization test PASSED")
        return 0
    except asyncio.TimeoutError:
        logger.error("MCP initialization timed out after 30 seconds")
        return 1
    except Exception as e:
        logger.error(f"MCP initialization failed: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(test_mcp()))

