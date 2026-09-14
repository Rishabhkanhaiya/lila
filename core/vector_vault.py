import os
import threading
from core.jarvis_logger import log_error, log_warn

# ── Optional imports — graceful degradation if not installed ──────────────────
try:
    import chromadb
    HAS_CHROMA = True
except ImportError:
    chromadb = None
    HAS_CHROMA = False
    log_warn("vector_vault", "chromadb not installed — episodic memory disabled.")

try:
    from pypdf import PdfReader
    HAS_PDF = True
except ImportError:
    try:
        from PyPDF2 import PdfReader
        HAS_PDF = True
    except ImportError:
        PdfReader = None
        HAS_PDF = False

try:
    import docx as _docx
    HAS_DOCX = True
except ImportError:
    _docx = None
    HAS_DOCX = False

try:
    from pptx import Presentation
    HAS_PPTX = True
except ImportError:
    Presentation = None
    HAS_PPTX = False

# ── Safe Embedding Function (NO ONNX / NO torch DLLs) ────────────────────────
# ChromaDB's default OnnxMiniLmL6V2 crashes on Windows in non-main threads
# (0xC0000005 — DLL init routines can't run in background threads).
# We replace it with a Gemini text-embedding-004 backed function, with a
# deterministic hash fallback so the vault never silently loses data.

_EMBED_DIM = 768  # text-embedding-004 output dimension

_BaseEF = chromadb.EmbeddingFunction if (HAS_CHROMA and hasattr(chromadb, 'EmbeddingFunction')) else object

class _SafeGeminiEmbedding(_BaseEF):
    """
    Thread-safe embedding function for ChromaDB.
    Uses Gemini text-embedding-004 via google-genai SDK.
    Falls back to deterministic hash-embedding on any API error.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._client = None

    def _get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    try:
                        from dotenv import load_dotenv
                        load_dotenv()
                        from google import genai
                        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
                        if api_key:
                            self._client = genai.Client(api_key=api_key)
                    except Exception:
                        self._client = None
        return self._client

    def _hash_embed(self, text: str):
        """Deterministic 768-dim float embedding from SHA-256 of text."""
        import hashlib, struct
        h = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
        # Tile hash bytes to fill 768 dims (768 * 4 bytes = 3072)
        repeated = (h * ((768 * 4 // len(h)) + 1))[:768 * 4]
        floats = list(struct.unpack(f"{768}f", repeated))
        # Normalize to unit vector
        norm = sum(x * x for x in floats) ** 0.5 or 1.0
        return [x / norm for x in floats]

    def __call__(self, input):
        """ChromaDB embedding function interface: input is a list of strings."""
        results = []
        client = self._get_client()
        for text in input:
            try:
                if client:
                    resp = client.models.embed_content(
                        model="text-embedding-004",
                        contents=text
                    )
                    emb = resp.embeddings[0].values
                    if len(emb) != _EMBED_DIM:
                        emb = (list(emb) + [0.0] * _EMBED_DIM)[:_EMBED_DIM]
                    results.append(list(emb))
                else:
                    results.append(self._hash_embed(text))
            except Exception:
                results.append(self._hash_embed(text))
        return results

    @property
    def is_legacy(self):
        return False

    def name(self) -> str:
        return "safe_gemini_embedding"

    def embed_query(self, input):
        if isinstance(input, str):
            input = [input]
        return self.__call__(input)

    def embed_documents(self, input):
        return self.__call__(input)

_safe_embed_fn = _SafeGeminiEmbedding()

# ── ChromaDB initialization ───────────────────────────────────────────────────
VAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "chroma_vault")
chroma_client = None
collection = None
episodic_collection = None

if HAS_CHROMA:
    try:
        os.makedirs(VAULT_DB_PATH, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=VAULT_DB_PATH)
        # Use our safe embedding function — no ONNX, no torch, no DLL crashes
        collection = chroma_client.get_or_create_collection(
            name="document_vault_v2",
            embedding_function=_safe_embed_fn
        )
        episodic_collection = chroma_client.get_or_create_collection(
            name="episodic_memory_v2",
            embedding_function=_safe_embed_fn
        )
    except Exception as _e:
        log_error("vector_vault", "chromadb_init", _e)
        HAS_CHROMA = False
        collection = None
        episodic_collection = None


def ingest_conversation(user_text, ai_response):
    """Saves a conversational exchange permanently into the semantic memory vault."""
    if not HAS_CHROMA: return
    try:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chunk = f"Time: {timestamp}\nUser said: {user_text}\nJARVIS replied: {ai_response}"
        safe_time = timestamp.replace(":", "-").replace(" ", "_")
        doc_id = f"episode_{safe_time}"
        # Fix #4c: Direct call — no thread-then-join (that's just blocking with overhead)
        episodic_collection.add(
            documents=[chunk],
            metadatas=[{"timestamp": timestamp, "type": "conversation"}],
            ids=[doc_id]
        )
        print(f"[🧠 EPISODIC MEMORY]: Logged conversation to neural vault.")
    except Exception as e:
        err_lower = str(e).lower()
        if any(w in err_lower for w in ["bad allocation", "onnx", "nan", "infinity", "validation error"]):
            log_warn("vector_vault", f"Episodic memory skipped due to embedding model limit: {e}")
        else:
            log_error("vector_vault", "ingest_conversation", e)

def recall_episode(query, limit=5):
    """Searches past conversations to remember what was discussed."""
    if not HAS_CHROMA: return ""
    if not query: return ""
    # Prevent querying if collection is empty
    if episodic_collection.count() == 0:
        return ""
    results = episodic_collection.query(
        query_texts=[query],
        n_results=limit
    )
    
    if not results or not results.get('documents') or not results['documents'][0]:
        return ""
        
    compiled = "--- PAST EPISODIC MEMORIES (CONVERSATION HISTORY) ---\n"
    for idx, doc in enumerate(results['documents'][0]):
        compiled += f"{doc}\n---\n"
    return compiled

def extract_text(file_path):
    """⚡ THE OMNI-READER: Automatically reads PDF, Word, PPT, TXT, and Code!"""
    ext = file_path.lower().split('.')[-1]
    text = ""
    
    try:
        if ext == 'pdf':
            reader = PdfReader(file_path)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted: text += extracted + "\n"
                
        elif ext == 'docx':
            doc = _docx.Document(file_path)
            for para in doc.paragraphs:
                text += para.text + "\n"
                
        elif ext == 'pptx':
            prs = Presentation(file_path)
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        text += shape.text + "\n"
                        
        elif ext in ['txt', 'md', 'py', 'js', 'html', 'css', 'json']:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
                
        else:
            log_warn("vector_vault", f"extract_text: unsupported file type '{ext}'")
            return None
            
    except Exception as e:
        log_error("vector_vault", f"extract_text({ext})", e)
        return None
        
    return text

def ingest_document(file_path):
    """Eats any document, chunks it, and indexes it instantly into the Neural Vault."""
    if not HAS_CHROMA:
        print("[VAULT]: chromadb not installed. Cannot ingest document.")
        return False
    print(f"\n[📚 UNIVERSAL VAULT]: Processing document -> {file_path}...")
    
    if not os.path.exists(file_path):
        print(f"[⚠️ ERROR]: Could not find the file at {file_path}")
        return False
        
    # Route to the Omni-Reader
    text = extract_text(file_path)
    
    if not text or not text.strip():
        print(f"[⚠️ ERROR]: No readable text found in {file_path}")
        return False
        
    try:
        # Chunk the text into 800-character blocks with 200 overlap
        chunk_size = 800
        overlap = 200
        chunks = []
        for i in range(0, len(text), chunk_size - overlap):
            chunks.append(text[i:i + chunk_size])
        
        source_name = os.path.basename(file_path)
        
        # Delete old chunks from this specific file so we don't get duplicates
        # ChromaDB handles this gracefully if the source doesn't exist yet
        collection.delete(where={"source": source_name})
        
        # Generate unique IDs and metadata for the Vector DB
        ids = [f"{source_name}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [{"source": source_name} for _ in range(len(chunks))]
        
        # Inject into Chroma (It automatically generates the neural embeddings!)
        collection.add(
            documents=chunks,
            metadatas=metadatas,
            ids=ids
        )
        
        print(f"[✅ UNIVERSAL VAULT]: Successfully indexed {len(chunks)} neural chunks.")
        return True
        
    except Exception as e:
        log_error("vector_vault", "ingest_document", e)
        return False

def search_vault(query, limit=3):
    """Uses Neural Embeddings to find the true semantic meaning of your query."""
    if not HAS_CHROMA: return ""
    if not query: 
        return ""
        
    # Semantic Vector Search
    results = collection.query(
        query_texts=[query],
        n_results=limit
    )
    
    # Check if we got valid documents back
    if not results or not results['documents'] or not results['documents'][0]:
        return ""
        
    compiled = "--- RELEVANT NEURAL VAULT DATA ---\n\n"
    
    # Extract the highest matched documents from the arrays
    for idx, doc in enumerate(results['documents'][0]):
        source = results['metadatas'][0][idx]['source']
        compiled += f"[Source: {source}]\n{doc}\n\n"
    return compiled

def ingest_text(text: str, category: str = "general", tags: list = None) -> str:
    """Directly chunks and indexes raw text into ChromaDB Vector Vault without needing a disk file."""
    if not text or not text.strip():
        return ""
    import time, uuid
    doc_id = f"doc_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    if not HAS_CHROMA or collection is None:
        return doc_id
    try:
        tag_str = ",".join(tags) if tags else ""
        chunk_size = 800
        overlap = 100
        chunks = []
        metadatas = []
        ids = []
        for i in range(0, len(text), chunk_size - overlap):
            chunk = text[i:i + chunk_size].strip()
            if chunk:
                c_idx = len(chunks)
                chunks.append(chunk)
                metadatas.append({"source": f"text_ingest:{category}", "chunk_index": c_idx, "tags": tag_str})
                ids.append(f"{doc_id}_{c_idx}")
        if chunks:
            collection.add(documents=chunks, metadatas=metadatas, ids=ids)
        return doc_id
    except Exception as e:
        log_error("vector_vault", "ingest_text", e)
        return doc_id