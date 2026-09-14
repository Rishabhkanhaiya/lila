import os
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import re

try:
    import cloudscraper as _cloudscraper_module
    _HAS_CLOUDSCRAPER = True
except ImportError:
    _HAS_CLOUDSCRAPER = False
    _cloudscraper_module = None

load_dotenv()

class WebCrawler:
    def __init__(self):
        # Load our 3 Enterprise API Keys (Wikipedia doesn't need one!)
        self.keys = {
            "Tavily": os.environ.get("TAVILY_API_KEY"),
            "Exa": os.environ.get("EXA_API_KEY"),
            "Serper": os.environ.get("SERPER_API_KEY")
        }
        # ⚡ THE ANTI-CLOUDFLARE BYPASS ENGINE (graceful: uses httpx if cloudscraper unavailable)
        if _HAS_CLOUDSCRAPER:
            self.scraper = _cloudscraper_module.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
        else:
            # Fallback scraper using httpx with browser-like headers
            import warnings
            warnings.warn("[WebCrawler] cloudscraper not installed — using httpx fallback for scraping.", stacklevel=2)
            self.scraper = None


    def search_tavily(self, query):
        url = "https://api.tavily.com/search"
        payload = {"api_key": self.keys["Tavily"], "query": query, "include_answer": True, "max_results": 5}
        response = httpx.post(url, json=payload, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        compiled = "--- TAVILY AI DATA ---\n\n"
        if data.get("answer"): compiled += f"SUMMARY: {data['answer']}\n\n"
        for r in data.get("results", []):
            title = r.get("title", "")
            compiled += f"TITLE: {title}\nSOURCE: {r['url']}\nCONTENT: {r.get('content', '')[:600]}...\n\n"
        return compiled

    def search_exa(self, query):
        url = "https://api.exa.ai/search"
        payload = {"query": query, "useAutoprompt": True, "numResults": 4, "contents": {"text": True}}
        headers = {"accept": "application/json", "content-type": "application/json", "x-api-key": self.keys["Exa"]}
        response = httpx.post(url, json=payload, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        compiled = "--- EXA NEURAL DATA ---\n\n"
        for r in data.get("results", []): 
            text = r.get('text', '')[:600]
            compiled += f"TITLE: {r.get('title', '')}\nSOURCE: {r['url']}\nCONTENT: {text}...\n\n"
        return compiled

    def search_serper(self, query):
        url = "https://google.serper.dev/search"
        payload = {"q": query, "num": 6}
        headers = {'X-API-KEY': self.keys["Serper"], 'Content-Type': 'application/json'}
        response = httpx.post(url, headers=headers, json=payload, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        compiled = "--- SERPER GOOGLE DATA ---\n\n"
        if "answerBox" in data and "snippet" in data["answerBox"]:
            compiled += f"QUICK ANSWER: {data['answerBox']['snippet']}\n\n"
        for i, r in enumerate(data.get("organic", [])[:6]):
            compiled += f"[{i+1}] {r.get('title', '')}\nSOURCE: {r.get('link', '')}\nCONTENT: {r.get('snippet', '')}\n\n"
        return compiled

    def search_wikipedia(self, query):
        """THE INFINITE FALLBACK: 100% Free, No API Key, Pure Factual Data."""
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query", "format": "json", "list": "search",
            "srsearch": query, "utf8": 1, "srlimit": 2
        }
        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        search_results = data.get("query", {}).get("search", [])
        if not search_results:
            raise Exception("No Wikipedia entries found for this topic.")
            
        compiled = "--- WIKIPEDIA FACTUAL DATA ---\n\n"
        for r in search_results:
            clean_snippet = BeautifulSoup(r['snippet'], 'html.parser').get_text()
            page_url = f"https://en.wikipedia.org/?curid={r['pageid']}"
            compiled += f"SOURCE: {page_url}\nCONTENT: {clean_snippet}...\n\n"
            
        return compiled

    def search_duckduckgo(self, query):
        """STEALTH ZERO-KEY ENGINE: Fast, live, free, undetectable web search."""
        if not query or not str(query).strip():
            return ""
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        try:
            res = httpx.post(url, data={"q": query}, headers=headers, timeout=8.0, follow_redirects=True)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            results = soup.select(".result")
            if not results:
                return ""
        except Exception:
            return ""
            
        compiled = "--- DUCKDUCKGO LIVE WEB DATA ---\n\n"
        count = 0
        for r in results:
            title_tag = r.select_one(".result__title a")
            snippet_tag = r.select_one(".result__snippet")
            if title_tag and snippet_tag:
                title = title_tag.get_text().strip()
                raw_href = title_tag.get("href", "")
                actual_url = raw_href
                if "uddg=" in raw_href:
                    import urllib.parse
                    parts = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                    if "uddg" in parts:
                        actual_url = parts["uddg"][0]
                snippet = snippet_tag.get_text().strip()
                compiled += f"TITLE: {title}\nSOURCE: {actual_url}\nCONTENT: {snippet}\n\n"
                count += 1
                if count >= 4:
                    break
        if count == 0:
            raise Exception("Could not parse result snippets from DuckDuckGo response")
        return compiled

    def execute_research(self, query, max_sources=4):
        """THE HYDRA ROUTER: Tries 5 different engines in sequence."""
        engines = [
            ("Serper (Google)", self.keys["Serper"], self.search_serper),
            ("Tavily (AI-Native)", self.keys["Tavily"], self.search_tavily),
            ("Exa (Neural)", self.keys["Exa"], self.search_exa),
            ("DuckDuckGo (Live Stealth)", "NO_KEY_NEEDED", self.search_duckduckgo),
            ("Wikipedia (Infinite Database)", "NO_KEY_NEEDED", self.search_wikipedia) 
        ]

        for name, key, search_function in engines:
            if not key:
                print(f"[HYDRA]: {name} skipped. No API key found.")
                continue
                
            print(f"\n[HYDRA]: Engaging {name} for: '{query}'...")
            try:
                result = search_function(query)
                print(f"[HYDRA]: Data successfully acquired via {name}.")
                return result
            except Exception as e:
                print(f"[HYDRA ERROR]: {name} failed ({e}). Rerouting to next engine...")

        return "Research failed: All external networks and Wikipedia are completely unreachable."

    def research(self, query: str) -> str:
        """Alias for execute_research to support background queue workers."""
        return self.execute_research(query)

    def deep_scrape(self, url: str, max_words: int = 1500) -> str:
        """Bypasses Cloudflare and extracts clean text from any target URL.
        Uses cloudscraper if available, otherwise falls back to httpx with browser headers.
        """
        try:
            print(f"  [SPIDER]: Bypassing security firewalls -> {url}")
            _HEADERS = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
            if self.scraper is not None:
                # Primary: cloudscraper (anti-Cloudflare bypass)
                response = self.scraper.get(url, timeout=15)
                text_content = response.text
                status_code = response.status_code
            else:
                # Fallback: httpx with browser headers
                resp = httpx.get(url, headers=_HEADERS, timeout=15.0, follow_redirects=True)
                text_content = resp.text
                status_code = resp.status_code

            if status_code != 200:
                print(f"  [SPIDER BLOCK]: Target hostile (Status {status_code}).")
                return ""

            soup = BeautifulSoup(text_content, 'html.parser')

            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.extract()

            text = soup.get_text(separator=' ', strip=True)
            text = re.sub(r'\s+', ' ', text)

            # Modern SPA / Dynamic fallback: if content is too short or blocked by JS
            if len(text.strip()) < 150 or "enable javascript" in text.lower():
                try:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True)
                        page = browser.new_page()
                        page.goto(url, timeout=12000, wait_until="domcontentloaded")
                        page.wait_for_timeout(1000)
                        rendered = page.inner_text("body")
                        browser.close()
                        if len(rendered.strip()) > len(text.strip()):
                            text = re.sub(r'\s+', ' ', rendered)
                except Exception:
                    pass

            return text[:max_words]
        except Exception as e:
            print(f"  [SPIDER CRASH]: Failed to breach {url} -> {e}")
            return ""


    def recursive_research(self, query):
        """The Stage 2 Engine: Searches, finds links, and clicks them to read the articles."""
        print(f"\n[🕷️ RECURSIVE SPIDER ACTIVATED]: Deep-diving for '{query}'...")
        
        base_data = self.execute_research(query)
        
        urls_to_breach = []
        urls = re.findall(r'(https?://[^\s\n]+)', base_data)
        
        for url in urls:
            if url not in urls_to_breach and 'api.' not in url and 'wikipedia.org' not in url:
                urls_to_breach.append(url)
            if len(urls_to_breach) >= 2:
                break
                
        deep_data = f"--- DEEP DIVE RESEARCH FOR: {query} ---\n\n"
        deep_data += f"[INITIAL SEARCH SUMMARY]:\n{base_data}\n\n"
        
        for url in urls_to_breach:
            deep_data += f"\n[BREACHING URL]: {url}\n"
            page_text = self.deep_scrape(url)
            if page_text:
                deep_data += f"[EXTRACTED PAGE CONTENT]: {page_text}...\n"
            else:
                deep_data += "[EXTRACTED PAGE CONTENT]: Access Denied or Empty.\n"
                
        return deep_data


# ── Global Singleton & Module-Level Functional API ──────────────────────────
_default_crawler = None

def get_crawler() -> WebCrawler:
    global _default_crawler
    if _default_crawler is None:
        _default_crawler = WebCrawler()
    return _default_crawler

def search_duckduckgo(query: str) -> str:
    """Module-level alias for DuckDuckGo search."""
    return get_crawler().search_duckduckgo(query)

def execute_research(query: str, max_sources: int = 4) -> str:
    """Module-level alias for Hydra multi-engine research."""
    return get_crawler().execute_research(query, max_sources=max_sources)

def deep_scrape(url: str, max_words: int = 1500) -> str:
    """Module-level alias for anti-bot deep scraping."""
    return get_crawler().deep_scrape(url, max_words=max_words)

def search_tavily(query: str) -> str:
    """Module-level alias for Tavily AI search."""
    return get_crawler().search_tavily(query)

def search_wikipedia(query: str) -> str:
    """Module-level alias for Wikipedia search."""
    return get_crawler().search_wikipedia(query)


if __name__ == "__main__":
    crawler = WebCrawler()
    print("--- RUNNING HYDRA DIAGNOSTICS ---")
    result = crawler.recursive_research("Quantum Computing breakthroughs")
    print("\n[DIGITAL SCRATCHPAD OUTPUT]:\n")
    print(result)