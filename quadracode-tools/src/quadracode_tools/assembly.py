"""A central assembly point for collecting and exposing all available Quadracode tools.

This module serves as the primary entry point for the Quadracode runtime to
discover and register all tools defined within the `quadracode-tools` package. It
imports the individual tool functions from their respective modules and aggregates
them into a single list. This centralized approach simplifies the process of
providing a comprehensive set of capabilities to a LangGraph agent, as the runtime
only needs to call the `get_tools()` function to access the entire toolset.
"""
from __future__ import annotations

from typing import List
from langchain_core.tools import BaseTool

# Local tools
from .tools.python_repl import python_repl
from .tools.bash_shell import bash_shell
from .tools.read_file import read_file
from .tools.write_file import write_file
from .tools.agent_registry import agent_registry_tool
from .tools.agent_management import agent_management_tool
from .tools.autonomous_control import (
    autonomous_checkpoint,
    autonomous_escalate,
    hypothesis_critique,
    request_final_review,
)
from .tools.property_tests import generate_property_tests
from .tools.refinement_ledger import manage_refinement_ledger
from .tools.workspace import (
    workspace_create,
    workspace_exec,
    workspace_copy_to,
    workspace_copy_from,
    workspace_destroy,
    workspace_info,
)
from .tools.test_suite import run_full_test_suite


def get_tools() -> List[BaseTool]:
    """Assembles and returns a list of all standard Quadracode agent tools.

    This function acts as a registry for all the core tools that a Quadracode agent
    can use. By collecting them in one place, it simplifies the process of
    configuring the LangGraph runtime, which requires a list of tools to be
    provided during initialization.

    The returned list includes tools for:
    - Filesystem operations (`read_file`, `write_file`)
    - Shell command execution (`bash_shell`, `python_repl`)
    - Isolated workspace management (`workspace_*`)
    - Agent lifecycle and discovery (`agent_registry_tool`, `agent_management_tool`)
    - Automated testing (`run_full_test_suite`, `generate_property_tests`)
    - Meta-cognitive and autonomous control (`manage_refinement_ledger`, `autonomous_*`)

    Returns:
        A list of `BaseTool` instances ready to be used by a LangGraph agent.
    """
    return [
        # Local tools
        python_repl,
        bash_shell,
        read_file,
        write_file,
        workspace_create,
        workspace_exec,
        workspace_copy_to,
        workspace_copy_from,
        workspace_destroy,
        workspace_info,
        agent_registry_tool,
        agent_management_tool,
        autonomous_checkpoint,
        autonomous_escalate,
        hypothesis_critique,
        request_final_review,
        run_full_test_suite,
        generate_property_tests,
        manage_refinement_ledger,
    ]
