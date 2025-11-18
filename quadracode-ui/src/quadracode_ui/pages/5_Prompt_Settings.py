"""
Streamlit page for configuring LLM prompt templates.

This page provides an interactive UI for viewing and editing all prompt templates
used in the context engine and related components. Changes can be saved and applied
to the runtime configuration.
"""

import json
import streamlit as st
from pathlib import Path
from typing import Dict, Any
import yaml

from quadracode_ui.utils.config_sync import get_config_sync
from quadracode_ui.utils.redis_client import get_redis_client

# Import configuration types (these would be shared with runtime)
try:
    from quadracode_contracts.config import PromptTemplates
except ImportError:
    # Fallback if contracts not available
    class PromptTemplates:
        def __init__(self):
            pass


st.set_page_config(
    page_title="Prompt Settings - Quadracode",
    page_icon="⚙️",
    layout="wide"
)

st.title("⚙️ Prompt Template Configuration")
st.markdown("""
Configure the prompt templates used by the context engine and other LLM-based components.
These prompts control how the system manages context, performs compression, and makes decisions.
""")

# Initialize Redis client and config sync
redis_client = get_redis_client()
config_sync = get_config_sync(redis_client)

# Initialize session state for prompt templates
if "prompt_templates" not in st.session_state:
    # Load templates from Redis or file
    st.session_state.prompt_templates = config_sync.load_prompts()
    st.session_state.config_version = config_sync.get_current_version()

# Check for updates from other sources
updated_config = config_sync.watch_for_updates()
if updated_config:
    st.session_state.prompt_templates = updated_config
    st.session_state.config_version = config_sync.get_current_version()
    st.info("Configuration updated from external source")
    st.rerun()

# Tabs for different prompt categories
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🎯 Governor", 
    "📝 Reducer", 
    "🔄 Compression", 
    "🌐 Domains",
    "📤 Export/Import"
])

# Governor Prompts Tab
with tab1:
    st.header("Context Governor Prompts")
    st.markdown("Configure prompts for the context governor that manages segment operations.")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("System Prompt")
        governor_system = st.text_area(
            "Governor system prompt",
            value=st.session_state.prompt_templates["governor"]["system_prompt"],
            height=150,
            key="governor_system_prompt",
            help="The base system prompt that defines the governor's role"
        )
        
    with col2:
        st.subheader("Instructions Template")
        governor_instructions = st.text_area(
            "Governor instructions",
            value=st.session_state.prompt_templates["governor"]["instructions"],
            height=150,
            key="governor_instructions",
            help="Instructions for how the governor should process context"
        )
    
    st.subheader("Governor Decision Types")
    st.info("""
    **Available decisions:**
    - `retain` - Keep the segment as-is
    - `compress` - Reduce size while preserving meaning
    - `summarize` - Create a brief summary
    - `isolate` - Reduce priority
    - `externalize` - Move to external storage
    - `discard` - Remove from context
    """)
    
    if st.button("Save Governor Settings", key="save_governor"):
        st.session_state.prompt_templates["governor"]["system_prompt"] = governor_system
        st.session_state.prompt_templates["governor"]["instructions"] = governor_instructions
        st.success("Governor prompts updated!")

# Reducer Prompts Tab
with tab2:
    st.header("Context Reducer Prompts")
    st.markdown("Configure prompts for context reduction and summarization.")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("System Prompt")
        reducer_system = st.text_area(
            "Reducer system prompt",
            value=st.session_state.prompt_templates["reducer"]["system_prompt"],
            height=120,
            key="reducer_system_prompt"
        )
        
        st.subheader("Chunk Processing Prompt")
        reducer_chunk = st.text_area(
            "Chunk summarization prompt",
            value=st.session_state.prompt_templates["reducer"]["chunk_prompt"],
            height=120,
            key="reducer_chunk_prompt",
            help="Template for summarizing individual chunks. Supports {target_tokens} and {chunk} variables."
        )
    
    with col2:
        st.subheader("Combine Prompt")
        reducer_combine = st.text_area(
            "Combine summaries prompt",
            value=st.session_state.prompt_templates["reducer"]["combine_prompt"],
            height=120,
            key="reducer_combine_prompt",
            help="Template for combining multiple summaries. Supports {combined} variable."
        )
        
        st.subheader("Focus Clause Template")
        focus_clause = st.text_input(
            "Focus clause (when focus is specified)",
            value=" Focus on {focus}.",
            key="focus_clause",
            help="Added when a specific focus is provided. Supports {focus} variable."
        )
    
    if st.button("Save Reducer Settings", key="save_reducer"):
        st.session_state.prompt_templates["reducer"]["system_prompt"] = reducer_system
        st.session_state.prompt_templates["reducer"]["chunk_prompt"] = reducer_chunk
        st.session_state.prompt_templates["reducer"]["combine_prompt"] = reducer_combine
        st.success("Reducer prompts updated!")

# Compression Profiles Tab
with tab3:
    st.header("Compression Profiles")
    st.markdown("Configure compression profiles that control how aggressively content is reduced.")
    
    # Select profile to edit
    profile_names = list(st.session_state.prompt_templates["compression_profiles"].keys())
    selected_profile = st.selectbox("Select profile to edit", profile_names)
    
    if selected_profile:
        profile = st.session_state.prompt_templates["compression_profiles"][selected_profile]
        
        col1, col2, col3 = st.columns([1, 1, 1])
        
        with col1:
            summary_ratio = st.slider(
                "Summary Ratio",
                min_value=0.1,
                max_value=1.0,
                value=profile.get("summary_ratio", 0.5),
                step=0.1,
                key=f"ratio_{selected_profile}",
                help="Target ratio of output tokens to input tokens"
            )
        
        with col2:
            preserve_detail = st.checkbox(
                "Preserve Detail",
                value=profile.get("preserve_detail", True),
                key=f"detail_{selected_profile}",
                help="Whether to preserve technical details and exact values"
            )
        
        with col3:
            prioritize_recent = st.checkbox(
                "Prioritize Recent",
                value=profile.get("prioritize_recent", False),
                key=f"recent_{selected_profile}",
                help="Give more weight to recent information"
            )
            
            keep_structure = st.checkbox(
                "Keep Structure",
                value=profile.get("keep_structure", False),
                key=f"structure_{selected_profile}",
                help="Maintain original document structure"
            )
        
        if st.button(f"Update {selected_profile} Profile", key=f"save_profile_{selected_profile}"):
            st.session_state.prompt_templates["compression_profiles"][selected_profile] = {
                "summary_ratio": summary_ratio,
                "preserve_detail": preserve_detail,
                "prioritize_recent": prioritize_recent,
                "keep_structure": keep_structure
            }
            st.success(f"{selected_profile} profile updated!")
    
    # Add new profile
    st.divider()
    st.subheader("Add New Profile")
    new_profile_name = st.text_input("Profile name", key="new_profile_name")
    if st.button("Add Profile") and new_profile_name:
        if new_profile_name not in st.session_state.prompt_templates["compression_profiles"]:
            st.session_state.prompt_templates["compression_profiles"][new_profile_name] = {
                "summary_ratio": 0.5,
                "preserve_detail": True,
                "prioritize_recent": False,
                "keep_structure": False
            }
            st.success(f"Added new profile: {new_profile_name}")
            st.rerun()
        else:
            st.error("Profile already exists!")

# Domain Templates Tab
with tab4:
    st.header("Domain-Specific Templates")
    st.markdown("Configure how prompts are adapted for different content domains.")
    
    # Default domains
    if "domain_templates" not in st.session_state.prompt_templates:
        st.session_state.prompt_templates["domain_templates"] = {
            "code": {
                "focus": "function signatures, logic flow, and dependencies",
                "summary_style": "preserve exact syntax and structure",
                "priority": "implementation details and error handling"
            },
            "documentation": {
                "focus": "key concepts, examples, and API references",
                "summary_style": "maintain hierarchical structure",
                "priority": "usage patterns and constraints"
            },
            "test_results": {
                "focus": "failures, error messages, and stack traces",
                "summary_style": "structured with pass/fail statistics",
                "priority": "failing tests and root causes"
            }
        }
    
    domain_names = list(st.session_state.prompt_templates.get("domain_templates", {}).keys())
    selected_domain = st.selectbox("Select domain to edit", domain_names)
    
    if selected_domain:
        domain = st.session_state.prompt_templates["domain_templates"][selected_domain]
        
        focus = st.text_input(
            f"Focus areas for {selected_domain}",
            value=domain.get("focus", ""),
            key=f"focus_{selected_domain}"
        )
        
        summary_style = st.text_input(
            f"Summary style for {selected_domain}",
            value=domain.get("summary_style", ""),
            key=f"style_{selected_domain}"
        )
        
        priority = st.text_input(
            f"Priority elements for {selected_domain}",
            value=domain.get("priority", ""),
            key=f"priority_{selected_domain}"
        )
        
        if st.button(f"Update {selected_domain} Domain", key=f"save_domain_{selected_domain}"):
            st.session_state.prompt_templates["domain_templates"][selected_domain] = {
                "focus": focus,
                "summary_style": summary_style,
                "priority": priority
            }
            st.success(f"{selected_domain} domain updated!")

# Export/Import Tab
with tab5:
    st.header("Export/Import Configuration")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Export Current Configuration")
        
        export_format = st.radio("Export format", ["JSON", "YAML"])
        
        if st.button("Generate Export"):
            if export_format == "JSON":
                export_data = json.dumps(st.session_state.prompt_templates, indent=2)
                st.download_button(
                    "Download JSON",
                    data=export_data,
                    file_name="prompt_templates.json",
                    mime="application/json"
                )
            else:
                export_data = yaml.dump(st.session_state.prompt_templates, default_flow_style=False)
                st.download_button(
                    "Download YAML",
                    data=export_data,
                    file_name="prompt_templates.yaml",
                    mime="text/yaml"
                )
            
            st.code(export_data, language=export_format.lower())
    
    with col2:
        st.subheader("Import Configuration")
        
        uploaded_file = st.file_uploader(
            "Choose a configuration file",
            type=['json', 'yaml', 'yml']
        )
        
        if uploaded_file is not None:
            file_contents = uploaded_file.read().decode()
            
            try:
                if uploaded_file.name.endswith('.json'):
                    imported_config = json.loads(file_contents)
                else:
                    imported_config = yaml.safe_load(file_contents)
                
                st.json(imported_config)
                
                if st.button("Apply Imported Configuration"):
                    st.session_state.prompt_templates.update(imported_config)
                    st.success("Configuration imported successfully!")
                    st.rerun()
                    
            except Exception as e:
                st.error(f"Error parsing file: {e}")

# Sidebar with quick actions
with st.sidebar:
    st.header("Quick Actions")
    
    # Current compression profile
    st.subheader("Active Compression Profile")
    active_profile = st.selectbox(
        "Select active profile",
        list(st.session_state.prompt_templates.get("compression_profiles", {}).keys()),
        key="active_compression_profile"
    )
    
    # Context pressure simulation
    st.subheader("Context Pressure")
    context_ratio = st.slider(
        "Simulated context usage",
        0.0, 1.0, 0.5,
        help="Simulate different context pressure levels"
    )
    
    if context_ratio < 0.5:
        pressure = "Low"
        modifier = "Be thorough and preserve detail where valuable."
    elif context_ratio < 0.75:
        pressure = "Medium"
        modifier = "Balance detail with conciseness."
    elif context_ratio < 0.9:
        pressure = "High"
        modifier = "Aggressively compress while keeping essential facts."
    else:
        pressure = "Critical"
        modifier = "Maximum compression - only the most critical information."
    
    st.info(f"**Pressure Level:** {pressure}")
    st.caption(modifier)
    
    st.divider()
    
    # Reset to defaults
    if st.button("Reset to Defaults", type="secondary"):
        if st.button("Confirm Reset"):
            st.session_state.clear()
            st.rerun()
    
    # Save and apply to runtime
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("💾 Save to Runtime", type="primary", use_container_width=True):
            if config_sync.save_prompts(st.session_state.prompt_templates):
                st.success("✅ Saved to runtime and persistent storage")
                st.session_state.config_version = config_sync.get_current_version()
                
                # Notify runtime to reload
                if config_sync.publish_reload_signal():
                    st.info("🔄 Runtime notified to reload configuration")
            else:
                st.error("Failed to save configuration")
    
    with col2:
        if st.button("📥 Reload from Runtime", use_container_width=True):
            st.session_state.prompt_templates = config_sync.load_prompts()
            st.session_state.config_version = config_sync.get_current_version()
            st.success("Reloaded configuration from runtime")
            st.rerun()
    
    # Show current version
    version = st.session_state.get("config_version", 0)
    st.caption(f"Configuration version: {version}")

# Footer with instructions
st.divider()
st.caption("""
**How to use:**
1. Edit prompts in each category tab
2. Test with different compression profiles
3. Export your configuration for backup
4. Import configurations to share settings

**Variables:** Use `{variable_name}` syntax in templates for dynamic substitution.
""")
