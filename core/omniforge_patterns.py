"""
core/omniforge_patterns.py — Curated React + Vite + Three.js Component Pattern Library
======================================================================================
Provides indexed, vetted section patterns (Navigation, Heroes, 3D Canvases, Bento Grids,
Project Showcases, and Modals) used by OmniForge v2 for compositional RAG synthesis.
"""

from typing import Dict, List, Any

# ─────────────────────────────────────────────────────────────────────────────
# 1. Vetted React + Tailwind Component Patterns
# ─────────────────────────────────────────────────────────────────────────────

PATTERN_NAVBAR = """import React, { useState } from 'react';
import { Menu, X, Sparkles } from 'lucide-react';

interface NavbarProps {
  title?: string;
  links?: { label: string; href: string }[];
  onActionClick?: () => void;
  actionText?: string;
}

export const Navbar: React.FC<NavbarProps> = ({
  title = "OmniForge",
  links = [
    { label: "Overview", href: "#overview" },
    { label: "Features", href: "#features" },
    { label: "Projects", href: "#projects" },
    { label: "Contact", href: "#contact" }
  ],
  onActionClick,
  actionText = "Get Started"
}) => {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <nav className="fixed top-4 inset-x-0 max-w-6xl mx-auto px-4 z-50">
      <div className="glass-card rounded-2xl px-6 py-3.5 flex items-center justify-between border shadow-2xl backdrop-blur-xl dark:bg-slate-900/70 bg-white/85 dark:border-white/10 border-slate-200/90">
        <a href="#" className="flex items-center gap-2.5 group">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-500/30 group-hover:scale-105 transition-transform">
            <Sparkles className="w-4 h-4" />
          </div>
          <span className="font-bold dark:text-white text-slate-900 tracking-tight text-base">{title}</span>
        </a>

        <div className="hidden md:flex items-center gap-8 text-sm font-medium dark:text-slate-300 text-slate-600">
          {links.map((l: any, idx: number) => {
            const labelText = l.label || l.name || l.title || "Link";
            return (
              <a
                key={idx}
                href={l.href}
                onClick={l.onClick}
                className="hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
              >
                {labelText}
              </a>
            );
          })}
        </div>

        <div className="hidden md:flex items-center gap-3">
          <button
            onClick={onActionClick}
            className="px-4 py-2 text-xs font-semibold rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-400 hover:to-purple-500 text-white shadow-lg shadow-indigo-500/25 transition-all hover:scale-105"
          >
            {actionText}
          </button>
        </div>

        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="md:hidden text-slate-400 hover:text-white"
        >
          {mobileOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
        </button>
      </div>

      {mobileOpen && (
        <div className="md:hidden mt-2 glass-card rounded-2xl p-4 border border-white/10 flex flex-col gap-3">
          {links.map((l: any, idx: number) => {
            const labelText = l.label || l.name || l.title || "Link";
            return (
              <a
                key={idx}
                href={l.href}
                onClick={(e) => {
                  setMobileOpen(false);
                  if (l.onClick) l.onClick(e);
                }}
                className="text-sm text-slate-600 dark:text-slate-300 hover:text-indigo-600 py-1"
              >
                {labelText}
              </a>
            );
          })}
          <button
            onClick={() => { setMobileOpen(false); onActionClick && onActionClick(); }}
            className="w-full mt-2 py-2.5 rounded-xl bg-indigo-600 text-white text-xs font-semibold"
          >
            {actionText}
          </button>
        </div>
      )}
    </nav>
  );
};
"""

PATTERN_THREE_PARTICLES = """import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';

interface ThreeCanvasProps {
  particleCount?: number;
  particleColor?: string;
}

export const ThreeCanvas: React.FC<ThreeCanvasProps> = ({
  particleCount = 8000,
  particleColor = "#818cf8"
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 400;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, width / height, 0.1, 1000);
    camera.position.z = 25;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // Particle Geometry
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);

    const baseColor = new THREE.Color(particleColor);
    const accentColor = new THREE.Color("#c084fc");

    for (let i = 0; i < particleCount; i++) {
      const i3 = i * 3;
      const radius = Math.random() * 18 + 2;
      const spinAngle = radius * 1.5;
      const branchAngle = ((i % 3) * 2 * Math.PI) / 3;

      const randomX = (Math.random() - 0.5) * 1.5;
      const randomY = (Math.random() - 0.5) * 1.5;
      const randomZ = (Math.random() - 0.5) * 1.5;

      positions[i3] = Math.cos(branchAngle + spinAngle) * radius + randomX;
      positions[i3 + 1] = randomY;
      positions[i3 + 2] = Math.sin(branchAngle + spinAngle) * radius + randomZ;

      const mixedColor = baseColor.clone().lerp(accentColor, radius / 20);
      colors[i3] = mixedColor.r;
      colors[i3 + 1] = mixedColor.g;
      colors[i3 + 2] = mixedColor.b;
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const material = new THREE.PointsMaterial({
      size: 0.07,
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
    });

    const particles = new THREE.Points(geometry, material);
    scene.add(particles);

    // Mouse Interaction
    let mouseX = 0;
    let mouseY = 0;
    const handleMouseMove = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      mouseX = ((e.clientX - rect.left) / container.clientWidth) * 2 - 1;
      mouseY = -(((e.clientY - rect.top) / container.clientHeight) * 2 - 1);
    };
    window.addEventListener('mousemove', handleMouseMove);

    // Animation Loop
    let animId: number;
    const clock = new THREE.Clock();
    const animate = () => {
      animId = requestAnimationFrame(animate);
      const elapsedTime = clock.getElapsedTime();
      particles.rotation.y = elapsedTime * 0.08 + mouseX * 0.2;
      particles.rotation.x = mouseY * 0.15;
      renderer.render(scene, camera);
    };
    animate();

    // Resize
    const handleResize = () => {
      if (!container) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animId);
      renderer.dispose();
      geometry.dispose();
      material.dispose();
      if (container && renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [particleCount, particleColor]);

  return <div ref={containerRef} className="w-full h-full min-h-[400px] relative pointer-events-none" />;
};
"""

PATTERN_CONTACT_MODAL = """import React, { useState } from 'react';
import { X, Send, CheckCircle2 } from 'lucide-react';
import confetti from 'canvas-confetti';

interface ContactModalProps {
  isOpen: boolean;
  onClose: () => void;
  recipient?: string;
}

export const ContactModal: React.FC<ContactModalProps> = ({
  isOpen,
  onClose,
  recipient = "Rishabh Joshi"
}) => {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [submitted, setSubmitted] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim()) return;

    confetti({
      particleCount: 80,
      spread: 70,
      origin: { y: 0.6 }
    });

    setSubmitted(true);
    setTimeout(() => {
      setSubmitted(false);
      setName('');
      setEmail('');
      setMessage('');
      onClose();
    }, 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-md animate-in fade-in duration-200">
      <div className="glass-card w-full max-w-md p-6 rounded-3xl border dark:border-white/10 border-slate-200/90 shadow-2xl relative dark:bg-slate-900/95 bg-white/95 dark:text-white text-slate-900 text-left">
        <button
          onClick={onClose}
          className="absolute top-5 right-5 dark:text-slate-400 text-slate-500 dark:hover:text-white hover:text-slate-900 transition-colors"
        >
          <X className="w-5 h-5" />
        </button>

        {submitted ? (
          <div className="py-10 text-center flex flex-col items-center justify-center">
            <div className="w-12 h-12 rounded-full bg-emerald-500/20 text-emerald-500 flex items-center justify-center mb-3">
              <CheckCircle2 className="w-6 h-6" />
            </div>
            <h3 className="text-xl font-bold dark:text-white text-slate-900 mb-1">Message Sent!</h3>
            <p className="text-sm dark:text-slate-400 text-slate-600">Thanks for reaching out to {recipient}.</p>
          </div>
        ) : (
          <div>
            <h3 className="text-xl font-bold dark:text-white text-slate-900 mb-1">Get in Touch</h3>
            <p className="text-xs dark:text-slate-400 text-slate-600 mb-6">Drop a message for {recipient} directly.</p>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold dark:text-slate-300 text-slate-700 mb-1.5">Your Name</label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Alex Rivera"
                  className="w-full px-4 py-2.5 rounded-xl dark:bg-slate-800/60 bg-slate-100 border dark:border-white/10 border-slate-300 dark:text-white text-slate-900 placeholder-slate-400 text-sm focus:outline-none focus:border-indigo-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold dark:text-slate-300 text-slate-700 mb-1.5">Your Email</label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="alex@company.com"
                  className="w-full px-4 py-2.5 rounded-xl dark:bg-slate-800/60 bg-slate-100 border dark:border-white/10 border-slate-300 dark:text-white text-slate-900 placeholder-slate-400 text-sm focus:outline-none focus:border-indigo-500 transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold dark:text-slate-300 text-slate-700 mb-1.5">Message</label>
                <textarea
                  rows={3}
                  required
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Write your note here..."
                  className="w-full px-4 py-2.5 rounded-xl dark:bg-slate-800/60 bg-slate-100 border dark:border-white/10 border-slate-300 dark:text-white text-slate-900 placeholder-slate-400 text-sm focus:outline-none focus:border-indigo-500 transition-colors resize-none"
                />
              </div>

              <button
                type="submit"
                className="w-full py-3 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-400 hover:to-purple-500 text-white font-semibold text-xs shadow-lg shadow-indigo-500/25 transition-all hover:scale-[1.02] flex items-center justify-center gap-2"
              >
                <span>Send Note</span>
                <Send className="w-3.5 h-3.5" />
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
};
"""

PATTERN_BENTO_GRID = """import React from 'react';
import { Cpu, Terminal, Shield, Zap, Sparkles, Layers } from 'lucide-react';

interface BentoItem {
  icon: React.ElementType;
  title: string;
  category: string;
  desc: string;
  badge?: string;
  className?: string;
}

interface BentoGridProps {
  title?: string;
  subtitle?: string;
  items?: BentoItem[];
}

export const BentoGrid: React.FC<BentoGridProps> = ({
  title = "Core Capabilities",
  subtitle = "High-performance systems built with modern engineering standards.",
  items = [
    {
      icon: Cpu,
      title: "AI & Neural Systems",
      category: "Machine Intelligence",
      desc: "Autonomous agents, Gemini Live voice streaming, and low-latency inference pipelines.",
      badge: "Production",
      className: "md:col-span-2"
    },
    {
      icon: Terminal,
      title: "Full-Stack Software",
      category: "Architecture",
      desc: "React 18, TypeScript, Python FastAPI, and Vite-powered modular interfaces.",
      badge: "High-End",
      className: "md:col-span-1"
    },
    {
      icon: Zap,
      title: "Sub-Second Telemetry",
      category: "Performance",
      desc: "Real-time state broadcast and event-driven automation pipelines.",
      badge: "<100ms",
      className: "md:col-span-1"
    },
    {
      icon: Layers,
      title: "Interactive 3D Visuals",
      category: "Graphics",
      desc: "Three.js particle shaders and WebGL scene management with 60 FPS fluidity.",
      badge: "Three.js",
      className: "md:col-span-2"
    }
  ]
}) => {
  return (
    <section id="features" className="py-20 px-4 max-w-6xl mx-auto">
      <div className="text-center mb-14">
        <span className="px-3 py-1 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-500 border border-indigo-500/20 uppercase tracking-wider">
          Architecture
        </span>
        <h2 className="text-3xl sm:text-5xl font-extrabold dark:text-white text-slate-900 mt-3 mb-4 tracking-tight">{title}</h2>
        <p className="dark:text-slate-400 text-slate-600 text-sm sm:text-base max-w-xl mx-auto">{subtitle}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {items.map((item: any, idx: number) => {
          const iconVal = item.icon;
          const descText = item.desc || item.description || "";
          const catText = item.category || "Capability";
          return (
            <div
              key={idx}
              className={`glass-card rounded-3xl p-7 border dark:border-white/10 border-slate-200/90 dark:bg-slate-900/60 bg-white/85 shadow-xl hover:border-indigo-500/40 transition-all duration-300 group flex flex-col justify-between ${item.className || ''}`}
            >
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div className="w-12 h-12 rounded-2xl bg-indigo-500/10 text-indigo-500 border border-indigo-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                    {React.isValidElement(iconVal) ? (
                      iconVal
                    ) : typeof iconVal === 'function' ? (
                      React.createElement(iconVal, { className: "w-6 h-6" })
                    ) : (
                      <Cpu className="w-6 h-6" />
                    )}
                  </div>
                  {item.badge && (
                    <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold dark:bg-white/5 bg-slate-100 dark:text-slate-300 text-slate-700 border dark:border-white/10 border-slate-200">
                      {item.badge}
                    </span>
                  )}
                </div>
                <span className="text-xs font-medium text-indigo-500 uppercase tracking-wider">{catText}</span>
                <h3 className="text-xl font-bold dark:text-white text-slate-900 mt-1 mb-2 group-hover:text-indigo-600 dark:group-hover:text-indigo-300 transition-colors">{item.title}</h3>
                <p className="text-sm dark:text-slate-400 text-slate-600 leading-relaxed">{descText}</p>
                {item.content && <div className="mt-4">{item.content}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};
"""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Compositional Pattern Retrieval
# ─────────────────────────────────────────────────────────────────────────────

def get_pattern_library_context() -> str:
    """Returns curated pattern signatures for the Gemini 2.5 Flash prompt."""
    return f"""
AVAILABLE PRODUCTION REACT COMPONENTS IN THE PROJECT:
1. Navbar:
   import {{ Navbar }} from './components/Navbar';
   <Navbar title="..." links=[...] onActionClick={{() => setContactOpen(true)}} />

2. ThreeCanvas (Interactive 3D particle universe):
   import {{ ThreeCanvas }} from './components/ThreeCanvas';
   <ThreeCanvas particleCount={{6000}} particleColor="#818cf8" />

3. BentoGrid:
   import {{ BentoGrid }} from './components/BentoGrid';
   <BentoGrid title="..." subtitle="..." items=[...] />

4. ContactModal (with Canvas Confetti celebration):
   import {{ ContactModal }} from './components/ContactModal';
   <ContactModal isOpen={{contactOpen}} onClose={{() => setContactOpen(false)}} recipient="..." />
"""
