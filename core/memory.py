import os
import json
import datetime
import threading
from core.jarvis_logger import log_error, log_warn

# The single local file where JARVIS will store his permanent brain graph
_DATA_DIR   = os.path.join(os.path.dirname(__file__), "..", "data")
MEMORY_FILE = os.path.join(_DATA_DIR, "jarvis_memory.json")
memory_lock = threading.Lock()
_last_compress_attempt = 0

def load_brain():
    """Loads the structured JSON knowledge graph securely."""
    if not os.path.exists(MEMORY_FILE):
        # Create an advanced base structure if the file doesn't exist
        return {"Procedures": {}, "User_Profile": {}, "General_Facts": {}}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure the file isn't corrupted or empty
            if not isinstance(data, dict):
                log_warn("memory", "load_brain: file corrupted, resetting to empty graph")
                return {"Procedures": {}, "User_Profile": {}, "General_Facts": {}}
            return data
    except Exception as e:
        log_error("memory", "load_brain", e)
        return {"Procedures": {}, "User_Profile": {}, "General_Facts": {}}

def save_brain(data):
    """Saves the knowledge graph securely to the hard drive."""
    import tempfile
    import os
    # Atomic write to prevent zero-byte truncation on crash
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(MEMORY_FILE) or ".", text=True)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
    os.replace(temp_path, MEMORY_FILE)

def save_memory(topic, fact):
    """
    ADVANCED MEMORY ROUTER: Automatically categorizes the memory and adds a timestamp.
    This replaces the old flat-file system with a structured Knowledge Graph.
    """
    with memory_lock:
        brain = load_brain()
        
        # 1. AI Auto-Categorization Logic
        topic_lower = str(topic).lower()
        if "procedure" in topic_lower or "code" in topic_lower or "how_to" in topic_lower:
            category = "Procedures"
        elif "user" in topic_lower or "my" in topic_lower or "i_" in topic_lower:
            category = "User_Profile"
        else:
            category = "General_Facts"
            
        # Ensure category exists in case of an older JSON file
        if category not in brain:
            brain[category] = {}
            
        # 2. Add Timestamping for chronological awareness
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 3. Save the Data as a rich object
        brain[category][topic] = {
            "data": fact,
            "learned_on": timestamp
        }
        
        # 4. Write to disk
        save_brain(brain)
        print(f"\n[💾 KNOWLEDGE GRAPH UPDATED]: Saved '{topic}' into the [{category}] sector.")
        
        # 5. Compress if too large
        if os.path.exists(MEMORY_FILE) and os.path.getsize(MEMORY_FILE) > 50000:
            global _last_compress_attempt
            import time
            if time.time() - _last_compress_attempt > 300: # 5 minutes backoff
                _last_compress_attempt = time.time()
                print("\n[🧠 MEMORY]: Context limit approaching. Initiating background memory compression...")
                _get_compress_executor().submit(compress_memory_background)

_compress_executor = None
_compress_exec_lock = threading.Lock()

def _get_compress_executor():
    global _compress_executor
    if _compress_executor is None:
        with _compress_exec_lock:
            if _compress_executor is None:
                import concurrent.futures
                _compress_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="MemoryCompress"
                )
    return _compress_executor

_compress_lock = threading.Lock()

def compress_memory_background():
    """Summarizes and condenses the JSON knowledge graph to prevent context window bloat."""
    if not _compress_lock.acquire(blocking=False):
        return
    try:
        from core.brain import call_groq_brain
        import json
        
        with memory_lock:
            brain = load_brain()
            
        raw_json = json.dumps(brain)
        prompt = f"Condense the following knowledge graph JSON to be as concise as possible while retaining all distinct factual data, procedures, and profile facts. Return ONLY valid JSON matching the original schema ('Procedures', 'User_Profile', 'General_Facts').\n\n{raw_json}"
        
        response = call_groq_brain(prompt, phase="DIRECTIVE", is_logic_task=True)
        if response and isinstance(response, dict) and "Procedures" in response:
            with memory_lock:
                current_brain = load_brain()
                if json.dumps(current_brain) != raw_json:
                    print("[MEMORY]: Brain modified during compression. Aborting to prevent data loss.")
                    return
                save_brain(response)
            print("\n[🧠 MEMORY]: Compression complete. Context window optimized.")
    except Exception as e:
        log_error("memory", "compress_memory_background", e)
    finally:
        _compress_lock.release()

def update_memory(entity, attribute, value):
    """
    Legacy support for specific entity mapping (e.g. updating a specific friend's name).
    Kept active so no features are removed.
    """
    with memory_lock:
        brain = load_brain()
        entity = entity.capitalize()
        
        if entity not in brain:
            brain[entity] = {}
            
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        brain[entity][attribute] = {
            "data": value,
            "learned_on": timestamp
        }
        
        save_brain(brain)
        return f"Successfully mapped {attribute} of {entity} to {value}."

def get_memory_context():
    """Formats the highly advanced knowledge graph so the AI can read it efficiently."""
    with memory_lock:
        brain = load_brain()
    
    if not brain:
        return "You currently have no long-term memories stored."
        
    context = "LONG-TERM MEMORY DATABASE (Knowledge Graph):\n"
    
    for category, items in brain.items():
        # ⚡ BUG FIX: Category header is now safely outside the type-check
        context += f"\n[{category.upper()}]:\n"
        
        # ⚡ BUG FIX: Proper indentation and exhaustive type-checking to prevent data loss
        if isinstance(items, dict) and items:
            for topic, details in items.items():
                # Check if it's the new Advanced format (dict) or old legacy format (string)
                if isinstance(details, dict):
                    fact = details.get("data", "")
                    date = details.get("learned_on", "Unknown Date")
                    context += f"- {topic} (Learned: {date}):\n  {fact}\n"
                else:
                    context += f"- {topic}: {details}\n"
                    
        elif isinstance(items, list) and items:
            for item in items:
                context += f"- {item}\n"
                
        elif isinstance(items, str) and items.strip():
            context += f"- {items}\n"
            
        else:
            context += "  (Empty Sector)\n"
                
    return context

def read_memory():
    """Returns the raw JSON graph (useful for debugging)."""
    with memory_lock:
        brain = load_brain()
    return json.dumps(brain, indent=2)