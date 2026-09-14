import os

# ⚡ ZERO-VRAM LAZY LOADER (2026 Modern Architecture)
# PyTorch and Shap-E are deferred until explicitly invoked to preserve 0 MB RAM/VRAM at startup.
_torch_checked = False
HAS_SHAP_E = False
device = None

def _ensure_runtime():
    global _torch_checked, HAS_SHAP_E, device
    if _torch_checked:
        return HAS_SHAP_E
    try:
        import torch
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        HAS_SHAP_E = True
    except Exception as e:
        print(f"[WARN] 3D Engine disabled (torch/shap_e blocked by OS): {e}")
        HAS_SHAP_E = False
        device = None
    _torch_checked = True
    return HAS_SHAP_E

def generate_local_3d_model(prompt, filename="output_model.obj"):
    """Sculpts a 3D model using OpenAI Shap-E and saves it to disk."""
    print(f"\n[🧠 3D ENGINE]: Sculpting '{prompt}'...")
    
    if not _ensure_runtime():
        print("[⚠️ 3D ENGINE]: Runtime unavailable (PyTorch or Shap-E missing).")
        return None

    try:
        import gc
        import torch
        from shap_e.diffusion.sample import sample_latents
        from shap_e.diffusion.gaussian_diffusion import diffusion_from_config
        from shap_e.models.download import load_model, load_config
        from shap_e.util.notebooks import create_pan_cameras, decode_latent_images, decode_latent_mesh

        # ⚡ OPTIMIZATION 1: Forcefully clear all VRAM junk before starting
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            
        print("[⚙️ 3D ENGINE]: Loading Neural Weights into VRAM...")
        xm = load_model('transmitter', device=device)
        model = load_model('text300M', device=device)
        diffusion = diffusion_from_config(load_config('diffusion'))
            
        # ⚡ 4GB VRAM OPTIMIZATION: use_fp16=True prevents memory crashes ⚡
        latents = sample_latents(
            batch_size=1,
            model=model,
            diffusion=diffusion,
            guidance_scale=15.0,
            model_kwargs=dict(texts=[prompt]),

            progress=True,
            clip_denoised=True,
            use_fp16=True, 
            use_karras=True,
            karras_steps=24, # ⚡ OPTIMIZATION 2: Dropped from 64 to 24. Massive speed boost!
            sigma_min=1e-3,
            sigma_max=160,
            s_churn=0,
        )

        # Extract the 3D Mesh and convert it to standard 3D triangles
        mesh = decode_latent_mesh(xm, latents[0]).tri_mesh()
        
        # Save it as an .obj file so our UI can read it
        save_path = os.path.join(os.path.dirname(__file__), '..', 'ui', 'web', filename)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'w') as f:
            mesh.write_obj(f)
            
        print(f"[✅ 3D ENGINE]: Model sculpted and saved to {save_path}")
        
        # ⚡ VRAM LEAK FIX: explicitly delete heavy objects and GC
        del model, xm, diffusion, latents, mesh
        gc.collect()
        
        # Clean up VRAM after we are done
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            
        return save_path
        
    except Exception as e:
        print(f"[⚠️ 3D ENGINE ERROR]: GPU Memory overload or generation failure. Error: {e}")
        return None

# --- QUICK TEST SCRIPT ---
if __name__ == "__main__":
    # Test it by running this file directly!
    generate_local_3d_model("a highly detailed futuristic smartphone")