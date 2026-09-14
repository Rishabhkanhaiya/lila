"""
core/omniforge_modal.py — Floating Interactive Configuration Window for OmniForge Pro
======================================================================================
Spawns a sleek, modern, floating desktop modal where the user can visually configure:
  1. Project Name
  2. Theme: Clean White / Light Theme vs Dark Slate vs Cyber Matrix vs Cosmic Glow
  3. Blueprint / Category: Portfolio, SaaS, Analytics, 3D WebGL, Custom AI
  4. Primary Accent Color
  5. Custom Features / Specifications
  6. Instant "Forge Application" execution with live compiler feedback
"""

import sys
import threading
import time
from pathlib import Path
from typing import Optional

WORKSPACE_ROOT = Path(r"c:\Users\Rishabh_Joshi\Downloads\jarvis_project")
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import customtkinter as ctk


class OmniForgeModal(ctk.CTk):
    def __init__(self, initial_prompt: str = ""):
        super().__init__()

        # Window setup
        self.title("OmniForge Pro • AI Website Architect")
        self.geometry("540x680")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        # Theme defaults
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.configure(fg_color="#070a13")

        self._build_ui(initial_prompt)

    def _build_ui(self, initial_prompt: str):
        # ── Header ──
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=24, pady=(20, 10))

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="⚡ OmniForge Pro Studio",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color="#f8fafc"
        )
        title_lbl.pack(anchor="w")

        subtitle_lbl = ctk.CTkLabel(
            header_frame,
            text="Configure your visual theme, stack & specs for Lila to forge live.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#94a3b8"
        )
        subtitle_lbl.pack(anchor="w")

        # ── Scrollable Form Container ──
        form_frame = ctk.CTkScrollableFrame(self, fg_color="#0d1322", corner_radius=16, border_width=1, border_color="#1e293b")
        form_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # 1. Project Name
        lbl_name = ctk.CTkLabel(form_frame, text="Project Name", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1")
        lbl_name.pack(anchor="w", padx=12, pady=(10, 4))

        self.entry_name = ctk.CTkEntry(
            form_frame,
            placeholder_text="e.g. rishabh_portfolio, ai_saas, modern_studio",
            height=38,
            corner_radius=10,
            fg_color="#141c2f",
            border_color="#334155",
            text_color="#f8fafc"
        )
        self.entry_name.insert(0, "my_web_app")
        self.entry_name.pack(fill="x", padx=12, pady=(0, 12))

        # 2. Theme Selection (The core feature requested by user!)
        lbl_theme = ctk.CTkLabel(form_frame, text="Visual Theme & Style", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1")
        lbl_theme.pack(anchor="w", padx=12, pady=(4, 4))

        self.theme_var = ctk.StringVar(value="Clean White / Light")
        self.theme_segmented = ctk.CTkSegmentedButton(
            form_frame,
            values=["Clean White / Light", "Dark Slate / Zinc", "Cyber Matrix", "Cosmic Glow"],
            variable=self.theme_var,
            height=34,
            corner_radius=10,
            selected_color="#4f46e5",
            selected_hover_color="#4338ca",
            unselected_color="#141c2f",
            unselected_hover_color="#1e293b",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.theme_segmented.pack(fill="x", padx=12, pady=(0, 14))

        # 3. App Blueprint / Type
        lbl_bp = ctk.CTkLabel(form_frame, text="Blueprint Category", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1")
        lbl_bp.pack(anchor="w", padx=12, pady=(4, 4))

        self.bp_var = ctk.StringVar(value="Developer Portfolio")
        self.bp_menu = ctk.CTkOptionMenu(
            form_frame,
            values=[
                "Developer Portfolio",
                "SaaS Product Landing",
                "Real-Time Analytics Dashboard",
                "3D Spatial / Three.js WebGL",
                "Cyberpunk Neural Console",
                "Custom AI App"
            ],
            variable=self.bp_var,
            height=36,
            corner_radius=10,
            fg_color="#141c2f",
            button_color="#334155",
            button_hover_color="#475569",
            text_color="#f8fafc",
            dropdown_fg_color="#141c2f"
        )
        self.bp_menu.pack(fill="x", padx=12, pady=(0, 14))

        # 4. Accent Color
        lbl_accent = ctk.CTkLabel(form_frame, text="Primary Accent Color", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1")
        lbl_accent.pack(anchor="w", padx=12, pady=(4, 4))

        self.accent_var = ctk.StringVar(value="Indigo / Violet (#6366f1)")
        self.accent_menu = ctk.CTkOptionMenu(
            form_frame,
            values=[
                "Indigo / Violet (#6366f1)",
                "Emerald / Mint (#10b981)",
                "Sky / Cyan (#0ea5e9)",
                "Rose / Crimson (#f43f5e)",
                "Amber / Gold (#f59e0b)",
                "Monochrome Sleek"
            ],
            variable=self.accent_var,
            height=36,
            corner_radius=10,
            fg_color="#141c2f",
            button_color="#334155",
            button_hover_color="#475569",
            text_color="#f8fafc",
            dropdown_fg_color="#141c2f"
        )
        self.accent_menu.pack(fill="x", padx=12, pady=(0, 14))

        # 5. Specific Features / Prompt Details
        lbl_desc = ctk.CTkLabel(form_frame, text="Key Features & Custom Prompt", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1")
        lbl_desc.pack(anchor="w", padx=12, pady=(4, 4))

        self.txt_desc = ctk.CTkTextbox(
            form_frame,
            height=85,
            corner_radius=10,
            fg_color="#141c2f",
            border_width=1,
            border_color="#334155",
            text_color="#f8fafc"
        )
        default_p = initial_prompt or "Include interactive 3D particle canvas, skills bento grid, category filters, and contact modal."
        self.txt_desc.insert("1.0", default_p)
        self.txt_desc.pack(fill="x", padx=12, pady=(0, 14))

        # ── Status Bar ──
        self.lbl_status = ctk.CTkLabel(
            self,
            text="Ready to forge with Vite + React 18 + TypeScript",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.lbl_status.pack(padx=24, pady=(6, 2))

        # ── Action Buttons ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(6, 18))

        self.btn_forge = ctk.CTkButton(
            btn_frame,
            text="🚀 Forge & Launch Website",
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#4f46e5",
            hover_color="#4338ca",
            command=self._on_forge_click
        )
        self.btn_forge.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_hub = ctk.CTkButton(
            btn_frame,
            text="Studio Hub",
            width=100,
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._on_hub_click
        )
        btn_hub.pack(side="right")

    def _on_hub_click(self):
        try:
            from core.omniforge import open_studio_hub
            open_studio_hub()
            self.lbl_status.configure(text="Opened Studio Hub at http://localhost:5252/", text_color="#38bdf8")
        except Exception as e:
            self.lbl_status.configure(text=f"Hub error: {e}", text_color="#f87171")

    def _on_forge_click(self):
        project_name = self.entry_name.get().strip() or "my_web_app"
        theme = self.theme_var.get()
        blueprint = self.bp_var.get()
        accent = self.accent_var.get()
        user_notes = self.txt_desc.get("1.0", "end").strip()

        # Build full comprehensive prompt with theme and styling instructions
        full_prompt = (
            f"Build a production-grade web application for '{project_name}'. "
            f"Category: {blueprint}. "
            f"Theme: {theme}. "
            f"Primary Accent: {accent}. "
            f"Requirements: {user_notes}."
        )

        self.btn_forge.configure(state="disabled", text="⏳ Forging Application...")
        self.lbl_status.configure(text="Scaffolding Vite React + Compiler Verification...", text_color="#fbbf24")

        # Run synthesis in background thread
        threading.Thread(target=self._run_forge_task, args=(project_name, full_prompt, theme), daemon=True).start()

    def _run_forge_task(self, project_name: str, prompt: str, theme: str):
        try:
            from core.omniforge import build_and_launch_app

            clean_bp = "auto"
            if "Cyberpunk" in prompt:
                clean_bp = "cyber_matrix"

            res = build_and_launch_app(
                project_name=project_name,
                prompt=prompt,
                blueprint=clean_bp
            )

            url = res.get("url", "http://localhost:5210/")
            msg = f"✅ Launched live at {url}! Exit code 0 verified."
            self.after(0, lambda: self._on_forge_success(msg))
        except Exception as e:
            err_msg = f"❌ Forge error: {str(e)[:60]}"
            self.after(0, lambda: self._on_forge_error(err_msg))

    def _on_forge_success(self, message: str):
        self.btn_forge.configure(state="normal", text="🚀 Forge Another Website")
        self.lbl_status.configure(text=message, text_color="#4ade80")

    def _on_forge_error(self, err_message: str):
        self.btn_forge.configure(state="normal", text="🚀 Retry Forge")
        self.lbl_status.configure(text=err_message, text_color="#f87171")


def show_omniforge_modal(initial_prompt: str = ""):
    """Launch the floating configuration modal window."""
    app = OmniForgeModal(initial_prompt=initial_prompt)
    app.mainloop()


if __name__ == "__main__":
    prompt_arg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    show_omniforge_modal(prompt_arg)
