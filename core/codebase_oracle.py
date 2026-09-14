"""
core/codebase_oracle.py — JARVIS Codebase Oracle (RAG Engine)
==============================================================
Ingests an entire code repository into a dedicated ChromaDB
collection and answers semantic questions about it.

Uses the same ChromaDB client as vector_vault.py but a
separate "codebase_oracle" collection so it never pollutes
episodic memory or document vault.

Voice usage:
  "Jarvis, ingest my project at C:/code/myapp"
  "Jarvis, how does authentication work in my project?"
  "Jarvis, what does the login function do?"
  "Jarvis, clear the oracle"
"""

import os
import threading
from pathlib import Path
from core.jarvis_logger import log_error, log_warn, log_info

# ── Extensions that we will ingest ────────────────────────────────────────────
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".java", ".kt", ".swift", ".go", ".rs", ".c", ".cpp", ".h",
    ".cs", ".rb", ".php", ".dart", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt", ".env.example", ".sh", ".bat",
}

# ── Directories to skip during ingestion ─────────────────────────────────────
SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "coverage", ".idea", ".vscode",
}

# ── ChromaDB collection ───────────────────────────────────────────────────────
try:
    import chromadb
    HAS_CHROMA = True
except ImportError:
    chromadb = None
    HAS_CHROMA = False
    log_warn("codebase_oracle", "chromadb not installed — Oracle disabled.")

VAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "chroma_vault")
_oracle_collection = None
_lock = threading.Lock()
_ingested_root = ""     # tracks which repo is currently loaded

def _get_collection():
    global _oracle_collection
    if not HAS_CHROMA:
        return None
    if _oracle_collection is None:
        try:
            os.makedirs(VAULT_DB_PATH, exist_ok=True)
            client = chromadb.PersistentClient(path=VAULT_DB_PATH)
            from core.vector_vault import _SafeGeminiEmbedding
            _oracle_collection = client.get_or_create_collection(
                name="codebase_oracle",
                embedding_function=_SafeGeminiEmbedding()
            )
        except Exception as e:
            log_error("codebase_oracle", "_get_collection", e)
    return _oracle_collection


def _chunk_code(text: str, chunk_size: int = 600, overlap: int = 100) -> list[str]:
    """Chunks a source file into overlapping segments for better recall."""
    chunks = []
    for i in range(0, len(text), chunk_size - overlap):
        chunk = text[i:i + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def ingest_repository(root_path: str, ui_callback=None) -> str:
    """
    Walks the entire repository at root_path, reads every code file,
    chunks it, and stores it in the codebase_oracle ChromaDB collection.
    Returns a status message string.
    """
    global _ingested_root

    if not HAS_CHROMA:
        return "Oracle unavailable: chromadb not installed."

    root = Path(root_path).resolve()
    if not root.exists() or not root.is_dir():
        return f"Directory not found: {root_path}"

    collection = _get_collection()
    if collection is None:
        return "Oracle collection failed to initialize."

    def _status(msg):
        log_info("codebase_oracle", msg)
        if ui_callback:
            ui_callback(msg)
        print(f"[🔮 ORACLE]: {msg}")

    _status(f"Scanning repository: {root}")

    files_found = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext in CODE_EXTENSIONS:
                files_found.append(Path(dirpath) / fname)

    if not files_found:
        return f"No code files found in {root_path}"

    _status(f"Found {len(files_found)} files. Ingesting...")

    # Clear old data for this repo so re-ingests stay fresh
    try:
        existing = collection.get(where={"repo": str(root)})
        if existing and existing.get("ids"):
            collection.delete(ids=existing["ids"])
            _status(f"Cleared {len(existing['ids'])} old chunks.")
    except Exception as e:
        log_warn("codebase_oracle", f"Clear old data failed (ok on first run): {e}")

    total_chunks = 0
    for fpath in files_found:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
            if not text.strip():
                continue
            rel_path = str(fpath.relative_to(root))
            chunks = _chunk_code(text)
            ids = [f"{rel_path}::chunk_{i}" for i in range(len(chunks))]
            metas = [{"repo": str(root), "file": rel_path, "chunk": i} for i in range(len(chunks))]
            collection.add(documents=chunks, metadatas=metas, ids=ids)
            total_chunks += len(chunks)
        except Exception as e:
            log_warn("codebase_oracle", f"Failed to ingest {fpath}: {e}")

    _ingested_root = str(root)
    msg = f"Oracle indexed {total_chunks} chunks from {len(files_found)} files in {root.name}"
    _status(msg)
    return msg


def query_oracle(question: str, n_results: int = 5) -> str:
    """
    Semantically searches the ingested codebase and answers the question
    using retrieved code context + LLM.
    Returns the final answer string.
    """
    if not HAS_CHROMA:
        return "Oracle unavailable: chromadb not installed."

    collection = _get_collection()
    if collection is None or collection.count() == 0:
        return "Oracle has no data. Please ingest a repository first."

    try:
        results = collection.query(query_texts=[question], n_results=min(n_results, collection.count()))
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
    except Exception as e:
        log_error("codebase_oracle", "query_oracle", e)
        return f"Oracle query failed: {e}"

    if not docs:
        return "No relevant code found for that question."

    # Build context block
    context_parts = []
    for doc, meta in zip(docs, metas):
        fname = meta.get("file", "unknown")
        context_parts.append(f"### File: {fname}\n```\n{doc}\n```")
    context = "\n\n".join(context_parts)

    prompt = (
        f"You are JARVIS, an expert code analyst. "
        f"Based on the following code excerpts from a repository, answer this question concisely:\n\n"
        f"Question: {question}\n\n"
        f"{context}\n\n"
        f"Answer clearly and reference specific file names and function names where relevant."
    )

    try:
        from core.brain import call_groq_brain
        answer = call_groq_brain(prompt, phase="CONVERSATION", is_logic_task=False)
        if isinstance(answer, dict):
            answer = answer.get("reply", str(answer))
        return str(answer)
    except Exception as e:
        log_error("codebase_oracle", "query_oracle_llm", e)
        return f"LLM answer failed: {e}\n\nRaw context:\n{context[:500]}"


def get_status() -> dict:
    """Returns status dict for the UI dashboard."""
    collection = _get_collection()
    count = 0
    if collection:
        try:
            count = collection.count()
        except Exception:
            pass
    return {
        "repo": _ingested_root or "None",
        "chunks": count,
        "ready": count > 0,
    }


def clear_oracle() -> str:
    """Clears all ingested codebase data."""
    global _ingested_root, _oracle_collection
    collection = _get_collection()
    if collection:
        try:
            all_ids = collection.get()["ids"]
            if all_ids:
                collection.delete(ids=all_ids)
        except Exception as e:
            log_warn("codebase_oracle", f"clear failed: {e}")
    _ingested_root = ""
    return "Oracle cleared. Ready for a new repository."
