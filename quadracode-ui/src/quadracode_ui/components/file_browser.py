"""
File browser component for workspace file exploration.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.util import ClassNotFound

from quadracode_ui.utils.workspace_utils import (
    get_file_icon,
    get_file_metadata,
    list_workspace_files,
    read_workspace_file,
)


def build_file_tree_structure(files: list[str]) -> dict[str, Any]:
    """
    Builds a hierarchical tree structure from a flat list of file paths.
    
    Args:
        files: List of file paths.
    
    Returns:
        A nested dictionary representing the folder structure.
    """
    tree: dict[str, Any] = {"_files": [], "_dirs": {}}
    
    for file_path in files:
        path = Path(file_path)
        parts = path.parts
        
        # Navigate/create directory structure
        current_level = tree
        for i, part in enumerate(parts[:-1]):  # All but the last (filename)
            if part not in current_level["_dirs"]:
                current_level["_dirs"][part] = {"_files": [], "_dirs": {}}
            current_level = current_level["_dirs"][part]
        
        # Add file to the appropriate directory level
        current_level["_files"].append(file_path)
    
    return tree


def render_hierarchical_tree(
    workspace_id: str,
    tree: dict[str, Any],
    path_prefix: str = "",
    level: int = 0,
) -> str | None:
    """
    Recursively renders a hierarchical file tree with expandable folders.
    
    Args:
        workspace_id: The workspace ID.
        tree: The tree structure dictionary.
        path_prefix: Current path prefix for building full paths.
        level: Current nesting level for visual indentation.
    
    Returns:
        Selected file path or None.
    """
    selected_file = None
    indent = "  " * level
    
    # Render directories (folders)
    for dir_name in sorted(tree["_dirs"].keys()):
        dir_path = f"{path_prefix}/{dir_name}" if path_prefix else dir_name
        
        with st.expander(f"📁 {dir_name}", expanded=level < 2):
            # Recursively render subdirectories and files
            result = render_hierarchical_tree(
                workspace_id,
                tree["_dirs"][dir_name],
                dir_path,
                level + 1,
            )
            if result:
                selected_file = result
    
    # Render files in current directory
    for file_path in sorted(tree["_files"]):
        path = Path(file_path)
        icon = get_file_icon(file_path)
        
        # Get file metadata
        metadata = get_file_metadata(workspace_id, file_path)
        size_kb = metadata.get("size", 0) / 1024
        size_display = f"{size_kb:.1f} KB" if size_kb > 0 else "0 KB"
        
        # Create clickable file button with metadata
        col1, col2 = st.columns([4, 1])
        with col1:
            if st.button(
                f"{icon} {path.name}",
                key=f"file_btn_{workspace_id}_{file_path}",
                use_container_width=True,
            ):
                selected_file = file_path
        with col2:
            st.caption(size_display)
    
    return selected_file


def render_file_tree(workspace_id: str) -> str | None:
    """
    Renders a file tree browser for a workspace with hierarchical structure.

    Args:
        workspace_id: The workspace ID to browse.

    Returns:
        The selected file path, or None if no selection.
    """
    # Initialize selected file in session state
    session_key = f"selected_file_{workspace_id}"
    if session_key not in st.session_state:
        st.session_state[session_key] = None
    
    files = list_workspace_files(workspace_id)
    
    if not files:
        st.info("No files found in workspace")
        return None
    
    # Build hierarchical structure
    tree_structure = build_file_tree_structure(files)
    
    # Render hierarchical tree
    selected = render_hierarchical_tree(workspace_id, tree_structure)
    
    # Update session state if a file was selected
    if selected:
        st.session_state[session_key] = selected
    
    return st.session_state[session_key]


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
    
    # Show detailed file metadata
    path = Path(file_path)
    metadata = get_file_metadata(workspace_id, file_path)
    
    # Metadata display
    meta_cols = st.columns(4)
    
    with meta_cols[0]:
        st.caption("**File:**")
        st.code(path.name, language=None)
    
    with meta_cols[1]:
        size_bytes = metadata.get("size", len(content))
        if size_bytes < 1024:
            size_display = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            size_display = f"{size_bytes / 1024:.1f} KB"
        else:
            size_display = f"{size_bytes / (1024 * 1024):.1f} MB"
        
        st.caption("**Size:**")
        st.code(size_display, language=None)
    
    with meta_cols[2]:
        st.caption("**Type:**")
        st.code(path.suffix or 'no ext', language=None)
    
    with meta_cols[3]:
        modified_time = metadata.get("modified_time", "")
        if modified_time:
            try:
                dt = datetime.fromisoformat(modified_time.replace("Z", "+00:00"))
                time_display = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, AttributeError):
                time_display = "unknown"
        else:
            time_display = "unknown"
        
        st.caption("**Modified:**")
        st.code(time_display, language=None)
    
    # Full path display
    with st.expander("📂 Full Path & Metadata", expanded=False):
        st.code(file_path, language=None)
        st.json(metadata)
    
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


