"""
core/theme_factory.py — JARVIS Mathematical Theme & Contrast Engine
===================================================================
Implements the Anthropic theme-factory specification:
- 10 curated battle-tested themes.
- HSL color ramp mathematics.
- Programmatic WCAG 2.1 AA/AAA contrast enforcement.
- Topic-to-theme heuristic mapping.
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, Any
import colorsys
import re


def hex_to_rgb(hex_code: str) -> Tuple[int, int, int]:
    clean = hex_code.strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join([c * 2 for c in clean])
    if len(clean) != 6:
        clean = "0066FF"
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    r = max(0, min(255, int(round(r))))
    g = max(0, min(255, int(round(g))))
    b = max(0, min(255, int(round(b))))
    return f"#{r:02X}{g:02X}{b:02X}"


def rgb_to_hsl(r: int, g: int, b: int) -> Tuple[float, float, float]:
    return colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)


def hsl_to_rgb(h: float, l: float, s: float) -> Tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def calculate_relative_luminance(r: int, g: int, b: int) -> float:
    """Computes WCAG 2.1 relative luminance."""
    channels = [r / 255.0, g / 255.0, b / 255.0]
    lin = []
    for c in channels:
        if c <= 0.03928:
            lin.append(c / 12.92)
        else:
            lin.append(((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def calculate_contrast_ratio(lum1: float, lum2: float) -> float:
    """Computes WCAG contrast ratio between two relative luminances."""
    bright = max(lum1, lum2)
    dark = min(lum1, lum2)
    return (bright + 0.05) / (dark + 0.05)


@dataclass
class ThemeColor:
    hex: str
    rgb: Tuple[int, int, int] = field(init=False)
    luminance: float = field(init=False)

    def __post_init__(self):
        if not self.hex.startswith("#"):
            self.hex = f"#{self.hex}"
        self.rgb = hex_to_rgb(self.hex)
        self.luminance = calculate_relative_luminance(*self.rgb)

    @property
    def r(self) -> int:
        return self.rgb[0]

    @property
    def g(self) -> int:
        return self.rgb[1]

    @property
    def b(self) -> int:
        return self.rgb[2]

    @property
    def hex_clean(self) -> str:
        """Hex string without '#' symbol, perfect for pptxgenjs or python-pptx RGBColor."""
        return self.hex.lstrip("#")


def ensure_contrast(fg: ThemeColor, bg: ThemeColor, min_ratio: float = 4.5) -> ThemeColor:
    """
    Shifts the lightness of foreground color until it achieves the requested WCAG contrast ratio.
    Guarantees that body text, labels, and icons remain 100% readable.
    """
    ratio = calculate_contrast_ratio(fg.luminance, bg.luminance)
    if ratio >= min_ratio:
        return fg

    h, l, s = rgb_to_hsl(*fg.rgb)
    is_bg_dark = bg.luminance < 0.3

    # Shift lightness towards 1.0 (if dark bg) or towards 0.0 (if light bg)
    step = 0.05 if is_bg_dark else -0.05
    current_l = l

    for _ in range(20):
        current_l = max(0.02, min(0.98, current_l + step))
        r, g, b = hsl_to_rgb(h, current_l, s)
        new_lum = calculate_relative_luminance(r, g, b)
        new_ratio = calculate_contrast_ratio(new_lum, bg.luminance)
        if new_ratio >= min_ratio:
            return ThemeColor(rgb_to_hex(r, g, b))

    # Fallback to pure white or near black if color saturation prevented ratio
    fallback_hex = "#FFFFFF" if is_bg_dark else "#0A0A0A"
    return ThemeColor(fallback_hex)


@dataclass
class DesignTheme:
    id: str
    name: str
    description: str
    is_dark: bool
    bg: ThemeColor
    surface: ThemeColor
    surface_elevated: ThemeColor
    primary: ThemeColor
    secondary: ThemeColor
    accent: ThemeColor
    text_primary: ThemeColor
    text_secondary: ThemeColor
    border: ThemeColor
    font_heading: str = "Segoe UI"
    font_body: str = "Segoe UI"

    def __post_init__(self):
        # Programmatically verify and guarantee WCAG AA contrast for text elements
        self.text_primary = ensure_contrast(self.text_primary, self.surface, min_ratio=4.5)
        self.text_secondary = ensure_contrast(self.text_secondary, self.surface, min_ratio=3.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "is_dark": self.is_dark,
            "bg": self.bg.hex,
            "surface": self.surface.hex,
            "surface_elevated": self.surface_elevated.hex,
            "primary": self.primary.hex,
            "secondary": self.secondary.hex,
            "accent": self.accent.hex,
            "text_primary": self.text_primary.hex,
            "text_secondary": self.text_secondary.hex,
            "border": self.border.hex,
            "font_heading": self.font_heading,
            "font_body": self.font_body,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 10 Curated Anthropic-Inspired Themes
# ─────────────────────────────────────────────────────────────────────────────

CURATED_THEMES: Dict[str, DesignTheme] = {
    "tech-innovation": DesignTheme(
        id="tech-innovation",
        name="Tech Innovation",
        description="Bold and modern cyber aesthetic with electric blue and neon cyan accents.",
        is_dark=True,
        bg=ThemeColor("#070B14"),
        surface=ThemeColor("#0E1626"),
        surface_elevated=ThemeColor("#17223B"),
        primary=ThemeColor("#0066FF"),
        secondary=ThemeColor("#00F0FF"),
        accent=ThemeColor("#00FF9D"),
        text_primary=ThemeColor("#FFFFFF"),
        text_secondary=ThemeColor("#94A3B8"),
        border=ThemeColor("#1E293B"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "midnight-galaxy": DesignTheme(
        id="midnight-galaxy",
        name="Midnight Galaxy",
        description="Deep obsidian space aesthetic with violet and vivid pink highlights.",
        is_dark=True,
        bg=ThemeColor("#0A0B14"),
        surface=ThemeColor("#141528"),
        surface_elevated=ThemeColor("#1F203D"),
        primary=ThemeColor("#8B5CF6"),
        secondary=ThemeColor("#EC4899"),
        accent=ThemeColor("#38BDF8"),
        text_primary=ThemeColor("#F8FAFC"),
        text_secondary=ThemeColor("#A5B4FC"),
        border=ThemeColor("#2E2B52"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "ocean-depths": DesignTheme(
        id="ocean-depths",
        name="Ocean Depths",
        description="Calming executive maritime palette with deep navy, teal, and seafoam.",
        is_dark=True,
        bg=ThemeColor("#0B1320"),
        surface=ThemeColor("#132238"),
        surface_elevated=ThemeColor("#1C3150"),
        primary=ThemeColor("#0D9488"),
        secondary=ThemeColor("#38BDF8"),
        accent=ThemeColor("#2DD4BF"),
        text_primary=ThemeColor("#F1F5F9"),
        text_secondary=ThemeColor("#94A3B8"),
        border=ThemeColor("#1E3A5F"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "forest-canopy": DesignTheme(
        id="forest-canopy",
        name="Forest Canopy",
        description="Lush ecological palette with rich deep moss, emerald green, and gold amber.",
        is_dark=True,
        bg=ThemeColor("#06170F"),
        surface=ThemeColor("#0D291C"),
        surface_elevated=ThemeColor("#143B29"),
        primary=ThemeColor("#10B981"),
        secondary=ThemeColor("#F59E0B"),
        accent=ThemeColor("#34D399"),
        text_primary=ThemeColor("#F0FDF4"),
        text_secondary=ThemeColor("#A7F3D0"),
        border=ThemeColor("#1B4D36"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "modern-minimalist": DesignTheme(
        id="modern-minimalist",
        name="Modern Minimalist",
        description="Clean, crisp high-contrast light theme with cobalt blue and subtle slate cards.",
        is_dark=False,
        bg=ThemeColor("#FFFFFF"),
        surface=ThemeColor("#F8FAFC"),
        surface_elevated=ThemeColor("#F1F5F9"),
        primary=ThemeColor("#2563EB"),
        secondary=ThemeColor("#475569"),
        accent=ThemeColor("#0D9488"),
        text_primary=ThemeColor("#0F172A"),
        text_secondary=ThemeColor("#475569"),
        border=ThemeColor("#E2E8F0"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "arctic-frost": DesignTheme(
        id="arctic-frost",
        name="Arctic Frost",
        description="Clean medical and scientific light theme with ice blue, cyan, and deep navy text.",
        is_dark=False,
        bg=ThemeColor("#F0F9FF"),
        surface=ThemeColor("#E0F2FE"),
        surface_elevated=ThemeColor("#BAE6FD"),
        primary=ThemeColor("#0284C7"),
        secondary=ThemeColor("#0284C7"),
        accent=ThemeColor("#0D9488"),
        text_primary=ThemeColor("#0C4A6E"),
        text_secondary=ThemeColor("#0369A1"),
        border=ThemeColor("#B9E6FE"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "sunset-boulevard": DesignTheme(
        id="sunset-boulevard",
        name="Sunset Boulevard",
        description="High-energy dark theme featuring burnt orange, rose red, and warm stone grays.",
        is_dark=True,
        bg=ThemeColor("#141210"),
        surface=ThemeColor("#211D1A"),
        surface_elevated=ThemeColor("#312B26"),
        primary=ThemeColor("#EA580C"),
        secondary=ThemeColor("#F43F5E"),
        accent=ThemeColor("#FBBF24"),
        text_primary=ThemeColor("#FAFAF9"),
        text_secondary=ThemeColor("#D6D3D1"),
        border=ThemeColor("#443E38"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "golden-hour": DesignTheme(
        id="golden-hour",
        name="Golden Hour",
        description="Prestigious wealth and premium aesthetic with rich gold, amber, and obsidian zinc.",
        is_dark=True,
        bg=ThemeColor("#12110D"),
        surface=ThemeColor("#1F1C15"),
        surface_elevated=ThemeColor("#302B20"),
        primary=ThemeColor("#D97706"),
        secondary=ThemeColor("#FBBF24"),
        accent=ThemeColor("#38BDF8"),
        text_primary=ThemeColor("#FEF3C7"),
        text_secondary=ThemeColor("#D4D4D8"),
        border=ThemeColor("#453D2C"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "desert-rose": DesignTheme(
        id="desert-rose",
        name="Desert Rose",
        description="Elegant editorial aesthetic with soft crimson, dusty rose, and warm cream surfaces.",
        is_dark=False,
        bg=ThemeColor("#FFF1F2"),
        surface=ThemeColor("#FFE4E6"),
        surface_elevated=ThemeColor("#FECDD3"),
        primary=ThemeColor("#BE185D"),
        secondary=ThemeColor("#9D174D"),
        accent=ThemeColor("#0284C7"),
        text_primary=ThemeColor("#881337"),
        text_secondary=ThemeColor("#9F1239"),
        border=ThemeColor("#FDA4AF"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
    "botanical-garden": DesignTheme(
        id="botanical-garden",
        name="Botanical Garden",
        description="Organic fresh light theme with forest green, bright lime, and crisp mint surfaces.",
        is_dark=False,
        bg=ThemeColor("#F7FEE7"),
        surface=ThemeColor("#ECFCCB"),
        surface_elevated=ThemeColor("#D9F99D"),
        primary=ThemeColor("#059669"),
        secondary=ThemeColor("#4D7C0F"),
        accent=ThemeColor("#0284C7"),
        text_primary=ThemeColor("#14532D"),
        text_secondary=ThemeColor("#166534"),
        border=ThemeColor("#BEF264"),
        font_heading="Segoe UI",
        font_body="Segoe UI"
    ),
}


def get_theme_for_topic(topic: str, preferred_id: str = "") -> DesignTheme:
    """
    Intelligently maps a subject/topic or explicit theme ID to one of the 10 curated themes.
    Enforces consistent brand identity based on domain semantics.
    """
    if preferred_id and preferred_id in CURATED_THEMES:
        return CURATED_THEMES[preferred_id]

    t_lower = topic.lower()

    # Heuristic domain mapping
    if any(k in t_lower for k in ["environment", "climate", "green", "nature", "forest", "solar", "renewable", "ecology", "carbon", "pollution"]):
        return CURATED_THEMES["forest-canopy"]
    if any(k in t_lower for k in ["crypto", "gaming", "space", "galaxy", "metaverse", "vr", "entertainment"]):
        return CURATED_THEMES["midnight-galaxy"]
    if any(k in t_lower for k in ["finance", "bank", "wealth", "investment", "audit", "corporate", "market", "quarterly", "consulting"]):
        return CURATED_THEMES["ocean-depths"]
    if any(k in t_lower for k in ["medical", "health", "pharma", "clinical", "hospital", "doctor", "biology", "vaccine", "biotech"]):
        return CURATED_THEMES["arctic-frost"]
    if any(k in t_lower for k in ["marketing", "pitch", "launch", "sales", "agency", "creative", "campaign", "advertising"]):
        return CURATED_THEMES["sunset-boulevard"]
    if any(k in t_lower for k in ["luxury", "gold", "real estate", "jewelry", "premium", "vip"]):
        return CURATED_THEMES["golden-hour"]
    if any(k in t_lower for k in ["fashion", "beauty", "cosmetics", "lifestyle", "interior"]):
        return CURATED_THEMES["desert-rose"]
    if any(k in t_lower for k in ["agriculture", "botany", "farming", "organic", "food", "crops"]):
        return CURATED_THEMES["botanical-garden"]
    if any(k in t_lower for k in ["minimal", "clean", "simple", "legal", "white"]):
        return CURATED_THEMES["modern-minimalist"]

    # Default to Tech Innovation for AI, software, engineering, and general topics
    return CURATED_THEMES["tech-innovation"]
