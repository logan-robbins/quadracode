"""
Message list component for displaying chat history.
"""

from typing import Any

import streamlit as st

from quadracode_contracts import HUMAN_RECIPIENT
from quadracode_ui.utils.message_utils import format_timestamp


def render_message_list(history: list[dict[str, Any]], show_payload: bool = False) -> None:
    """
    Renders a list of chat messages with enhanced styling.

    Args:
        history: List of message dictionaries with 'role', 'content', etc.
        show_payload: Whether to show expandable payload sections.
    """
    for item in history:
        role = item.get("role", "assistant")
        content = item.get("content", "")
        sender = item.get("sender", "")
        display_role = "user" if role in {"human", "user"} else "assistant"
        
        with st.chat_message(display_role):
            # Color-coded sender badge
            if sender:
                if sender in {"human", "human_clone"}:
                    badge_color = "#1E88E5"  # Blue for human/human_clone
                    icon = "👤" if sender == "human" else "🤖"
                elif sender == "orchestrator":
                    badge_color = "#7B1FA2"  # Purple for orchestrator
                    icon = "🎯"
                elif sender.startswith("agent"):
                    badge_color = "#43A047"  # Green for agents
                    icon = "🔧"
                else:
                    badge_color = "#757575"  # Gray for unknown
                    icon = "⚪"
                
                # Styled sender badge
                st.markdown(
                    f'<div style="display: inline-block; background-color: {badge_color}; '
                    f'color: white; padding: 2px 8px; border-radius: 12px; '
                    f'font-size: 12px; margin-bottom: 8px;">'
                    f'{icon} {sender}</div>',
                    unsafe_allow_html=True,
                )
            
            # Show timestamp if available
            timestamp = item.get("timestamp")
            if timestamp:
                st.caption(f"*{format_timestamp(timestamp)}*")
            
            # Render main content with markdown
            if content:
                st.markdown(content)
            else:
                st.caption("*(no content)*")
            
            # Show expandable trace if available
            trace = item.get("trace")
            if trace:
                with st.expander("📋 View Trace", expanded=False):
                    st.json(trace)
            
            # Show expandable payload if enabled
            if show_payload:
                # Collect payload info
                payload_info = {}
                if ticket_id := item.get("ticket_id"):
                    payload_info["ticket_id"] = ticket_id
                if sender:
                    payload_info["sender"] = sender
                if timestamp:
                    payload_info["timestamp"] = timestamp
                
                if payload_info:
                    with st.expander("🔍 View Payload", expanded=False):
                        st.json(payload_info)


def render_message_input(
    placeholder: str = "Type your message...",
    disabled: bool = False,
    key: str = "message_input",
) -> str | None:
    """
    Renders a message input box and returns the submitted message.

    Args:
        placeholder: Placeholder text for the input.
        disabled: Whether the input should be disabled.
        key: Unique key for the input widget.

    Returns:
        The submitted message text, or None if not submitted.
    """
    return st.chat_input(placeholder, disabled=disabled, key=key)


def render_message_card(
    message_dict: dict[str, Any],
    show_full: bool = False,
) -> None:
    """
    Renders a single message as a card (for mailbox monitor).

    Args:
        message_dict: Dictionary containing message fields.
        show_full: Whether to show full message content.
    """
    cols = st.columns([1, 2, 2, 3])
    
    with cols[0]:
        st.caption(format_timestamp(message_dict.get("timestamp", "")))
    
    with cols[1]:
        sender = message_dict.get("sender", "unknown")
        # Color code by sender type
        if sender in {"human", "human_clone"}:
            st.markdown(f"🔵 **{sender}**")
        elif sender == "orchestrator":
            st.markdown(f"🟣 **{sender}**")
        elif sender.startswith("agent"):
            st.markdown(f"🟢 **{sender}**")
        else:
            st.markdown(f"⚪ **{sender}**")
    
    with cols[2]:
        recipient = message_dict.get("recipient", "unknown")
        st.caption(f"→ {recipient}")
    
    with cols[3]:
        message_text = message_dict.get("message", "")
        preview = message_text[:80]
        if len(message_text) > 80:
            preview += "..."
        st.text(preview)
    
    if show_full:
        with st.expander("Full Message", expanded=False):
            st.markdown(f"**Message:**\n\n{message_text}")
            
            payload = message_dict.get("payload")
            if payload:
                st.markdown("**Payload:**")
                st.json(payload)
            
            msg_id = message_dict.get("id")
            if msg_id:
                st.caption(f"Message ID: `{msg_id}`")
            
            mailbox = message_dict.get("mailbox")
            if mailbox:
                st.caption(f"Mailbox: `{mailbox}`")


