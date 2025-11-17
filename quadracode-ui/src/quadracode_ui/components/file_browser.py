"""
File browser component for workspace file exploration.
"""

from pathlib import Path
from typing import Any

import streamlit as st
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.util import ClassNotFound

from quadracode_ui.utils.workspace_utils import (
    get_file_icon,
    list_workspace_files,
    read_workspace_file,
)


def render_file_tree(workspace_id: str) -> str | None:
    """
    Renders a file tree browser for a workspace.

    Args:
        workspace_id: The workspace ID to browse.

    Returns:
        The selected file path, or None if no selection.
    """
    files = list_workspace_files(workspace_id)
    
    if not files:
        st.info("No files found in workspace")
        return None
    
    # Organize files by directory
    file_tree: dict[str, list[str]] = {}
    for file_path in files:
        path = Path(file_path)
        parent = str(path.parent)
        if parent not in file_tree:
            file_tree[parent] = []
        file_tree[parent].append(file_path)
    
    # Create selectbox with icons
    file_options = []
    for file_path in sorted(files):
        icon = get_file_icon(file_path)
        file_options.append(f"{icon} {file_path}")
    
    if not file_options:
        st.info("No files available")
        return None
    
    selected = st.selectbox(
        "Select file to view",
        options=file_options,
        key=f"file_browser_{workspace_id}",
    )
    
    if selected:
        # Remove icon from selection
        file_path = selected.split(" ", 1)[1] if " " in selected else selected
        return file_path
    
    return None


def render_file_content(
    workspace_id: str,
    file_path: str,
    syntax_highlight: bool = True,
    show_line_numbers: bool = True,
) -> None:
    """
    Renders file content with optional syntax highlighting.

    Args:
        workspace_id: The workspace ID.
        file_path: The full path to the file.
        syntax_highlight: Whether to apply syntax highlighting.
        show_line_numbers: Whether to show line numbers.
    """
    success, content = read_workspace_file(workspace_id, file_path)
    
    if not success:
        st.error(f"Failed to read file: {content}")
        return
    
    # Show file metadata
    path = Path(file_path)
    cols = st.columns(3)
    cols[0].caption(f"**File:** {path.name}")
    cols[1].caption(f"**Size:** {len(content)} bytes")
    cols[2].caption(f"**Type:** {path.suffix or 'no extension'}")
    
    # Determine language for syntax highlighting
    if syntax_highlight:
        try:
            if path.suffix == ".md":
                st.markdown(content)
                return
            
            # Try to get lexer for syntax highlighting
            try:
                lexer = get_lexer_for_filename(file_path)
            except ClassNotFound:
                # Fallback to plain text
                lexer = get_lexer_by_name("text")
            
            formatter = HtmlFormatter(
                style="monokai",
                linenos="inline" if show_line_numbers else False,
                cssclass="source",
            )
            
            highlighted = highlight(content, lexer, formatter)
            
            # Inject CSS for syntax highlighting
            st.markdown(
                f"""
                <style>
                {formatter.get_style_defs('.source')}
                .source {{
                    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
                    font-size: 12px;
                    line-height: 1.5;
                    border-radius: 4px;
                    padding: 10px;
                    overflow-x: auto;
                }}
                </style>
                {highlighted}
                """,
                unsafe_allow_html=True,
            )
        except Exception:
            # Fallback to plain code block
            st.code(content, language="text")
    else:
        st.code(content, language="text")
    
    # Copy to clipboard button
    if st.button("📋 Copy to Clipboard", key=f"copy_{workspace_id}_{file_path}"):
        st.code(content, language="text")
        st.success("Content ready to copy (displayed above)")


def render_file_search(files: list[str], search_term: str = "") -> list[str]:
    """
    Filters files based on a search term.

    Args:
        files: List of file paths.
        search_term: Search term to filter by.

    Returns:
        Filtered list of file paths.
    """
    if not search_term:
        return files
    
    search_lower = search_term.lower()
    return [f for f in files if search_lower in f.lower()]


def render_file_type_filter(files: list[str]) -> list[str]:
    """
    Renders a file type filter UI.

    Args:
        files: List of file paths.

    Returns:
        Filtered list of file paths based on selected types.
    """
    # Extract unique extensions
    extensions = sorted({Path(f).suffix for f in files if Path(f).suffix})
    
    if not extensions:
        return files
    
    selected_ext = st.multiselect(
        "Filter by file type",
        options=extensions,
        default=extensions,
        key="file_type_filter",
    )
    
    if not selected_ext:
        return files
    
    return [f for f in files if Path(f).suffix in selected_ext]


def render_workspace_file_browser(workspace_id: str) -> None:
    """
    Renders a complete file browser interface for a workspace.

    Args:
        workspace_id: The workspace ID to browse.
    """
    st.subheader("Workspace Files")
    
    # Search box
    search = st.text_input(
        "Search files",
        placeholder="Type to search...",
        key=f"file_search_{workspace_id}",
    )
    
    # Load files
    all_files = list_workspace_files(workspace_id)
    
    if not all_files:
        st.info("No files found in workspace")
        return
    
    # Apply search filter
    filtered_files = render_file_search(all_files, search)
    
    if not filtered_files:
        st.warning(f"No files match '{search}'")
        return
    
    # File tree selection
    selected_file = render_file_tree(workspace_id)
    
    if selected_file:
        st.divider()
        render_file_content(workspace_id, selected_file)


