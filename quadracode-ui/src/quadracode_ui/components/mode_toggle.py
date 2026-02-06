"""
Mode toggle component for switching between Human and HumanClone modes.
"""

import streamlit as st

from quadracode_contracts import HUMAN_CLONE_RECIPIENT, HUMAN_RECIPIENT, SUPERVISOR_RECIPIENT
from quadracode_ui.utils.message_utils import active_supervisor, set_supervisor


def render_mode_toggle(chat_id: str | None = None) -> str:
    """
    Renders a mode toggle UI component for Human/HumanClone selection.

    Args:
        chat_id: Optional chat ID to associate the mode with.

    Returns:
        The currently selected mode ('human' or 'supervisor').
    """
    current = active_supervisor()
    
    # Display mode indicator badge
    if current in {HUMAN_CLONE_RECIPIENT, SUPERVISOR_RECIPIENT}:
        st.info("🤖 **Supervisor Mode Active** - Messages sent to orchestrator with autonomous supervisor")
    else:
        st.info("👤 **Human Mode Active** - Direct human supervision")
    
    # Radio button selection
    mode_options = {
        "Human Mode": HUMAN_RECIPIENT,
        "Supervisor Mode (Autonomous)": SUPERVISOR_RECIPIENT,
    }
    
    # Find current selection
    current_label = "Human Mode"
    for label, value in mode_options.items():
        if value == current:
            current_label = label
            break
    
    selected_label = st.radio(
        "Control Mode",
        options=list(mode_options.keys()),
        index=list(mode_options.keys()).index(current_label),
        help=(
            "**Human Mode**: Direct interaction with orchestrator.\n\n"
            "**HumanClone Mode**: Autonomous operation with HumanClone supervisor."
        ),
        key=f"mode_toggle_{chat_id or 'global'}",
    )
    
    selected_value = mode_options[selected_label]
    
    # Check if mode changed
    if selected_value != current:
        # Show warning dialog
        st.warning(
            "⚠️ Switching modes will change how messages are routed. "
            "Existing messages in the chat history remain unchanged.",
            icon="⚠️",
        )
        
        # Update the supervisor
        set_supervisor(selected_value, chat_id)
    
    return "supervisor" if selected_value in {HUMAN_CLONE_RECIPIENT, SUPERVISOR_RECIPIENT} else "human"


def render_mode_status(compact: bool = False) -> None:
    """
    Renders a compact status indicator for the current mode.

    Args:
        compact: If True, renders a compact version suitable for headers.
    """
    current = active_supervisor()
    
    if compact:
        if current in {HUMAN_CLONE_RECIPIENT, SUPERVISOR_RECIPIENT}:
            st.caption("🤖 Supervisor Mode")
        else:
            st.caption("👤 Human Mode")
    else:
        if current in {HUMAN_CLONE_RECIPIENT, SUPERVISOR_RECIPIENT}:
            st.success("🤖 Supervisor Mode Active", icon="🤖")
        else:
            st.info("👤 Human Mode Active", icon="👤")


