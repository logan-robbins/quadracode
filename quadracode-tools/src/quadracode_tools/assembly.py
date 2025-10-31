from __future__ import annotations

from typing import List
from langchain_core.tools import BaseTool

# Local tools
from .tools.python_repl import python_repl
from .tools.bash_shell import bash_shell
from .tools.read_file import read_file
from .tools.write_file import write_file
from .tools.agent_registry import agent_registry_tool


def get_tools() -> List[BaseTool]:
    return [
        # Local tools
        python_repl,
        bash_shell,
        read_file,
        write_file,
        agent_registry_tool,
    ]
