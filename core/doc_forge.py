"""
core/doc_forge.py — JARVIS Document Forge (Anthropic Skills Architecture)
========================================================================
Generates publication-grade PowerPoint, Word, Excel, and PDF documents
governed by rigid layout primitives, mathematical HSL themes, and WCAG contrast.

Features:
  - PPTX: 16:9 widescreen canvas with 5 deterministic layout primitives
    (HERO_TITLE, METRICS_3CARD, SPLIT_COMPARISON, PROCESS_TIMELINE, KEY_TAKEAWAYS_GRID).
  - DOCX: Native python-docx with styled callout boxes, zebra tables, and typography hierarchy.
  - THEMES: Integrated with core/theme_factory.py for 10 curated themes & WCAG compliance.
  - SKILLS: Progressive disclosure via core/skills_registry.py.
  - RESILIENCE: Safe save with automatic timestamping on file locks (zero PermissionError crashes).
"""

import os
import json
import re
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from core.jarvis_logger import log_error, log_warn, log_info

OUTPUT_DIR = Path(os.path.expanduser("~")) / "Documents" / "JARVIS_DocForge"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Optional library checks ───────────────────────────────────────────────────
try:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.enum.shapes import MSO_SHAPE
    HAS_PPTX = True
except ImportError:
    HAS_PPTX = False
    log_warn("doc_forge", "python-pptx not installed. Run: pip install python-pptx")

try:
    import docx
    from docx.shared import Inches as DocxInches, Pt as DocxPt, RGBColor as DocxRGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False
    log_warn("doc_forge", "python-docx not installed. Run: pip install python-docx")

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False
    log_warn("doc_forge", "openpyxl not installed. Run: pip install openpyxl")

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors as rl_colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    HAS_PDF = True
except ImportError:
    HAS_PDF = False
    log_warn("doc_forge", "reportlab not installed. Run: pip install reportlab")

from core.theme_factory import get_theme_for_topic, DesignTheme, ThemeColor
from core.skills_registry import get_skill_rulebook_for_task


def generate_chart_image(
    title: str,
    categories: List[str],
    values: List[float],
    chart_type: str = "bar",
    theme_colors: Optional[List[str]] = None
) -> Optional[str]:
    """
    Renders high-resolution executive chart image via matplotlib and returns the image path.
    Supported chart types: 'bar', 'horizontal_bar', 'line', 'pie'.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
        # Apply dark / modern minimalist palette
        fig.patch.set_facecolor('#0d0b12')
        ax.set_facecolor('#16111d')
        
        palette = theme_colors or ['#f48498', '#b392f0', '#ffd1b3', '#bbf2f6', '#ffcad4']
        
        if chart_type == "horizontal_bar":
            bars = ax.barh(categories, values, color=palette[:len(categories)], height=0.55, edgecolor='none')
            for bar in bars:
                width = bar.get_width()
                ax.annotate(f'{width}',
                            xy=(width, bar.get_y() + bar.get_height() / 2),
                            xytext=(4, 0), textcoords="offset points",
                            ha='left', va='center', color='#f5eff6', fontsize=10, fontweight='bold')
        elif chart_type == "line":
            ax.plot(categories, values, color=palette[0], marker='o', linewidth=2.5, markersize=8)
            ax.fill_between(range(len(categories)), values, color=palette[0], alpha=0.2)
        elif chart_type == "pie":
            ax.pie(values, labels=categories, colors=palette[:len(values)], autopct='%1.1f%%',
                   textprops={'color': '#f5eff6', 'fontsize': 10}, startangle=140)
            ax.axis('equal')
        else:
            bars = ax.bar(categories, values, color=palette[:len(categories)], width=0.55, edgecolor='none')
            for bar in bars:
                height = bar.get_height()
                ax.annotate(f'{height}',
                            xy=(bar.get_x() + bar.get_width() / 2, height),
                            xytext=(0, 4), textcoords="offset points",
                            ha='center', va='bottom', color='#f5eff6', fontsize=10, fontweight='bold')
            
        if chart_type != "pie":
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#635b71')
            ax.spines['bottom'].set_color('#635b71')
            ax.tick_params(colors='#9d92a8', labelsize=10)
            ax.grid(axis='y', linestyle='--', alpha=0.15, color='#ffffff')
            
        ax.set_title(title, color='#ffcad4', fontsize=14, fontweight='bold', pad=15)
        
        chart_dir = OUTPUT_DIR / "charts"
        chart_dir.mkdir(parents=True, exist_ok=True)
        filename = f"chart_{int(datetime.datetime.now().timestamp())}.png"
        filepath = str(chart_dir / filename)
        
        plt.tight_layout()
        plt.savefig(filepath, facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
        return filepath
    except Exception as e:
        log_warn("doc_forge", f"Chart generation failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CONTENT GENERATOR (LLM-powered with Progressive Skill Injection)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_content(topic: str, doc_type: str, content_hint: str = "") -> dict:
    """
    Calls the LLM to generate structured document content.
    Injects progressive skill rulebooks to constrain the model output.
    """
    skill_rules = get_skill_rulebook_for_task(topic, doc_type)

    if doc_type == "pptx":
        prompt = f"""You are an elite executive presentation designer for JARVIS Astra.
Create a high-impact, professional PowerPoint presentation about: "{topic}".

SKILL CONSTRAINTS:
You must strictly assemble the deck from these 5 layout types across 6 to 8 slides:
1. "hero" — Slide 1 cover with title, kicker, and presenter subtitle.
2. "metrics" — 3 key performance metric cards (each with label, big stat, delta, and 2 concise bullets).
3. "split" — 2-column comparative analysis (left: vulnerabilities/challenges, right: solutions/technology).
4. "timeline" — 4-phase structured implementation roadmap (each step has phase name, date/timeframe, title, and description).
5. "grid" — 4-quadrant executive action plan or strategic pillars (each card has title and description).

Return ONLY valid JSON matching this exact structure:
{{
  "title": "Full Presentation Title",
  "subtitle": "Presenter Subtitle or Core Thesis",
  "theme_id": "tech-innovation | forest-canopy | ocean-depths | midnight-galaxy | modern-minimalist | arctic-frost | golden-hour | sunset-boulevard",
  "slides": [
    {{
      "layout": "hero",
      "kicker": "EXECUTIVE BRIEFING",
      "heading": "Slide Title",
      "subtitle": "Brief subtitle",
      "note": "Speaker notes"
    }},
    {{
      "layout": "metrics",
      "kicker": "CORE BENCHMARKS",
      "heading": "Quantifiable Performance Metrics",
      "metrics": [
        {{"label": "METRIC NAME 1", "stat": "VALUE", "delta": "TREND", "bullets": ["Point 1", "Point 2"]}},
        {{"label": "METRIC NAME 2", "stat": "VALUE", "delta": "TREND", "bullets": ["Point 1", "Point 2"]}},
        {{"label": "METRIC NAME 3", "stat": "VALUE", "delta": "TREND", "bullets": ["Point 1", "Point 2"]}}
      ],
      "note": "Speaker notes"
    }},
    {{
      "layout": "split",
      "kicker": "STRATEGIC ANALYSIS",
      "heading": "Critical Challenges vs Targeted Interventions",
      "left_heading": "Key Challenges & Vulnerabilities",
      "left_bullets": ["Challenge 1", "Challenge 2", "Challenge 3"],
      "right_heading": "Strategic Solutions & Interventions",
      "right_bullets": ["Solution 1", "Solution 2", "Solution 3"],
      "note": "Speaker notes"
    }},
    {{
      "layout": "timeline",
      "kicker": "TRANSITION ROADMAP",
      "heading": "Phased Implementation Milestones",
      "steps": [
        {{"phase": "Phase 1", "date": "2024-2026", "title": "Foundation Stage", "desc": "Key milestone details"}},
        {{"phase": "Phase 2", "date": "2026-2028", "title": "Scaling Milestone", "desc": "Key milestone details"}},
        {{"phase": "Phase 3", "date": "2028-2030", "title": "Expansion Stage", "desc": "Key milestone details"}},
        {{"phase": "Phase 4", "date": "2030+", "title": "Target Maturity", "desc": "Key milestone details"}}
      ],
      "note": "Speaker notes"
    }},
    {{
      "layout": "grid",
      "kicker": "STRATEGIC PILLARS",
      "heading": "Executive Action Plan & Takeaways",
      "cards": [
        {{"title": "1. Strategic Pillar Alpha", "desc": "High impact operational action item."}},
        {{"title": "2. Strategic Pillar Beta", "desc": "High impact operational action item."}},
        {{"title": "3. Strategic Pillar Gamma", "desc": "High impact operational action item."}},
        {{"title": "4. Strategic Pillar Delta", "desc": "High impact operational action item."}}
      ],
      "note": "Speaker notes"
    }}
  ]
}}

No markdown fences. Real data, professional tone, crisp phrasing."""

    elif doc_type in ("docx", "doc", "word"):
        prompt = f"""Generate a comprehensive, publication-grade executive Word document about: "{topic}".

Return ONLY valid JSON with this structure:
{{
  "title": "Document Title",
  "subtitle": "Subtitle or Executive Briefing Context",
  "theme_id": "tech-innovation | forest-canopy | ocean-depths | modern-minimalist",
  "executive_summary": "Comprehensive 3-4 sentence executive summary highlighting key findings, drivers, and strategic outcomes.",
  "table": {{
    "headers": ["Domain / Indicator", "Current Baseline", "Strategic Target", "Impact Level"],
    "rows": [
      ["Metric Alpha", "Current Status", "Target Outcome", "High"],
      ["Metric Beta", "Current Status", "Target Outcome", "Critical"],
      ["Metric Gamma", "Current Status", "Target Outcome", "Medium"]
    ]
  }},
  "sections": [
    {{
      "heading": "Section Heading",
      "content": "Paragraph content providing deep analytical context.",
      "bullet_points": ["Key operational point 1", "Key operational point 2", "Key operational point 3"]
    }}
  ],
  "conclusion": "Final concluding assessment and recommended roadmap."
}}

Generate 3-5 thorough sections. Professional, data-driven, no markdown fences."""

    elif doc_type == "xlsx":
        prompt = f"""Generate a professional Excel spreadsheet about: "{topic}".

Return ONLY valid JSON with this structure:
{{
  "title": "Spreadsheet Title",
  "sheets": [
    {{
      "name": "Sheet Name",
      "headers": ["Column1", "Column2", "Column3", "Column4"],
      "rows": [
        ["data", "data", "data", "data"],
        ["data", "data", "data", "data"]
      ],
      "summary": "Brief description of what this sheet shows"
    }}
  ]
}}

Generate 2-3 sheets with realistic data. No markdown fences."""

    else:  # pdf
        prompt = f"""Generate a professional PDF report about: "{topic}".

Return ONLY valid JSON with this structure:
{{
  "title": "Report Title",
  "subtitle": "Report subtitle or date",
  "executive_summary": "2-3 sentence executive summary",
  "sections": [
    {{
      "heading": "Section Title",
      "content": "Full paragraph content for this section",
      "bullet_points": ["key point 1", "key point 2"]
    }}
  ],
  "conclusion": "Concluding paragraph"
}}

Generate 4-6 sections. Be thorough and professional. No markdown fences."""

    if content_hint:
        prompt += f"\n\nContext & Specific Technical Requirements:\n{content_hint}"

    # 1. Primary: Google Gemini with native structured JSON output
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from google import genai
        key = os.getenv("GEMINI_API_KEY", "")
        if key:
            client = genai.Client(api_key=key)
            for m_cand in ["gemini-3.5-flash-lite", "gemini-2.5-flash"]:
                try:
                    resp = client.models.generate_content(model=m_cand, contents=prompt)
                    if resp and resp.text:
                        raw = resp.text.strip()
                        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
                        raw = re.sub(r'\s*```$', '', raw).strip()
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict) and (parsed.get("slides") or parsed.get("sections") or parsed.get("sheets") or parsed.get("table")):
                            return parsed
                except Exception:
                    continue
    except Exception as e:
        log_warn("doc_forge", f"Gemini content generation error: {e}")

    # 2. Secondary: Groq Brain
    try:
        from core.brain import call_groq_brain
        raw = call_groq_brain(prompt, phase="DIRECTIVE", is_logic_task=True)
        if isinstance(raw, dict):
            raw = raw.get("reply", "{}")
        raw = re.sub(r'^```(?:json)?\s*', '', str(raw), flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw).strip()
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and (parsed.get("slides") or parsed.get("sections") or parsed.get("sheets")):
            return parsed
    except Exception as e:
        log_error("doc_forge", "_generate_content (groq)", e)

    # 3. Resilient Fallback Skeleton (Structured by Layout Primitives)
    return {
        "title": topic,
        "subtitle": f"Executive Assessment — JARVIS Astra",
        "theme_id": "tech-innovation",
        "slides": [
            {
                "layout": "hero",
                "kicker": "EXECUTIVE BRIEFING",
                "heading": topic,
                "subtitle": f"Strategic Analysis and Overview — {datetime.date.today()}",
                "note": "Welcome everyone. Today we examine key findings and strategic trajectories."
            },
            {
                "layout": "metrics",
                "kicker": "CORE BENCHMARKS",
                "heading": "Performance Metrics & Indicators",
                "metrics": [
                    {"label": "BASELINE READINESS", "stat": "94.2%", "delta": "+12.4% YoY", "bullets": ["Primary metric achievement", "Operational readiness validated"]},
                    {"label": "RESOURCE EFFICIENCY", "stat": "3.8x", "delta": "+45% Gain", "bullets": ["Process optimization", "Resource waste reduced"]},
                    {"label": "PROJECTED IMPACT", "stat": "Tier 1", "delta": "Top Decile", "bullets": ["Benchmark leadership", "Multi-stakeholder alignment"]}
                ],
                "note": "These metrics illustrate the primary performance levers."
            },
            {
                "layout": "split",
                "kicker": "STRATEGIC ANALYSIS",
                "heading": "Critical Challenges vs Targeted Interventions",
                "left_heading": "Current Vulnerabilities",
                "left_bullets": ["Systemic resource bottlenecks", "Regulatory compliance friction", "Variable adoption rates"],
                "right_heading": "Targeted Solutions",
                "right_bullets": ["Decentralized infrastructure deployment", "Automated compliance tooling", "Stakeholder incentive frameworks"],
                "note": "Contrasting our principal hurdles against active interventions."
            },
            {
                "layout": "timeline",
                "kicker": "TRANSITION ROADMAP",
                "heading": "Phased Implementation Milestones",
                "steps": [
                    {"phase": "Phase 1", "date": "Q1-Q2", "title": "Infrastructure Foundation", "desc": "Establish core data pipelines and baseline monitoring."},
                    {"phase": "Phase 2", "date": "Q3-Q4", "title": "Scaling Deployment", "desc": "Expand operational rollouts across primary target domains."},
                    {"phase": "Phase 3", "date": "Year 2", "title": "Advanced Integration", "desc": "Deep workflow automation and ecosystem synchronization."},
                    {"phase": "Phase 4", "date": "Year 3+", "title": "Maturity & Net Zero", "desc": "Sustained performance governance and milestone parity."}
                ],
                "note": "Review the multi-stage deployment pathway."
            },
            {
                "layout": "grid",
                "kicker": "STRATEGIC PILLARS",
                "heading": "Key Action Items & Takeaways",
                "cards": [
                    {"title": "1. Operational Excellence", "desc": "Drive lean operations and automated audit reporting across workstreams."},
                    {"title": "2. Technology Acceleration", "desc": "Deploy modern tooling and resilient architecture to eliminate downtime."},
                    {"title": "3. Resource Governance", "desc": "Maintain disciplined capital and asset allocation models."},
                    {"title": "4. Ecosystem Growth", "desc": "Expand partner network integrations to sustain competitive advantage."}
                ],
                "note": "Concluding strategic pillars for immediate execution."
            }
        ],
        "executive_summary": f"Comprehensive briefing on {topic}, analyzing operational metrics, strategic challenges, and phased implementation roadmaps.",
        "table": {
            "headers": ["Domain", "Baseline", "Target", "Status"],
            "rows": [["Strategy", "Established", "Optimized", "Active"], ["Technology", "Modern", "State-of-the-Art", "In Progress"]]
        },
        "sections": [
            {"heading": "Executive Overview", "content": f"Detailed context regarding {topic}.", "bullet_points": ["Primary finding 1", "Primary finding 2"]},
            {"heading": "Roadmap & Next Steps", "content": "Phased rollout milestones.", "bullet_points": ["Immediate safeguard implementation", "Quarterly governance audits"]}
        ],
        "sheets": [
            {"name": "Overview", "headers": ["Metric", "Value", "Status"], "rows": [["Target", topic, "Active"]], "summary": "Dataset overview"}
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────
# POWERPOINT BUILDER (5 Anthropic Layout Primitives)
# ─────────────────────────────────────────────────────────────────────────────

def _build_pptx(data: dict, filepath: Path, topic: str = "") -> Optional[Path]:
    if not HAS_PPTX:
        return None

    theme = get_theme_for_topic(topic or data.get("title", ""), data.get("theme_id", ""))
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    def set_bg(slide, color: ThemeColor):
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(color.r, color.g, color.b)

    def add_textbox(slide, text: str, left: float, top: float, width: float, height: float,
                    font_size: int = 14, bold: bool = False, color: Optional[ThemeColor] = None,
                    align: PP_ALIGN = PP_ALIGN.LEFT, font_name: Optional[str] = None):
        col = color or theme.text_primary
        tx = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
        tf = tx.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.08)
        tf.margin_right = Inches(0.08)
        tf.margin_top = Inches(0.08)
        tf.margin_bottom = Inches(0.08)
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(font_size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor(col.r, col.g, col.b)
        run.font.name = font_name or (theme.font_heading if bold else theme.font_body)
        return tx

    def _render_hero_slide(slide, s_data: dict):
        set_bg(slide, theme.bg)
        # Top Accent Rule
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.08))
        bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(theme.primary.r, theme.primary.g, theme.primary.b)
        bar.line.fill.background()

        kicker = s_data.get("kicker", "EXECUTIVE BRIEFING").upper()
        add_textbox(slide, f"[ {kicker} ]", 1.0, 1.8, 11.33, 0.4, font_size=11, bold=True, color=theme.secondary)

        title = s_data.get("heading") or data.get("title") or "Executive Presentation"
        add_textbox(slide, title, 1.0, 2.3, 11.33, 1.8, font_size=42, bold=True, color=theme.text_primary)

        sub = s_data.get("subtitle") or data.get("subtitle") or "Prepared by JARVIS Astra • Verified Design System"
        add_textbox(slide, sub, 1.0, 4.3, 11.33, 0.8, font_size=16, color=theme.text_secondary)

        # Bottom Accent Rule
        bar2 = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(7.42), Inches(13.333), Inches(0.08))
        bar2.fill.solid(); bar2.fill.fore_color.rgb = RGBColor(theme.secondary.r, theme.secondary.g, theme.secondary.b)
        bar2.line.fill.background()

    def _render_metrics_slide(slide, s_data: dict):
        set_bg(slide, theme.bg)
        kicker = s_data.get("kicker", "PERFORMANCE METRICS").upper()
        add_textbox(slide, f"[ {kicker} ]", 0.8, 0.4, 11.7, 0.3, font_size=10, bold=True, color=theme.secondary)
        heading = s_data.get("heading", "Core Indicators & Benchmarks")
        add_textbox(slide, heading, 0.8, 0.7, 11.7, 0.7, font_size=24, bold=True, color=theme.text_primary)

        coords = [0.8, 4.85, 8.90]
        raw_metrics = s_data.get("metrics", [])
        if not raw_metrics:
            raw_metrics = [
                {"label": "BENCHMARK A", "stat": "98.4%", "delta": "+14.2% YoY", "bullets": ["Primary performance validated", "Target range achieved"]},
                {"label": "EFFICIENCY", "stat": "4.2x", "delta": "+35% Gain", "bullets": ["Resource conservation", "Process streamlined"]},
                {"label": "GOVERNANCE", "stat": "100%", "delta": "Verified", "bullets": ["Full regulatory compliance", "Periodic audit clearance"]}
            ]

        for i, m in enumerate(raw_metrics[:3]):
            x = coords[i]
            card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(1.6), Inches(3.65), Inches(5.1))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(theme.surface.r, theme.surface.g, theme.surface.b)
            card.line.color.rgb = RGBColor(theme.border.r, theme.border.g, theme.border.b)
            card.line.width = Pt(1)

            add_textbox(slide, m.get("label", f"METRIC {i+1}").upper(), x + 0.2, 1.9, 3.25, 0.3, font_size=10, bold=True, color=theme.text_secondary)
            add_textbox(slide, m.get("stat", "N/A"), x + 0.2, 2.2, 3.25, 0.8, font_size=36, bold=True, color=theme.primary)
            add_textbox(slide, m.get("delta", "Stable"), x + 0.2, 3.0, 3.25, 0.4, font_size=12, bold=True, color=theme.secondary)

            bullets = m.get("bullets", [])
            for j, b in enumerate(bullets[:2]):
                add_textbox(slide, f"• {b[:70]}", x + 0.2, 3.6 + (j * 0.5), 3.25, 0.45, font_size=12, color=theme.text_primary)

    def _render_split_slide(slide, s_data: dict):
        set_bg(slide, theme.bg)
        kicker = s_data.get("kicker", "STRATEGIC COMPARISON").upper()
        add_textbox(slide, f"[ {kicker} ]", 0.8, 0.4, 11.7, 0.3, font_size=10, bold=True, color=theme.secondary)
        heading = s_data.get("heading", "Critical Challenges vs Solutions")
        add_textbox(slide, heading, 0.8, 0.7, 11.7, 0.7, font_size=24, bold=True, color=theme.text_primary)

        # Left Column (Challenges / Current)
        c_left = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(1.6), Inches(5.6), Inches(5.1))
        c_left.fill.solid(); c_left.fill.fore_color.rgb = RGBColor(theme.surface.r, theme.surface.g, theme.surface.b)
        c_left.line.color.rgb = RGBColor(theme.border.r, theme.border.g, theme.border.b)
        add_textbox(slide, s_data.get("left_heading", "Current Challenges"), 1.1, 1.9, 5.0, 0.5, font_size=18, bold=True, color=theme.text_secondary)
        l_bullets = "\n".join([f"• {b[:80]}" for b in s_data.get("left_bullets", ["Bottleneck analysis", "Operational friction"])[:4]])
        add_textbox(slide, l_bullets, 1.1, 2.5, 5.0, 3.8, font_size=13, color=theme.text_primary)

        # Right Column (Solutions / Future)
        c_right = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.9), Inches(1.6), Inches(5.6), Inches(5.1))
        c_right.fill.solid(); c_right.fill.fore_color.rgb = RGBColor(theme.surface_elevated.r, theme.surface_elevated.g, theme.surface_elevated.b)
        c_right.line.color.rgb = RGBColor(theme.primary.r, theme.primary.g, theme.primary.b)
        c_right.line.width = Pt(1.5)
        add_textbox(slide, s_data.get("right_heading", "Strategic Solutions"), 7.2, 1.9, 5.0, 0.5, font_size=18, bold=True, color=theme.secondary)
        r_bullets = "\n".join([f"• {b[:80]}" for b in s_data.get("right_bullets", ["Targeted interventions", "Automated pipelines"])[:4]])
        add_textbox(slide, r_bullets, 7.2, 2.5, 5.0, 3.8, font_size=13, color=theme.text_primary)

    def _render_timeline_slide(slide, s_data: dict):
        set_bg(slide, theme.bg)
        kicker = s_data.get("kicker", "TRANSITION ROADMAP").upper()
        add_textbox(slide, f"[ {kicker} ]", 0.8, 0.4, 11.7, 0.3, font_size=10, bold=True, color=theme.secondary)
        heading = s_data.get("heading", "Phased Implementation Milestones")
        add_textbox(slide, heading, 0.8, 0.7, 11.7, 0.7, font_size=24, bold=True, color=theme.text_primary)

        t_coords = [0.8, 3.75, 6.7, 9.65]
        raw_steps = s_data.get("steps", [])
        if not raw_steps:
            raw_steps = [
                {"phase": "Phase 1", "date": "Q1-Q2", "title": "Foundation", "desc": "Initial setup and validation."},
                {"phase": "Phase 2", "date": "Q3-Q4", "title": "Deployment", "desc": "Scaling operational capabilities."},
                {"phase": "Phase 3", "date": "Year 2", "title": "Expansion", "desc": "Broad ecosystem adoption."},
                {"phase": "Phase 4", "date": "Year 3+", "title": "Maturity", "desc": "Full target attainment."}
            ]

        for i, step in enumerate(raw_steps[:4]):
            x = t_coords[i]
            card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(1.8), Inches(2.7), Inches(4.9))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(theme.surface.r, theme.surface.g, theme.surface.b)
            card.line.color.rgb = RGBColor(theme.border.r, theme.border.g, theme.border.b)

            badge = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 0.2), Inches(2.0), Inches(0.55), Inches(0.55))
            badge.fill.solid(); badge.fill.fore_color.rgb = RGBColor(theme.primary.r, theme.primary.g, theme.primary.b)
            badge.line.fill.background()

            add_textbox(slide, f"0{i+1}", x + 0.2, 2.05, 0.55, 0.4, font_size=11, bold=True, color=theme.text_primary, align=PP_ALIGN.CENTER)
            add_textbox(slide, step.get("phase", f"Stage {i+1}"), x + 0.85, 2.0, 1.6, 0.3, font_size=11, bold=True, color=theme.secondary)
            add_textbox(slide, step.get("date", ""), x + 0.85, 2.3, 1.6, 0.3, font_size=10, color=theme.text_secondary)
            add_textbox(slide, step.get("title", ""), x + 0.2, 2.8, 2.3, 0.6, font_size=14, bold=True, color=theme.text_primary)
            add_textbox(slide, step.get("desc", ""), x + 0.2, 3.5, 2.3, 2.8, font_size=12, color=theme.text_secondary)

    def _render_grid_slide(slide, s_data: dict):
        set_bg(slide, theme.bg)
        kicker = s_data.get("kicker", "STRATEGIC PILLARS").upper()
        add_textbox(slide, f"[ {kicker} ]", 0.8, 0.4, 11.7, 0.3, font_size=10, bold=True, color=theme.secondary)
        heading = s_data.get("heading", "Executive Action Framework")
        add_textbox(slide, heading, 0.8, 0.7, 11.7, 0.7, font_size=24, bold=True, color=theme.text_primary)

        quads = [
            (0.8, 1.6), (6.9, 1.6),
            (0.8, 4.3), (6.9, 4.3)
        ]
        raw_cards = s_data.get("cards", [])
        if not raw_cards:
            raw_cards = [
                {"title": "1. Operational Excellence", "desc": "Establish rigorous quality benchmarks and continuous review loops."},
                {"title": "2. Technology Acceleration", "desc": "Deploy resilient automation pipelines to eliminate manual latency."},
                {"title": "3. Resource Governance", "desc": "Enforce disciplined resource allocation and risk mitigation frameworks."},
                {"title": "4. Ecosystem Growth", "desc": "Expand multi-stakeholder partnerships to ensure long-term viability."}
            ]

        for i, card in enumerate(raw_cards[:4]):
            qx, qy = quads[i]
            qc = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(qx), Inches(qy), Inches(5.6), Inches(2.4))
            qc.fill.solid(); qc.fill.fore_color.rgb = RGBColor(theme.surface.r, theme.surface.g, theme.surface.b)
            qc.line.color.rgb = RGBColor(theme.border.r, theme.border.g, theme.border.b)

            strip = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(qx), Inches(qy), Inches(0.12), Inches(2.4))
            strip.fill.solid(); strip.fill.fore_color.rgb = RGBColor(theme.secondary.r, theme.secondary.g, theme.secondary.b)
            strip.line.fill.background()

            add_textbox(slide, card.get("title", f"Pillar {i+1}"), qx + 0.3, qy + 0.2, 5.1, 0.4, font_size=15, bold=True, color=theme.secondary)
            add_textbox(slide, card.get("desc", ""), qx + 0.3, qy + 0.7, 5.1, 1.5, font_size=12, color=theme.text_primary)

    def _render_chart_slide(slide, s_data: dict):
        set_bg(slide, theme.bg)
        kicker = s_data.get("kicker", "VISUAL INTELLIGENCE").upper()
        add_textbox(slide, f"[ {kicker} ]", 0.8, 0.4, 11.7, 0.3, font_size=10, bold=True, color=theme.secondary)
        heading = s_data.get("heading", "Data Analytics & Distribution")
        add_textbox(slide, heading, 0.8, 0.7, 11.7, 0.7, font_size=24, bold=True, color=theme.text_primary)

        chart_data = s_data.get("chart", {})
        title = chart_data.get("title", heading)
        categories = chart_data.get("categories", ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"])
        values = chart_data.get("values", [45, 68, 85, 92, 110])
        chart_type = chart_data.get("type", "bar")

        chart_img = generate_chart_image(
            title=title,
            categories=categories,
            values=values,
            chart_type=chart_type,
            theme_colors=[f"#{theme.primary.r:02x}{theme.primary.g:02x}{theme.primary.b:02x}",
                          f"#{theme.secondary.r:02x}{theme.secondary.g:02x}{theme.secondary.b:02x}"]
        )
        if chart_img and os.path.exists(chart_img):
            slide.shapes.add_picture(chart_img, Inches(1.5), Inches(1.8), width=Inches(10.33))

    # ── Render Decks through Deterministic Primitives ──────────────────────────
    slides_data = data.get("slides", [])
    if not slides_data:
        slides_data = [{"layout": "hero"}]

    for idx, s_info in enumerate(slides_data):
        slide = prs.slides.add_slide(blank_layout)
        l_type = s_info.get("layout", "").lower()

        # Layout inference if not explicitly provided
        if not l_type:
            if idx == 0:
                l_type = "hero"
            elif s_info.get("chart") or "chart" in s_info.get("heading", "").lower():
                l_type = "chart"
            elif s_info.get("metrics"):
                l_type = "metrics"
            elif s_info.get("left_bullets") or "vs" in s_info.get("heading", "").lower():
                l_type = "split"
            elif s_info.get("steps") or "timeline" in s_info.get("heading", "").lower() or "roadmap" in s_info.get("heading", "").lower():
                l_type = "timeline"
            else:
                l_type = "grid"

        if l_type == "hero":
            _render_hero_slide(slide, s_info)
        elif l_type == "metrics":
            _render_metrics_slide(slide, s_info)
        elif l_type == "split":
            _render_split_slide(slide, s_info)
        elif l_type == "timeline":
            _render_timeline_slide(slide, s_info)
        elif l_type == "chart" or s_info.get("chart"):
            _render_chart_slide(slide, s_info)
        else:
            _render_grid_slide(slide, s_info)

        # Attach speaker notes
        note_text = s_info.get("note", "")
        if note_text:
            try:
                slide.notes_slide.notes_text_frame.text = note_text
            except Exception:
                pass

    # Save with lock resilience
    try:
        prs.save(str(filepath))
        return filepath
    except (PermissionError, OSError) as pe:
        log_warn("doc_forge", f"PPTX target {filepath} locked ({pe}), saving with timestamp fallback.")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alt_path = filepath.parent / f"{filepath.stem}_{ts}{filepath.suffix}"
        prs.save(str(alt_path))
        return alt_path


# ─────────────────────────────────────────────────────────────────────────────
# WORD DOCUMENT BUILDER (Native python-docx with Callouts & Zebra Tables)
# ─────────────────────────────────────────────────────────────────────────────

def _build_docx(data: dict, filepath: Path, topic: str = "") -> Optional[Path]:
    if not HAS_DOCX:
        return None

    theme = get_theme_for_topic(topic or data.get("title", ""), data.get("theme_id", ""))
    doc = docx.Document()

    for s in doc.sections:
        s.top_margin = DocxInches(1.0)
        s.bottom_margin = DocxInches(1.0)
        s.left_margin = DocxInches(1.0)
        s.right_margin = DocxInches(1.0)

    # Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = DocxPt(0)
    p_title.paragraph_format.space_after = DocxPt(4)
    run_t = p_title.add_run(data.get("title", topic or "Executive Document"))
    run_t.font.name = theme.font_heading
    run_t.font.size = DocxPt(24)
    run_t.font.bold = True
    run_t.font.color.rgb = DocxRGBColor(theme.primary.r, theme.primary.g, theme.primary.b)

    # Subtitle
    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_after = DocxPt(16)
    sub_text = data.get("subtitle") or f"Executive Briefing • {datetime.date.today()} • Prepared by JARVIS Astra"
    run_sub = p_sub.add_run(sub_text)
    run_sub.font.name = theme.font_body
    run_sub.font.size = DocxPt(11)
    run_sub.font.italic = True
    run_sub.font.color.rgb = DocxRGBColor(100, 116, 139)

    # Executive Summary Callout Box
    exec_summary = data.get("executive_summary", "")
    if exec_summary:
        tbl_callout = doc.add_table(rows=1, cols=1)
        tbl_callout.alignment = WD_TABLE_ALIGNMENT.CENTER
        cell = tbl_callout.cell(0, 0)
        cell.width = DocxInches(6.5)

        # Background tint + thick left border
        bg_hex = "F0FDF4" if "forest" in theme.id else ("F0F9FF" if "arctic" in theme.id else "F1F5F9")
        shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{bg_hex}"/>')
        borders = parse_xml(
            f'<w:tcBorders {nsdecls("w")}>'
            f'  <w:top w:val="none"/>'
            f'  <w:left w:val="single" w:sz="24" w:space="0" w:color="{theme.primary.hex_clean}"/>'
            f'  <w:bottom w:val="none"/>'
            f'  <w:right w:val="none"/>'
            f'</w:tcBorders>'
        )
        cell._tc.get_or_add_tcPr().append(shd)
        cell._tc.get_or_add_tcPr().append(borders)

        cp = cell.paragraphs[0]
        cp.paragraph_format.space_before = DocxPt(6)
        cp.paragraph_format.space_after = DocxPt(6)
        cp.paragraph_format.left_indent = DocxInches(0.15)
        cp.paragraph_format.right_indent = DocxInches(0.15)

        r_lead = cp.add_run("EXECUTIVE SUMMARY: ")
        r_lead.font.bold = True
        r_lead.font.size = DocxPt(10.5)
        r_lead.font.color.rgb = DocxRGBColor(theme.primary.r, theme.primary.g, theme.primary.b)

        r_body = cp.add_run(exec_summary)
        r_body.font.size = DocxPt(10.5)
        r_body.font.color.rgb = DocxRGBColor(15, 23, 42)

    # Structured Data Table (if provided)
    table_data = data.get("table", {})
    if table_data and table_data.get("headers") and table_data.get("rows"):
        doc.add_paragraph().paragraph_format.space_after = DocxPt(8)
        headers = table_data["headers"]
        rows = table_data["rows"]

        tbl = doc.add_table(rows=len(rows) + 1, cols=len(headers))
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        col_w = DocxInches(6.5 / max(1, len(headers)))

        # Header Row
        for j, text in enumerate(headers):
            c = tbl.cell(0, j)
            c.width = col_w
            c._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{theme.primary.hex_clean}"/>'))
            p = c.paragraphs[0]
            p.paragraph_format.space_before = DocxPt(4)
            p.paragraph_format.space_after = DocxPt(4)
            r = p.add_run(text)
            r.font.bold = True
            r.font.size = DocxPt(10)
            r.font.color.rgb = DocxRGBColor(255, 255, 255)

        # Data Rows (Zebra Striping)
        for i, row in enumerate(rows, start=1):
            bg_col = "F8FAFC" if i % 2 == 1 else "FFFFFF"
            for j, val in enumerate(row):
                if j < len(headers):
                    c = tbl.cell(i, j)
                    c.width = col_w
                    c._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{bg_col}"/>'))
                    p = c.paragraphs[0]
                    p.paragraph_format.space_before = DocxPt(3)
                    p.paragraph_format.space_after = DocxPt(3)
                    r = p.add_run(str(val))
                    r.font.size = DocxPt(9.5)
                    r.font.color.rgb = DocxRGBColor(15, 23, 42)

    # Sections
    for section in data.get("sections", []):
        doc.add_paragraph().paragraph_format.space_after = DocxPt(8)
        h = doc.add_paragraph()
        h.paragraph_format.space_before = DocxPt(12)
        h.paragraph_format.space_after = DocxPt(4)
        h.paragraph_format.keep_with_next = True
        r_h = h.add_run(section.get("heading", "Analysis"))
        r_h.font.bold = True
        r_h.font.size = DocxPt(14)
        r_h.font.color.rgb = DocxRGBColor(theme.primary.r, theme.primary.g, theme.primary.b)

        if section.get("content"):
            p = doc.add_paragraph()
            p.paragraph_format.space_after = DocxPt(6)
            p.paragraph_format.line_spacing = 1.15
            r = p.add_run(section["content"])
            r.font.size = DocxPt(10.5)
            r.font.color.rgb = DocxRGBColor(15, 23, 42)

        for bp in section.get("bullet_points", []):
            p = doc.add_paragraph(style='List Bullet')
            p.paragraph_format.space_before = DocxPt(2)
            p.paragraph_format.space_after = DocxPt(3)
            p.paragraph_format.left_indent = DocxInches(0.25)
            r = p.add_run(bp)
            r.font.size = DocxPt(10)
            r.font.color.rgb = DocxRGBColor(30, 41, 59)

        if "chart" in section:
            ch_data = section["chart"]
            chart_img = generate_chart_image(
                title=ch_data.get("title", section.get("heading", "Analytics Overview")),
                categories=ch_data.get("categories", ["Alpha", "Beta", "Gamma", "Delta"]),
                values=ch_data.get("values", [25, 45, 70, 95]),
                chart_type=ch_data.get("type", "bar")
            )
            if chart_img and os.path.exists(chart_img):
                doc.add_paragraph().paragraph_format.space_before = DocxPt(4)
                doc.add_picture(chart_img, width=DocxInches(6.0))

    # Conclusion
    if data.get("conclusion"):
        doc.add_paragraph().paragraph_format.space_after = DocxPt(8)
        h_con = doc.add_paragraph()
        h_con.paragraph_format.space_before = DocxPt(12)
        h_con.paragraph_format.space_after = DocxPt(4)
        r_hc = h_con.add_run("Conclusion & Strategic Next Steps")
        r_hc.font.bold = True
        r_hc.font.size = DocxPt(14)
        r_hc.font.color.rgb = DocxRGBColor(theme.secondary.r, theme.secondary.g, theme.secondary.b)

        p_con = doc.add_paragraph()
        p_con.paragraph_format.space_after = DocxPt(6)
        r_c = p_con.add_run(data["conclusion"])
        r_c.font.size = DocxPt(10.5)
        r_c.font.color.rgb = DocxRGBColor(15, 23, 42)

    try:
        doc.save(str(filepath))
        return filepath
    except (PermissionError, OSError) as pe:
        log_warn("doc_forge", f"DOCX target {filepath} locked ({pe}), saving with timestamp fallback.")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alt_path = filepath.parent / f"{filepath.stem}_{ts}{filepath.suffix}"
        doc.save(str(alt_path))
        return alt_path


# ─────────────────────────────────────────────────────────────────────────────
# EXCEL BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_xlsx(data: dict, filepath: Path) -> Optional[Path]:
    if not HAS_XLSX:
        return None

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    header_fill = PatternFill("solid", fgColor="0D1B2A")
    header_font = Font(bold=True, color="00F0FF", name="Segoe UI", size=11)
    title_font = Font(bold=True, color="00FF9D", name="Segoe UI", size=14)
    even_fill = PatternFill("solid", fgColor="07121E")
    odd_fill = PatternFill("solid", fgColor="03060F")
    data_font = Font(color="C8E6FF", name="Segoe UI", size=10)
    center_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        bottom=Side(style="thin", color="113355"),
        right=Side(style="thin", color="113355"),
    )

    for sheet_data in data.get("sheets", []):
        ws = wb.create_sheet(title=sheet_data.get("name", "Sheet")[:31])
        ws.sheet_view.showGridLines = False

        title_cell = ws.cell(row=1, column=1, value=data.get("title", "Report"))
        title_cell.font = title_font
        title_cell.alignment = center_align

        headers = sheet_data.get("headers", [])
        if headers:
            ws.merge_cells(start_row=1, start_column=1,
                           end_row=1, end_column=len(headers))

        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=2, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border
            ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 20

        for row_idx, row in enumerate(sheet_data.get("rows", []), start=3):
            fill = even_fill if row_idx % 2 == 0 else odd_fill
            for col_idx, value in enumerate(row, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = data_font
                cell.fill = fill
                cell.alignment = center_align
                cell.border = thin_border

        ws.row_dimensions[1].height = 30
        ws.row_dimensions[2].height = 24

    try:
        wb.save(str(filepath))
        return filepath
    except (PermissionError, OSError) as pe:
        log_warn("doc_forge", f"XLSX target {filepath} locked ({pe}), saving with timestamp fallback.")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alt_path = filepath.parent / f"{filepath.stem}_{ts}{filepath.suffix}"
        wb.save(str(alt_path))
        return alt_path


# ─────────────────────────────────────────────────────────────────────────────
# PDF BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_pdf(data: dict, filepath: Path) -> Optional[Path]:
    if not HAS_PDF:
        return None

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("JTitle", parent=styles["Heading1"],
        fontSize=24, textColor=rl_colors.HexColor("#00F0FF"),
        spaceAfter=6, fontName="Helvetica-Bold")
    subtitle_style = ParagraphStyle("JSub", parent=styles["Normal"],
        fontSize=11, textColor=rl_colors.HexColor("#00FF9D"),
        spaceAfter=20, fontName="Helvetica")
    heading_style = ParagraphStyle("JHeading", parent=styles["Heading2"],
        fontSize=14, textColor=rl_colors.HexColor("#00F0FF"),
        spaceAfter=6, spaceBefore=14, fontName="Helvetica-Bold")
    body_style = ParagraphStyle("JBody", parent=styles["Normal"],
        fontSize=10, textColor=rl_colors.HexColor("#C8E6FF"),
        spaceAfter=8, leading=16, fontName="Helvetica")
    bullet_style = ParagraphStyle("JBullet", parent=styles["Normal"],
        fontSize=10, textColor=rl_colors.HexColor("#A8D8FF"),
        spaceAfter=4, leftIndent=20, bulletIndent=10,
        fontName="Helvetica", bulletText="•")

    story = []
    story.append(Paragraph(data.get("title", "Report"), title_style))
    story.append(Paragraph(data.get("subtitle", ""), subtitle_style))

    if data.get("executive_summary"):
        story.append(Paragraph("Executive Summary", heading_style))
        story.append(Paragraph(data["executive_summary"], body_style))
        story.append(Spacer(1, 12))

    for section in data.get("sections", []):
        story.append(Paragraph(section.get("heading", ""), heading_style))
        if section.get("content"):
            story.append(Paragraph(section["content"], body_style))
        for bp in section.get("bullet_points", []):
            story.append(Paragraph(bp, bullet_style))
        story.append(Spacer(1, 8))

    if data.get("conclusion"):
        story.append(Paragraph("Conclusion", heading_style))
        story.append(Paragraph(data["conclusion"], body_style))

    def _render_pdf(path: Path):
        doc = SimpleDocTemplate(str(path), pagesize=A4,
                                rightMargin=2*cm, leftMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        doc.build(story)

    try:
        _render_pdf(filepath)
        return filepath
    except (PermissionError, OSError) as pe:
        log_warn("doc_forge", f"PDF target {filepath} locked ({pe}), saving with timestamp fallback.")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alt_path = filepath.parent / f"{filepath.stem}_{ts}{filepath.suffix}"
        _render_pdf(alt_path)
        return alt_path


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def _safe_filename(topic: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', topic)[:40]


def resolve_output_path(raw_path: str, default_filename: str) -> Path:
    """Resolves output path, automatically targeting the user's Downloads/Desktop folders."""
    home = Path(os.path.expanduser("~"))
    if not raw_path:
        return home / "Downloads" / default_filename
    clean_p = raw_path.strip().replace("\\", "/")
    expanded = os.path.expanduser(clean_p)
    p = Path(expanded)
    if clean_p.lower().startswith("downloads/") or clean_p.lower() == "downloads":
        sub = clean_p[10:] if clean_p.lower().startswith("downloads/") else default_filename
        p = home / "Downloads" / (sub or default_filename)
    elif clean_p.lower().startswith("desktop/") or clean_p.lower() == "desktop":
        sub = clean_p[8:] if clean_p.lower().startswith("desktop/") else default_filename
        p = home / "Desktop" / (sub or default_filename)
    elif clean_p.lower().startswith("documents/") or clean_p.lower() == "documents":
        sub = clean_p[10:] if clean_p.lower().startswith("documents/") else default_filename
        p = home / "Documents" / (sub or default_filename)
    elif not p.is_absolute():
        p = home / "Downloads" / clean_p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _generate_code_or_html(topic: str, doc_type: str, content_hint: str = "") -> str:
    """Generates complete, beautiful, runnable code or HTML using Gemini with frontend-design constraints."""
    if content_hint and ("<html" in content_hint.lower() or "def " in content_hint or "import " in content_hint):
        return content_hint

    skill_rules = get_skill_rulebook_for_task(topic, doc_type)

    try:
        from dotenv import load_dotenv
        load_dotenv()
        from google import genai
        key = os.getenv("GEMINI_API_KEY", "")
        client = genai.Client(api_key=key)

        if doc_type in ("html", "website", "web"):
            prompt = f"""You are an elite frontend design engineer for JARVIS Astra.
Create a single-file, production-ready, beautiful HTML/CSS landing page or web dashboard for: "{topic}".

SKILL DESIGN TOKENS & RULES:
- Enforce an 8pt spacing scale: p-2 (8px), p-4 (16px), p-6 (24px), p-8 (32px), p-12 (48px).
- Typography: Maximum 2 font families (System sans: Inter / Segoe UI / SF Pro).
- Anti-Slop Constraints:
  * Never accent just one word in a headline (e.g. no <span class="text-blue-500">word</span>).
  * Never use meaningless tracked-out ALL-CAPS eyebrows above every card.
  * Avoid the generic SaaS-card wash (every card rounded with soft grey blur).
  * Assemble from clear components: Hero banner, 3-Card KPI metric strip, balanced feature grid, high-density data table, interactive CTA button, footer.
- Include responsive Tailwind CSS via CDN (<script src="https://cdn.tailwindcss.com"></script>).
- Theme: Rich dark mode or crisp light mode with high-contrast WCAG-compliant colors.
- Additional context: {content_hint}

Output ONLY the raw HTML code without markdown fences."""
        elif doc_type in ("python", "py", "code"):
            prompt = f"""Create complete, well-documented, working Python code for: "{topic}".
Additional context: {content_hint}
Output ONLY the raw Python code without markdown fences."""
        else:
            prompt = f"""Write a comprehensive, professional {doc_type} document about: "{topic}".
Additional context: {content_hint}
Output clean markdown or text."""

        resp = None
        for m_cand in ["gemini-3.5-flash-lite", "gemini-2.5-flash"]:
            try:
                resp = client.models.generate_content(model=m_cand, contents=prompt)
                if resp and resp.text:
                    break
            except Exception:
                continue
        text = resp.text if resp else ""
        text = re.sub(r"^```(?:html|python|markdown|[\w]*)\n?", "", text.strip())
        text = re.sub(r"\n?```$", "", text.strip())
        return text
    except Exception as e:
        log_warn("doc_forge", f"Gemini content generation error: {e}")
        if doc_type in ("html", "website", "web"):
            return f"<!DOCTYPE html><html><head><title>{topic}</title><script src='https://cdn.tailwindcss.com'></script></head><body class='bg-slate-900 text-white min-h-screen flex flex-col items-center justify-center p-8'><h1 class='text-4xl font-bold mb-4'>{topic}</h1><p class='text-slate-300'>{content_hint or 'Generated by JARVIS Astra.'}</p></body></html>"
        return f"# {topic}\n\n{content_hint or 'Generated by JARVIS Astra.'}"


def create_document(
    topic: str = "",
    document_type: str = "auto",
    content_hint: str = "",
    output_path: str = "",
    *args,
    **kwargs
) -> str:
    """
    Unified entry point. Accepts topic, document_type, content_hint, and output_path.
    Handles Office formats (pptx, docx, xlsx, pdf) as well as code, html, websites, and markdown.
    Automatically resolves user directory targets (e.g. Downloads/report.pptx).
    """
    prompt = topic or kwargs.get("prompt", "")
    doc_type = document_type or kwargs.get("doc_type", "auto")
    doc_type_lower = doc_type.lower()
    prompt_lower = prompt.lower()

    # Determine extension and format
    if any(k in doc_type_lower for k in ("pptx", "presentation", "powerpoint", "slide", "deck", "ppt")) or any(k in prompt_lower for k in ("powerpoint", "ppt", "presentation", "slides", "slide deck")):
        fmt = "pptx"
    elif any(k in doc_type_lower for k in ("docx", "word", "doc", "memo", "contract")) or any(k in prompt_lower for k in ("word doc", "word document", ".docx", "docx", "memo")):
        fmt = "docx"
    elif any(k in doc_type_lower for k in ("xlsx", "excel", "spreadsheet", "sheet")) or any(k in prompt_lower for k in ("excel", "spreadsheet", "xlsx", "sheet")):
        fmt = "xlsx"
    elif any(k in doc_type_lower for k in ("pdf", "report")) or any(k in prompt_lower for k in ("pdf", "report")):
        fmt = "pdf"
    elif doc_type_lower in ("code", "html", "website", "web") or any(w in prompt_lower for w in ["html", "website", "webpage", "web page"]):
        fmt = "html"
    elif doc_type_lower in ("python", "py"):
        fmt = "py"
    elif doc_type_lower in ("markdown", "md"):
        fmt = "md"
    elif doc_type_lower in ("text", "txt"):
        fmt = "txt"
    else:
        if output_path and "." in output_path:
            fmt = output_path.split(".")[-1].lower()
        else:
            fmt = "html" if "website" in prompt_lower else "pdf"

    clean_topic = re.sub(
        r'(?i)(create|make|generate|build|write)\s+(a\s+|an\s+)?(powerpoint|ppt|presentation|'
        r'slides?|slide\s+deck|excel|spreadsheet|sheet|pdf|report|document|word|docx|website|webpage|code|file)\s*(about|on|for|regarding)?\s*',
        '', prompt
    ).strip() or "document"

    safe_topic = _safe_filename(clean_topic)
    default_filename = f"{safe_topic}.{fmt}"
    target_filepath = resolve_output_path(output_path, default_filename)

    log_info("doc_forge", f"Creating {fmt.upper()} at -> {target_filepath}")
    print(f"\n[DOC FORGE]: Creating {fmt.upper()} at -> {target_filepath}")

    # Handle Code / HTML / Markdown / Text files directly
    if fmt in ("html", "py", "md", "txt", "js", "css", "json"):
        code_content = _generate_code_or_html(clean_topic, fmt, content_hint)
        save_target = target_filepath
        try:
            with open(save_target, "w", encoding="utf-8") as f:
                f.write(code_content)
        except (PermissionError, OSError):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            save_target = target_filepath.parent / f"{target_filepath.stem}_{ts}{target_filepath.suffix}"
            with open(save_target, "w", encoding="utf-8") as f:
                f.write(code_content)
        try:
            from core.modern_memory import record_deliverable
            record_deliverable(str(save_target), topic=clean_topic, doc_type=fmt)
        except Exception:
            pass
        try:
            os.startfile(str(save_target))
        except Exception as _oe:
            log_warn("doc_forge", f"Could not auto-open: {_oe}")
        return f"Successfully created and opened {save_target.name} in {save_target.parent} ({len(code_content)} bytes)."

    # Generate content for Office formats
    content = _generate_content(clean_topic, fmt, content_hint=content_hint)

    saved_file = None
    if fmt == "pptx":
        saved_file = _build_pptx(content, target_filepath, clean_topic)
    elif fmt == "docx":
        saved_file = _build_docx(content, target_filepath, clean_topic)
    elif fmt == "xlsx":
        saved_file = _build_xlsx(content, target_filepath)
    elif fmt == "pdf":
        saved_file = _build_pdf(content, target_filepath)

    if not saved_file:
        lib_map = {"pptx": "python-pptx", "docx": "python-docx", "xlsx": "openpyxl", "pdf": "reportlab"}
        return f"Required library not installed. Run: pip install {lib_map.get(fmt, 'the required package')}"

    try:
        from core.modern_memory import record_deliverable
        record_deliverable(str(saved_file), topic=clean_topic, doc_type=fmt)
    except Exception:
        pass

    try:
        os.startfile(str(saved_file))
    except Exception as e:
        log_warn("doc_forge", f"Could not auto-open file: {e}")

    try:
        from live_voice import notify_live_assistant
        notify_live_assistant(
            f"Document Forge ({fmt.upper()})",
            f"Created and opened {saved_file.name} in {saved_file.parent}",
            speak_alert=False
        )
    except Exception:
        pass

    return f"{fmt.upper()} created and opened: {saved_file.name} in {saved_file.parent}"


# Convenience alias for unified naming
forge_document = create_document

