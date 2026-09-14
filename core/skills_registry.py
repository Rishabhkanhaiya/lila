"""
core/skills_registry.py — JARVIS Progressive Skill Routing Registry
===================================================================
Adopts the Anthropic Skills progressive disclosure architecture:
- Skills remain dormant with zero context bloat until triggered.
- Cheap routing step matches user intent or tool format to skill.
- Injects rigid rulebooks and design tokens right before generation.
"""

import os
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional, List
from core.jarvis_logger import log_info, log_warn


@dataclass
class JarvisSkill:
    name: str
    description: str
    triggers: List[str]
    doc_types: List[str]
    rulebook_path: Path

    def read_rulebook(self) -> str:
        """Loads the full rulebook on demand (progressive disclosure)."""
        try:
            if self.rulebook_path.exists():
                return self.rulebook_path.read_text(encoding="utf-8")
        except Exception as e:
            log_warn("skills_registry", f"Could not load rulebook for {self.name}: {e}")
        return ""


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

REGISTRY: Dict[str, JarvisSkill] = {
    "pptx": JarvisSkill(
        name="pptx",
        description="PowerPoint presentation skill enforcing 16:9 widescreen canvas, 5 deterministic layout primitives, and typography scales.",
        triggers=["slide", "slides", "deck", "presentation", "powerpoint", "ppt", "pptx", "pitch"],
        doc_types=["pptx", "presentation", "slides", "powerpoint", "presentation_outline", "deck"],
        rulebook_path=SKILLS_DIR / "pptx" / "SKILL.md"
    ),
    "docx": JarvisSkill(
        name="docx",
        description="Microsoft Word document skill enforcing typographical hierarchy, executive callout boxes, and zebra-striped tables via python-docx.",
        triggers=["word", "docx", "dotx", "doc", "memo", "contract", "brief", "formal report"],
        doc_types=["docx", "word", "doc", "memo"],
        rulebook_path=SKILLS_DIR / "docx" / "SKILL.md"
    ),
    "frontend-design": JarvisSkill(
        name="frontend-design",
        description="Web UI and frontend design skill enforcing an 8pt spacing scale, 2-font system, component assemblies, and strict anti-slop rules.",
        triggers=["html", "website", "landing page", "webpage", "ui", "dashboard", "component", "frontend", "css"],
        doc_types=["html", "website", "web", "ui", "dashboard"],
        rulebook_path=SKILLS_DIR / "frontend-design" / "SKILL.md"
    ),
    "theme-factory": JarvisSkill(
        name="theme-factory",
        description="Color palette and styling skill providing 10 curated Anthropic themes and mathematical WCAG contrast enforcement.",
        triggers=["theme", "color", "palette", "styling", "contrast", "branding"],
        doc_types=["theme", "palette", "style"],
        rulebook_path=SKILLS_DIR / "theme-factory" / "SKILL.md"
    )
}


def route_skill(topic: str = "", doc_type: str = "") -> Optional[JarvisSkill]:
    """
    Evaluates topic, intent keywords, and requested doc_type to match the appropriate skill.
    Returns the matched JarvisSkill object or None.
    """
    d_clean = (doc_type or "").strip().lower()
    t_clean = (topic or "").strip().lower()

    # 1. Exact doc_type match
    for skill in REGISTRY.values():
        if d_clean in skill.doc_types:
            return skill

    # 2. Topic trigger match
    for skill in REGISTRY.values():
        if any(trig in t_clean for trig in skill.triggers):
            return skill

    # 3. Keyword scan in doc_type
    for skill in REGISTRY.values():
        if any(trig in d_clean for trig in skill.triggers):
            return skill

    return None


def get_skill_rulebook_for_task(topic: str = "", doc_type: str = "") -> str:
    """
    One-line entry point for code generators: returns the matching rulebook body
    or empty string if no specialized skill matches.
    """
    skill = route_skill(topic, doc_type)
    if skill:
        log_info("skills_registry", f"Activated skill: {skill.name} for task: '{topic}' ({doc_type})")
        return skill.read_rulebook()
    return ""
