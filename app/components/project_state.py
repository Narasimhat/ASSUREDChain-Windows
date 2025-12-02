import copy
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = ROOT / "data" / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
PROJ_DIR = PROJECTS_DIR


def normalize_path_for_storage(path: Path) -> str:
    """Convert path to string, preferring relative path from ROOT to avoid UNC path issues."""
    try:
        # Try to get relative path from ROOT
        rel_path = path.relative_to(ROOT)
        return str(rel_path)
    except (ValueError, OSError):
        # If relative path fails, use absolute but as string
        return str(path)


def resolve_stored_path(path_str: str) -> Path:
    """Resolve a stored path string back to a Path object."""
    path = Path(path_str)
    if not path.is_absolute():
        # If it's relative, resolve from ROOT
        return ROOT / path
    return path


def ensure_local_path(path: Path) -> Path:
    """Ensure path uses drive letter instead of UNC network path."""
    path_str = str(path)
    # If it's a UNC path starting with \\\\mdc-berlin.net, try to convert to U: drive
    if path_str.startswith('\\\\\\\\mdc-berlin.net\\\\fs\\\\AG_Diecke\\\\DATA MANAGMENT'):
        # Replace UNC prefix with U: drive
        local_path = path_str.replace(
            '\\\\\\\\mdc-berlin.net\\\\fs\\\\AG_Diecke\\\\DATA MANAGMENT',
            'U:\\\\DATA MANAGMENT'
        )
        return Path(local_path)
    return path


DEFAULT_STRUCTURE = {
    "meta": {
        "project_name": "",
        "cell_line": "",
        "owner": "",
        "created_at": 0,
        "status": "draft",
    },
    "files": {
        "snapshots": [],
        "reports": [],
        "uploads": [],
    },
    "chain": [],
    "audit": [],
}

PROJECT_FOLDERS = ("snapshots", "reports", "uploads", "chainproofs", "exports")


def list_projects() -> List[str]:
    return sorted([p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()])


def ensure_dirs(project_id: str) -> Path:
    project_root = PROJECTS_DIR / project_id
    project_root.mkdir(parents=True, exist_ok=True)
    for folder in PROJECT_FOLDERS:
        (project_root / folder).mkdir(parents=True, exist_ok=True)
    return project_root


def project_subdir(project_id: str, *parts: str) -> Path:
    base = ensure_dirs(project_id)
    path = base
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path(project_id: str) -> Path:
    return ensure_dirs(project_id) / "manifest.json"


def load_manifest(project_id: str) -> Dict[str, Any]:
    path = manifest_path(project_id)
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            return json.loads(content)
        except (FileNotFoundError, OSError, PermissionError, json.JSONDecodeError) as e:
            st.warning(f"Could not load manifest for {project_id}: {e}. Creating new manifest.")
        except Exception as e:
            st.warning(f"Unexpected error loading manifest for {project_id}: {e}. Creating new manifest.")
    manifest = copy.deepcopy(DEFAULT_STRUCTURE)
    save_manifest(project_id, manifest)
    return manifest


def save_manifest(project_id: str, manifest: Dict[str, Any]) -> None:
    path = manifest_path(project_id)
    try:
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except (FileNotFoundError, OSError, PermissionError) as e:
        st.error(f"Could not save manifest for {project_id}: {e}")
    except Exception as e:
        st.error(f"Unexpected error saving manifest for {project_id}: {e}")


def append_audit(project_id: str, entry: Dict[str, Any]) -> None:
    manifest = load_manifest(project_id)
    manifest.setdefault("audit", []).append(entry)
    save_manifest(project_id, manifest)


def register_file(project_id: str, category: str, payload: Dict[str, Any]) -> None:
    manifest = load_manifest(project_id)
    manifest.setdefault("files", {}).setdefault(category, []).append(payload)
    save_manifest(project_id, manifest)


def register_chain_tx(project_id: str, tx_payload: Dict[str, Any]) -> None:
    manifest = load_manifest(project_id)
    manifest.setdefault("chain", []).append(tx_payload)
    save_manifest(project_id, manifest)


def load_project_meta(project_id: str) -> Dict[str, Any]:
    manifest = load_manifest(project_id)
    return copy.deepcopy(manifest.get("meta", {}))


def _deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in updates.items():
        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def update_project_meta(project_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    manifest = load_manifest(project_id)
    meta = manifest.get("meta", {})
    manifest["meta"] = _deep_merge(meta, updates)
    save_manifest(project_id, manifest)
    return manifest["meta"]


def create_project(project_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
    ensure_dirs(project_id)
    manifest = load_manifest(project_id)
    manifest["meta"].update(meta or {})
    manifest["meta"]["project_id"] = project_id
    save_manifest(project_id, manifest)


def use_project(label: str = "Project") -> Optional[str]:
    with st.sidebar:
        st.header("Projects")
        existing = list_projects()
        placeholder = f"-- Select {label} --"
        options = [placeholder] + existing
        if "project_id" in st.session_state and st.session_state["project_id"] in existing:
            default_index = options.index(st.session_state["project_id"])
        else:
            default_index = 0
        selected = st.selectbox(
            f"Select {label}",
            options,
            index=default_index,
            key="project_select",
        )
        new_id = st.text_input("Create new project ID", key="project_new_id")
        if st.button("Create project", key="project_create"):
            project_id = new_id.strip()
            if not project_id:
                st.warning("Project ID cannot be empty.")
            elif project_id in existing:
                st.warning("Project ID already exists.")
            else:
                create_project(project_id, {"created_at": int(time.time())})
                st.session_state["project_id"] = project_id
                st.rerun()
        # Only update session state if a valid project is selected, not the placeholder
        if selected != placeholder and selected in existing:
            st.session_state["project_id"] = selected
    return st.session_state.get("project_id")
