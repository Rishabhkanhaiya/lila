"""
core/gemini_web.py — JARVIS Web Application Forge (Modern 2026 Engine)
======================================================================
Uses the modern google.genai SDK with Gemini 2.5 Flash for high-speed
web application generation, Alpine.js interactivity, and AST diff editing.
"""

import os
import json
from google import genai
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

_client = None
def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY", "")
        _client = genai.Client(api_key=key)
    return _client

MODEL_NAME = "gemini-2.5-flash"

# ==========================================
# AGENT 1: THE LEAD ARCHITECT (Plans the folder)
# ==========================================
def run_architect_agent(user_prompt):
    print(f"\n[🧠 AGENT 1: ARCHITECT]: Planning directory structure for -> '{user_prompt[:30]}...'")
    client = _get_client()
    
    architect_rules = """
    You are the Lead Web Architect. The user wants a website.
    Decide how many pages are needed to make it a COMPLETE platform (e.g., index, about, dashboard, pricing, contact).
    You MUST output ONLY valid JSON. No markdown, no backticks, no explanations.
    
    Format EXACTLY like this:
    {
      "project_folder_name": "luxury_watch_app",
      "pages": [
        {"filename": "index.html", "purpose": "The stunning hero landing page with Bento Grid features."},
        {"filename": "catalog.html", "purpose": "Displays the product grid with glassmorphism cards."},
        {"filename": "contact.html", "purpose": "A sleek, modern contact form page."}
      ]
    }
    """
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=f"{architect_rules}\n\nUSER REQUEST: {user_prompt}"
        )
        raw_text = response.text or "{}"
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        print(f"[⚠️ ARCHITECT ERROR]: Failed to parse JSON -> {e}")
        return {"project_folder_name": "web_forge_app", "pages": [{"filename": "index.html", "purpose": "Main landing page"}]}

# ==========================================
# AGENT 2: THE UI CODER (Phase 3: Motion Engine)
# ==========================================
def generate_tailwind_web(user_prompt, output_path=""):
    blueprint = run_architect_agent(user_prompt)
    project_folder = os.path.basename(blueprint.get("project_folder_name", "app"))
    pages = blueprint.get("pages", [])
    
    save_dir = os.path.join(os.path.dirname(__file__), '..', 'ui', 'web', 'downloads', project_folder)
    os.makedirs(save_dir, exist_ok=True)
    print(f"[SYSTEM]: Created dedicated project folder -> /downloads/{project_folder}/")
    
    client = _get_client()
    
    # ── THE PHASE 3 SYSTEM PROMPT (GSAP + ALPINE + RESPONSIVE) ──
    system_rules = """
    You are an elite Silicon Valley UI/UX Engineer building a multi-page app.
    
    CRITICAL ASSETS TO INJECT (IN <HEAD>):
    1. Tailwind CSS: <script src="https://cdn.tailwindcss.com"></script>
    2. Lucide Icons: <script src="https://unpkg.com/lucide@latest"></script>
    3. Google Fonts: <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap" rel="stylesheet">
    4. Alpine.js (Interactivity): <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    5. GSAP (Animations): <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
    6. Tailwind Config: <script>tailwind.config = { theme: { extend: { fontFamily: { sans: ['Space Grotesk', 'sans-serif'] } } } }</script>

    THE 2026 DESIGN SYSTEM:
    1. THEME: Dark Mode (bg-zinc-950) with glowing accents.
    2. RESPONSIVE BREAKPOINTS (CRITICAL): The website will be viewed in a small iframe window. You MUST use mobile-first Tailwind classes. Use `flex-col md:flex-row` and `grid-cols-1 md:grid-cols-2 lg:grid-cols-3` to ensure NOTHING squishes on small screens.
    3. INTERACTIVITY: The Navbar MUST have a working mobile hamburger menu built with Alpine.js (`x-data`, `x-on:click`, `x-show`).
    4. ANIMATION: Use a tiny GSAP script at the bottom of the body to fade-in and slide-up the main hero and bento grid elements on page load.
    5. CARDS: Glassmorphism `bg-white/5 backdrop-blur-2xl border border-white/10 rounded-3xl p-8 hover:-translate-y-1 hover:shadow-[0_0_30px_rgba(52,211,153,0.2)] transition-all duration-300`.
    
    IMPORTANT ROUTING: Include a working Alpine.js Navbar on EVERY page. Links must match filenames exactly.
    
    OUTPUT: Return ONLY raw HTML. No markdown block wrap. Initialize Lucide icons at the bottom.
    """

    index_path = None
    
    for i, page in enumerate(pages):
        filename = page["filename"]
        purpose = page["purpose"]
        print(f"[CODER]: Forging {filename} ({i+1}/{len(pages)})...")
        
        page_prompt = f"""
        OVERALL PROJECT: {user_prompt}
        YOUR CURRENT TASK: Code the '{filename}' page.
        PAGE PURPOSE: {purpose}
        ALL PAGES IN APP: {[p['filename'] for p in pages]} (Ensure Navbar links to these)
        """
        
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=f"{system_rules}\n\n{page_prompt}"
            )
            raw_html = (response.text or "").replace("```html", "").replace("```", "").strip()
            
            safe_filename = os.path.basename(filename)
            file_path = os.path.join(save_dir, safe_filename)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(raw_html)
                
            if filename == "index.html" or index_path is None:
                index_path = file_path
                
        except Exception as e:
            print(f"[CODER ERROR on {filename}]: {e}")

    print(f"\n[FULL APP COMPILED]: Successfully generated {len(pages)} pages in /{project_folder}/")
    
    # If a specific output path was requested (e.g. in Downloads folder), copy the main page there
    target_path = index_path
    if output_path:
        home = os.path.expanduser("~")
        clean_out = output_path.strip().replace("\\", "/")
        if clean_out.lower().startswith("downloads/") or clean_out.lower() == "downloads":
            sub = clean_out[10:] if clean_out.lower().startswith("downloads/") else f"{project_folder}.html"
            target_path = os.path.join(home, "Downloads", sub or f"{project_folder}.html")
        elif clean_out.lower().startswith("desktop/") or clean_out.lower() == "desktop":
            sub = clean_out[8:] if clean_out.lower().startswith("desktop/") else f"{project_folder}.html"
            target_path = os.path.join(home, "Desktop", sub or f"{project_folder}.html")
        elif not os.path.isabs(clean_out):
            target_path = os.path.join(home, "Downloads", clean_out)
        else:
            target_path = clean_out

        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            if index_path and os.path.exists(index_path):
                import shutil
                shutil.copy2(index_path, target_path)
            else:
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(f"<!DOCTYPE html><html><head><title>{user_prompt}</title><script src='https://cdn.tailwindcss.com'></script></head><body class='bg-zinc-950 text-white p-8'><h1 class='text-3xl font-bold'>{user_prompt}</h1></body></html>")
        except Exception as _ce:
            print(f"[COPY ERROR]: {_ce}")

    # Auto-open in browser so user immediately sees it
    try:
        if target_path and os.path.exists(target_path):
            os.startfile(target_path)
    except Exception as _oe:
        pass

    return target_path or index_path


# ⚡ CONTINUOUS EDIT FUNCTION (PHASE 4: THE DIFF ENGINE) ⚡
def edit_existing_web(user_prompt, existing_file_path):
    print(f"[🛠️ WEB EDIT]: Firing AST Diff Engine for {os.path.basename(existing_file_path)}...")
    
    with open(existing_file_path, 'r', encoding='utf-8') as f:
        current_code = f.read()

    client = _get_client()
    
    edit_prompt = f"""
    You are an elite Code Editor AI. The user wants to modify the following HTML file.
    Do NOT rewrite the entire file. You must act as a 'Find and Replace' engine.
    
    RULES:
    1. Output ONLY a valid JSON array of changes. No markdown, no explanations.
    2. 'search' MUST be the EXACT existing code snippet from the file (copy-pasted perfectly so Python can find it).
    3. 'replace' is the upgraded code.
    
    FORMAT EXACTLY LIKE THIS:
    [
      {{
        "search": "<button class=\"bg-blue-500\">Submit</button>",
        "replace": "<button class=\"bg-emerald-500 shadow-lg\">Submit</button>"
      }}
    ]
    
    CURRENT CODE:
    {current_code}
    
    USER REQUEST: {user_prompt}
    """
    
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=edit_prompt
        )
        raw_text = response.text or "[]"
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        changes = json.loads(clean_json)
        
        updated_code = current_code
        for change in changes:
            search_str = change.get("search", "")
            replace_str = change.get("replace", "")
            if search_str and search_str in updated_code:
                updated_code = updated_code.replace(search_str, replace_str)
                print(f"[✅ DIFF APPLIED]: Swapped target component.")
            else:
                print(f"[⚠️ DIFF WARNING]: Could not find exact search string in file.")
                
        with open(existing_file_path, 'w', encoding='utf-8') as f:
            f.write(updated_code)
            
        return existing_file_path
        
    except Exception as e:
        print(f"[⚠️ AST ERROR]: Failed to parse JSON diff. Falling back to Full Rewrite... {e}")
        fallback_prompt = f"CURRENT CODE:\n{current_code}\n\nUSER REQUEST: {user_prompt}\n\nApply changes and return full raw HTML."
        fallback_resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=fallback_prompt
        )
        updated_code = (fallback_resp.text or "").replace("```html", "").replace("```", "").strip()
        with open(existing_file_path, 'w', encoding='utf-8') as f:
            f.write(updated_code)
        return existing_file_path
