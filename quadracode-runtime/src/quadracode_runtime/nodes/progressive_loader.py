"""
This module implements the `ProgressiveContextLoader`, a component of the context 
engine that is responsible for loading context artifacts on demand.

The `ProgressiveContextLoader` is a key part of the context engine's strategy for 
managing large and complex contexts. Instead of loading all possible context at 
the beginning of a task, this component analyzes the current state to determine 
what context is most likely to be needed next, and then loads only that context. 
This "progressive" approach helps to keep the context window focused and to avoid 
unnecessary overhead.
"""

from __future__ import annotations

import asyncio
import ast
import json
import re
import textwrap
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import ContextEngineConfig
from ..state import ContextEngineState, ContextSegment


class ProgressiveContextLoader:
    """
    Loads context artifacts on demand based on the current signals in the state.

    This class implements the core logic for the progressive loading process. It 
    analyzes the current state to identify the most pressing context needs, and 
    then orchestrates the loading of the corresponding artifacts. It also manages 
    a prefetch queue to proactively load context that is likely to be needed in 
    the near future.

    Attributes:
        config: The configuration for the context engine.
        project_root: The root directory of the project.
        ... and other configuration-derived paths.
    """

    def __init__(self, config: ContextEngineConfig) -> None:
        """
        Initializes the `ProgressiveContextLoader`.

        Args:
            config: The configuration for the context engine.
        """
        self.config = config
        self.project_root = Path(config.project_root).resolve()
        self.documentation_paths = [self.project_root / path for path in config.documentation_paths]
        self.skills_roots = [self.project_root / path for path in config.skills_paths]
        self._skills_cache: Dict[str, Dict[str, Any]] = {}

    async def prepare_context(self, state: ContextEngineState) -> ContextEngineState:
        """
        Prepares the context by analyzing needs and loading required artifacts.

        This is the main public method of the `ProgressiveContextLoader`. It 
        orchestrates the entire process of analyzing context needs, loading 
        the required artifacts, and integrating them into the state.

        Args:
            state: The current state of the context engine.

        Returns:
            The updated state with the newly loaded context.
        """
        needs = await self._analyze_context_needs(state)
        already_loaded = self._get_loaded_context_types(state)
        pending_needs = needs - already_loaded

        for need in sorted(pending_needs, key=self._need_priority, reverse=True):
            if not self._has_capacity(state, need):
                self._enqueue_prefetch(state, need, reason="capacity")
                if need not in state["pending_context"]:
                    state["pending_context"].append(need)
                continue

            segment = await self._load_context(need, state)
            if segment:
                state = await self._integrate_context(state, segment)
                self._remove_pending(state, need)

        self._trim_prefetch_queue(state)

        self._ensure_skills_catalog(state)
        search_terms = self._compile_search_terms(state)
        state = await self._activate_skills(state, search_terms)
        if search_terms:
            search_segment = await self._load_code_search(search_terms)
            if search_segment:
                state = await self._integrate_context(state, search_segment)
                self._remove_pending(state, "code_search_results")

        return state

    async def _analyze_context_needs(self, state: ContextEngineState) -> Set[str]:
        """
        Analyzes the current state to determine the most immediate context needs.
        """
        needs: Set[str] = set()

        if state.get("messages"):
            last_message = state["messages"][-1]
            intent = await self._extract_intent(last_message)
            intent_needs = {
                "code": {"code_context", "file_structure"},
                "debug": {"error_history", "stack_traces"},
                "design": {"architecture_docs", "design_patterns"},
                "test": {"test_suite", "coverage_reports"},
            }
            for key, required in intent_needs.items():
                if key in intent:
                    needs.update(required)

        milestone = state.get("current_milestone")
        if milestone is not None:
            needs.update(self._get_milestone_context_needs(milestone))

        return needs

    def _get_loaded_context_types(self, state: ContextEngineState) -> Set[str]:
        """Returns the set of context types that are already loaded."""
        loaded: Set[str] = set()
        for segment in state.get("context_segments", []):
            segment_type = segment.get("type", "").split(":", 1)[0]
            loaded.add(segment_type)
        return loaded

    async def _load_context(self, context_type: str, state: ContextEngineState) -> Optional[ContextSegment]:
        """
        Dispatches to the appropriate loader function for a given context type.
        """
        loaders = {
            "code_context": self._load_code_context,
            "file_structure": self._load_file_structure,
            "error_history": self._load_error_history,
            "stack_traces": self._load_stack_traces,
            "architecture_docs": self._load_architecture_docs,
            "design_patterns": self._load_design_patterns,
            "test_suite": self._load_test_suite,
            "coverage_reports": self._load_coverage_reports,
        }
        loader = loaders.get(context_type)
        return await loader(state) if loader else None

    async def _integrate_context(self, state: ContextEngineState, segment: Optional[ContextSegment]) -> ContextEngineState:
        """Integrates a new context segment into the state."""
        if not segment:
            return state

        existing_index = next(
            (idx for idx, current in enumerate(state["context_segments"]) if current.get("id") == segment["id"]),
            None,
        )

        if existing_index is not None:
            previous = state["context_segments"][existing_index]
            state["context_window_used"] = max(
                0,
                state.get("context_window_used", 0) - previous.get("token_count", 0),
            )
            state["context_segments"][existing_index] = segment
        else:
            state["context_segments"].append(segment)

        state["context_hierarchy"][segment["type"]] = self._need_priority(segment["type"])
        state["context_window_used"] += segment.get("token_count", 0)
        load_event = {
            "segment_id": segment["id"],
            "type": segment.get("type"),
            "tokens": segment.get("token_count", 0),
            "timestamp": segment.get("timestamp"),
            "replaced": existing_index is not None,
        }
        state.setdefault("recent_loads", []).append(load_event)
        return state

    def _has_capacity(self, state: ContextEngineState, context_type: str) -> bool:
        """
        Checks if there is enough capacity in the context window to load a new 
        context type.
        """
        estimate = self._estimate_context_size(context_type)
        max_tokens = state.get("context_window_max", self.config.context_window_max)
        projected = state.get("context_window_used", 0) + estimate
        return projected <= max_tokens * 0.85

    def _estimate_context_size(self, context_type: str) -> int:
        """Estimates the token size of a given context type."""
        estimates = {
            "code_context": 1500,
            "file_structure": 600,
            "error_history": 800,
            "stack_traces": 700,
            "architecture_docs": 1200,
            "design_patterns": 500,
            "test_suite": 1600,
            "coverage_reports": 900,
            "code_search_results": 900,
            "skill_main": 800,
        }
        return estimates.get(context_type, 500)

    def _need_priority(self, need: str | ContextSegment) -> int:
        """Returns the priority of a given context need."""
        if isinstance(need, dict):
            return need.get("priority", 5)
        priorities = {
            "code_context": 9,
            "file_structure": 7,
            "error_history": 9,
            "stack_traces": 8,
            "architecture_docs": 6,
            "design_patterns": 5,
            "test_suite": 8,
            "coverage_reports": 7,
            "code_search_results": 6,
            "skill_main": 6,
        }
        return priorities.get(str(need), 3)

    async def _extract_intent(self, message: Any) -> Set[str]:
        """Extracts the user's intent from a message."""
        content = self._message_to_text(message)
        lowered = content.lower()
        intent_markers = {
            "code": {"implement", "function", "refactor", "code", "module"},
            "debug": {"error", "traceback", "bug", "fail", "stack"},
            "design": {"design", "architecture", "plan", "proposal"},
            "test": {"test", "suite", "coverage", "pytest"},
        }
        detected: Set[str] = set()
        for intent, markers in intent_markers.items():
            if any(marker in lowered for marker in markers):
                detected.add(intent)
        return detected

    async def _load_code_context(self, state: ContextEngineState) -> Optional[ContextSegment]:
        """Loads a segment with general information about the codebase."""
        lines: List[str] = []
        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text())
            except tomllib.TOMLDecodeError:
                data = {}
            project = data.get("project", {})
            name = project.get("name") or self.project_root.name
            version = project.get("version", "0.0.0")
            lines.append(f"Project: {name} v{version}")
            dependencies = project.get("dependencies", [])
            if dependencies:
                lines.append("Dependencies:")
                for dep in dependencies[:10]:
                    lines.append(f"  - {dep}")
            optional = project.get("optional-dependencies", {})
            if optional:
                lines.append("Optional dependency groups:")
                for group, deps in optional.items():
                    snippet = ", ".join(deps[:3])
                    suffix = "…" if len(deps) > 3 else ""
                    lines.append(f"  - {group}: {snippet}{suffix}")
        else:
            lines.append("pyproject.toml not found at project root.")

        package_dirs = []
        for child in sorted(self.project_root.iterdir()):
            if (child / "pyproject.toml").exists():
                package_dirs.append(child.name)
        if package_dirs:
            lines.append("Managed packages:")
            lines.append(", ".join(package_dirs))

        runtime_src = self.project_root / "quadracode-runtime/src/quadracode_runtime"
        if runtime_src.exists():
            modules = sorted(str(path.relative_to(self.project_root)) for path in runtime_src.glob("*.py"))
            if modules:
                lines.append("Runtime modules:")
                lines.extend(f"  - {module}" for module in modules[:20])

        content = "\n".join(lines)
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-code-overview",
            content=content,
            segment_type="code_context",
            tokens=max(tokens, 50),
        )

    async def _load_file_structure(self, state: ContextEngineState) -> Optional[ContextSegment]:
        """Loads a segment with an overview of the file structure."""
        roots = [
            self.project_root / "quadracode-runtime",
            self.project_root / "quadracode-tools",
            self.project_root / "quadracode-ui",
            self.project_root / "tests",
        ]
        lines: List[str] = []
        for root in roots:
            if not root.exists():
                continue
            lines.append(f"/{root.name}")
            for child in sorted(root.iterdir())[:15]:
                prefix = "├──" if child.is_dir() else "└──"
                lines.append(f"  {prefix} {child.name}")
                if child.is_dir():
                    for sub in sorted(child.iterdir())[:5]:
                        sub_prefix = "│   ├──" if sub.is_dir() else "│   └──"
                        lines.append(f"  {sub_prefix} {sub.name}")
        content = "\n".join(lines) if lines else "Repository directories not discovered."
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-file-structure",
            content=content,
            segment_type="file_structure",
            tokens=max(tokens, 60),
        )

    async def _load_error_history(self, state: ContextEngineState) -> Optional[ContextSegment]:
        errors = state.get("error_history", [])
        if not errors:
            content = "No error events recorded in context state."
        else:
            lines = ["Recent error events:"]
            for entry in errors[-5:]:
                summary = entry.get("message") or entry.get("error") or "unknown"
                occurred = entry.get("timestamp") or "unknown time"
                attempts = entry.get("recovery_attempts")
                attempt_info = f" | attempts: {len(attempts)}" if attempts else ""
                lines.append(f"- {occurred}: {summary}{attempt_info}")
            content = "\n".join(lines)
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-error-history",
            content=content,
            segment_type="error_history",
            tokens=max(tokens, 30),
        )

    async def _load_stack_traces(self, state: ContextEngineState) -> Optional[ContextSegment]:
        errors = state.get("error_history", [])
        traces: List[str] = []
        for entry in errors[-5:]:
            trace = entry.get("traceback") or entry.get("stack_trace")
            if trace:
                traces.append(textwrap.dedent(trace).strip())
        content = "\n\n".join(traces) if traces else "No stack traces captured in error history."
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-stack-traces",
            content=content,
            segment_type="stack_traces",
            tokens=max(tokens, 25),
        )

    async def _load_architecture_docs(self, state: ContextEngineState) -> Optional[ContextSegment]:
        snippets: List[str] = []
        for path in self.documentation_paths:
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            preview = textwrap.shorten(text, width=800, placeholder="…")
            snippets.append(f"# {path.relative_to(self.project_root)}\n{preview}")
        content = "\n\n".join(snippets) if snippets else "No documentation files found in configured paths."
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-architecture-docs",
            content=content,
            segment_type="architecture_docs",
            tokens=max(tokens, 60),
        )

    async def _load_design_patterns(self, state: ContextEngineState) -> Optional[ContextSegment]:
        runtime_src = self.project_root / "quadracode-runtime/src/quadracode_runtime"
        classes: List[str] = []
        if runtime_src.exists():
            for py_file in runtime_src.glob("*.py"):
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8"))
                except (SyntaxError, OSError):
                    continue
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        classes.append(f"{py_file.relative_to(self.project_root)}::{node.name}")
        content = (
            "Identified runtime classes:\n" + "\n".join(classes[:60])
            if classes
            else "No class definitions discovered under quadracode-runtime/src/quadracode_runtime."
        )
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-design-patterns",
            content=content,
            segment_type="design_patterns",
            tokens=max(tokens, 40),
        )

    async def _load_test_suite(self, state: ContextEngineState) -> Optional[ContextSegment]:
        entries: List[str] = []
        for root in [self.project_root / "tests", self.project_root / "quadracode-runtime/tests"]:
            if not root.exists():
                continue
            for path in sorted(root.rglob("test_*.py")):
                entries.append(str(path.relative_to(self.project_root)))
        content = "Pytest files:\n" + "\n".join(entries[:80]) if entries else "No pytest files discovered under tests directories."
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-test-suite",
            content=content,
            segment_type="test_suite",
            tokens=max(tokens, 30),
        )

    async def _load_coverage_reports(self, state: ContextEngineState) -> Optional[ContextSegment]:
        coverage_files = [
            self.project_root / "coverage.xml",
            self.project_root / ".coverage",
            self.project_root / "htmlcov/index.html",
        ]
        existing = [path for path in coverage_files if path.exists()]
        content = (
            "Coverage artifacts present:\n" + "\n".join(str(path.relative_to(self.project_root)) for path in existing)
            if existing
            else "Coverage artifacts not found (expected coverage.xml, .coverage, or htmlcov/index.html)."
        )
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-coverage-reports",
            content=content,
            segment_type="coverage_reports",
            tokens=max(tokens, 20),
        )

    async def _load_code_search(self, terms: Set[str]) -> Optional[ContextSegment]:
        matches = await asyncio.to_thread(self._perform_code_search, terms)
        if not matches:
            return None
        lines = ["Code search results:"]
        for match in matches:
            lines.append(f"{match['path']}: line {match['line']} — {match['snippet']}")
        content = "\n".join(lines)
        tokens = len(content.split())
        return self._build_segment(
            segment_id="context-code-search",
            content=content,
            segment_type="code_search_results",
            tokens=max(tokens, 30),
        )

    def _perform_code_search(self, terms: Set[str]) -> List[Dict[str, Any]]:
        lowercase_terms = {term.lower() for term in terms}
        if not lowercase_terms:
            return []

        results: List[Dict[str, Any]] = []
        max_results = max(1, self.config.code_search_max_results)
        search_roots = [
            self.project_root / "quadracode-runtime",
            self.project_root / "quadracode-tools",
            self.project_root / "quadracode-ui",
        ]
        for root in search_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if len(results) >= max_results:
                    return results
                if path.is_dir():
                    if self._path_excluded(path):
                        continue
                    continue
                if self._path_excluded(path):
                    continue
                if path.suffix.lower() not in self.config.code_search_extensions:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                lower_lines = text.lower().splitlines()
                original_lines = text.splitlines()
                for idx, line in enumerate(lower_lines, start=1):
                    if any(term in line for term in lowercase_terms):
                        snippet = original_lines[idx - 1].strip()
                        if len(snippet) > 160:
                            snippet = snippet[:157] + "…"
                        results.append(
                            {
                                "path": str(path.relative_to(self.project_root)),
                                "line": idx,
                                "snippet": snippet,
                            }
                        )
                        break
        return results

    def _ensure_skills_catalog(self, state: ContextEngineState) -> None:
        if self._skills_cache:
            state["skills_catalog"] = list(self._skills_cache.values())
            return

        catalog = self._scan_skills()
        self._skills_cache = catalog
        state["skills_catalog"] = list(catalog.values())

    def _scan_skills(self) -> Dict[str, Dict[str, Any]]:
        catalog: Dict[str, Dict[str, Any]] = {}
        for root in self.skills_roots:
            if not root.exists() or not root.is_dir():
                continue
            for skill_dir in sorted(root.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                try:
                    metadata = self._parse_skill_file(skill_file)
                except Exception:
                    continue
                slug = metadata["slug"]
                if slug in catalog:
                    continue
                catalog[slug] = metadata
        return catalog

    def _parse_skill_file(self, path: Path) -> Dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        front_lines: List[str] = []
        body_lines: List[str] = []
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            idx = 1
            while idx < len(lines) and lines[idx].strip() != "---":
                front_lines.append(lines[idx])
                idx += 1
            idx += 1  # skip closing ---
            body_lines = lines[idx:]
        else:
            body_lines = lines

        metadata = self._parse_front_matter(front_lines)
        name = metadata.get("name") or path.parent.name
        description = metadata.get("description") or ""
        tags = self._coerce_list(metadata.get("tags"))
        links = self._coerce_list(metadata.get("links"))
        slug = self._slugify_skill(name)

        metadata.update(
            {
                "name": name,
                "description": description,
                "tags": tags,
                "links": links,
                "slug": slug,
                "skill_file": str(path),
                "skill_dir": str(path.parent),
            }
        )
        return metadata

    def _parse_front_matter(self, lines: List[str]) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        current_key: Optional[str] = None
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-") and current_key:
                data.setdefault(current_key, [])
                data[current_key].append(line[1:].strip())
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                if value:
                    data[key] = value
                else:
                    data[key] = []
                current_key = key
            elif current_key:
                existing = data.get(current_key, "")
                if isinstance(existing, list):
                    data[current_key].append(line)
                else:
                    data[current_key] = f"{existing} {line}".strip()
        return data

    def _coerce_list(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [item.strip() for item in text.split(",") if item.strip()]

    def _slugify_skill(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "skill"

    async def _activate_skills(
        self, state: ContextEngineState, search_terms: Set[str]
    ) -> ContextEngineState:
        catalog = {meta["slug"]: meta for meta in state.get("skills_catalog", [])}
        if not catalog:
            state["active_skills_metadata"] = []
            return state

        relevant = self._select_skills(catalog, search_terms)
        active_slugs = {meta.get("slug") for meta in state.get("active_skills_metadata", [])}
        for slug, meta in relevant.items():
            if slug not in active_slugs:
                skeleton = {
                    "name": meta.get("name"),
                    "description": meta.get("description", ""),
                    "tags": meta.get("tags", []),
                    "slug": slug,
                }
                state.setdefault("active_skills_metadata", []).append(skeleton)
        # trim to recent few to keep prompt compact
        state["active_skills_metadata"] = state.get("active_skills_metadata", [])[-8:]

        for slug, meta in relevant.items():
            if not self._skill_main_loaded(state, slug):
                if not self._has_capacity(state, "skill_main"):
                    self._enqueue_prefetch(state, f"skill:{slug}", reason="capacity")
                    continue
                segment = self._load_skill_main_segment(meta)
                if segment:
                    state = await self._integrate_context(state, segment)
                    entry = self._skill_entry(state, slug)
                    entry["main_loaded"] = True
                    entry["segment_id"] = segment["id"]
            self._schedule_skill_links(state, meta)
        return state

    def _select_skills(
        self, catalog: Dict[str, Dict[str, Any]], search_terms: Set[str]
    ) -> Dict[str, Dict[str, Any]]:
        if not catalog:
            return {}
        lowercase_terms = {term.lower() for term in search_terms}
        relevant: Dict[str, Dict[str, Any]] = {}
        for slug, meta in catalog.items():
            tags = {str(tag).lower() for tag in meta.get("tags", [])}
            if "auto" in tags or lowercase_terms & tags:
                relevant[slug] = meta
        return relevant

    def _skill_entry(self, state: ContextEngineState, slug: str) -> Dict[str, Any]:
        store = state.setdefault("loaded_skills", {})
        if slug not in store:
            store[slug] = {"main_loaded": False, "links_loaded": []}
        return store[slug]

    def _skill_main_loaded(self, state: ContextEngineState, slug: str) -> bool:
        entry = self._skill_entry(state, slug)
        return bool(entry.get("main_loaded"))

    def _load_skill_main_segment(self, metadata: Dict[str, Any]) -> Optional[ContextSegment]:
        body = self._read_skill_body(Path(metadata["skill_file"]))
        if not body:
            return None
        slug = metadata["slug"]
        segment_id = f"skill-{slug}"
        content = body
        tokens = max(60, len(content.split()))
        return self._build_segment(
            segment_id=segment_id,
            content=content,
            segment_type=f"skill:{slug}",
            tokens=tokens,
        )

    def _read_skill_body(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text.strip()

    def _schedule_skill_links(
        self, state: ContextEngineState, metadata: Dict[str, Any]
    ) -> None:
        slug = metadata["slug"]
        entry = self._skill_entry(state, slug)
        loaded_links = set(entry.get("links_loaded", []))
        for link in metadata.get("links", []):
            if link in loaded_links:
                continue
            queue_key = f"skill_link:{slug}:{link}"
            self._enqueue_prefetch(state, queue_key, reason="skill-link")

    def _path_excluded(self, path: Path) -> bool:
        excluded = set(self.config.code_search_exclude_dirs)
        return any(part in excluded for part in path.parts)

    def _enqueue_prefetch(self, state: ContextEngineState, need: str, *, reason: str) -> None:
        queue = state["prefetch_queue"]
        if any(item["type"] == need for item in queue):
            return
        queue.append(
            {
                "type": need,
                "priority": self._need_priority(need),
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _trim_prefetch_queue(self, state: ContextEngineState) -> None:
        limit = max(1, self.config.prefetch_queue_limit)
        queue = state["prefetch_queue"]
        if len(queue) <= limit:
            return
        queue.sort(key=lambda item: (-item["priority"], item["timestamp"]))
        del queue[limit:]

    def _remove_pending(self, state: ContextEngineState, need: str) -> None:
        if need in state["pending_context"]:
            state["pending_context"] = [item for item in state["pending_context"] if item != need]
        state["prefetch_queue"] = [item for item in state["prefetch_queue"] if item["type"] != need]

    def _compile_search_terms(self, state: ContextEngineState) -> Set[str]:
        if not state.get("messages"):
            return set()
        last_message = state["messages"][-1]
        content = self._message_to_text(last_message)
        stop_words = {
            "please",
            "could",
            "would",
            "implementation",
            "implement",
            "tests",
            "test",
            "design",
        }
        terms: Set[str] = set()
        for raw in content.replace("\n", " ").split():
            word = raw.strip(".,:;!?()[]{}\"'").lower()
            if len(word) < 4 or word in stop_words or not word.isascii():
                continue
            terms.add(word)
            if len(terms) >= 8:
                break
        return terms

    def _message_to_text(self, message: Any) -> str:
        """Coerces a LangChain/LangGraph message into plain text."""
        raw = getattr(message, "content", message)
        return self._coerce_content(raw).strip()

    def _coerce_content(self, raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            fragments = [self._coerce_content(item) for item in raw]
            return "\n".join(fragment for fragment in fragments if fragment)
        if isinstance(raw, dict):
            for key in ("text", "content", "message"):
                value = raw.get(key)
                if isinstance(value, str):
                    return value
            data = raw.get("data")
            if isinstance(data, (str, list, dict)):
                coerced = self._coerce_content(data)
                if coerced:
                    return coerced
            try:
                return json.dumps(raw, ensure_ascii=False)
            except Exception:
                return str(raw)
        return str(raw)

    def _build_segment(
        self,
        *,
        segment_id: str,
        content: str,
        segment_type: str,
        tokens: int,
    ) -> ContextSegment:
        """Builds a `ContextSegment` dictionary."""
        return {
            "id": segment_id,
            "content": content,
            "type": segment_type,
            "priority": self._need_priority(segment_type),
            "token_count": tokens,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decay_rate": 0.1,
            "compression_eligible": True,
            "restorable_reference": None,
        }

    def _get_milestone_context_needs(self, milestone: int | str) -> Set[str]:
        """
        Returns the set of context needs for a given milestone.
        """
        mapping: Dict[str, Set[str]] = {
            "1": {"architecture_docs", "code_context"},
            "2": {"code_context", "file_structure", "test_suite"},
            "3": {"test_suite", "coverage_reports", "error_history"},
            "4": {"architecture_docs", "design_patterns", "code_search_results"},
        }
        return mapping.get(str(milestone), set())
