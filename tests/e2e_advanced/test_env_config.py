"""Environment configuration for E2E tests.

Automatically detects whether tests are running inside Docker container
or on the host machine and adjusts connection settings accordingly.
"""

import os
from typing import Dict

def is_running_in_docker() -> bool:
    """Check if we're running inside a Docker container."""
    # Check for Docker environment file
    if os.path.exists('/.dockerenv'):
        return True
    # Check for Docker in cgroup (works on most Linux)
    try:
        with open('/proc/1/cgroup', 'r') as f:
            return 'docker' in f.read()
    except (FileNotFoundError, PermissionError):
        pass
    # Check if we're the test-runner container
    return os.environ.get('HOSTNAME', '').startswith('test-runner')

def get_service_urls() -> Dict[str, str]:
    """Get service URLs based on execution environment.
    
    Returns:
        Dict with service URLs appropriate for the environment.
    """
    if is_running_in_docker():
        # Running inside Docker network - use service names
        return {
            'redis_host': os.environ.get('REDIS_HOST', 'redis'),
            'redis_port': os.environ.get('REDIS_PORT', '6379'),
            'agent_registry_url': os.environ.get('AGENT_REGISTRY_URL', 'http://agent-registry:8090'),
            'mcp_redis_url': os.environ.get('MCP_REDIS_SERVER_URL', 'http://redis-mcp:8000/mcp/'),
        }
    else:
        # Running on host - use localhost with exposed ports
        return {
            'redis_host': os.environ.get('REDIS_HOST', '127.0.0.1'),
            'redis_port': os.environ.get('REDIS_PORT', '6379'),
            'agent_registry_url': os.environ.get('AGENT_REGISTRY_URL', 'http://localhost:8090'),
            'mcp_redis_url': os.environ.get('MCP_REDIS_SERVER_URL', 'http://127.0.0.1:8000/mcp/'),
        }

def get_redis_url() -> str:
    """Get Redis URL for the current environment."""
    config = get_service_urls()
    return f"redis://{config['redis_host']}:{config['redis_port']}/0"

def get_agent_registry_url() -> str:
    """Get Agent Registry URL for the current environment."""
    return get_service_urls()['agent_registry_url']

# Export configuration
SERVICE_URLS = get_service_urls()
REDIS_HOST = SERVICE_URLS['redis_host']
REDIS_PORT = SERVICE_URLS['redis_port']
AGENT_REGISTRY_URL = SERVICE_URLS['agent_registry_url']
MCP_REDIS_URL = SERVICE_URLS['mcp_redis_url']
REDIS_URL = get_redis_url()
