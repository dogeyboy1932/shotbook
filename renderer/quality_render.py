"""Standalone Wan2.2-TI2V-5B quality render (720p) -- the 'quality' path.
Run with the .venv-quality venv (newer diffusers). Mirrors the team's
generate_video.py but uses the 5B model at 720p via diffusers.

  CUDA_VISIBLE_DEVICES=0 .venv-quality/bin/python renderer/quality_render.py \
      --prompt "your fully assembled shot prompt" --out test5b.mp4 --frames 81 --steps 40
"""
import argparse, time
import torch
from diffusers import WanPipeline, AutoencoderKLWan
from diffusers.utils import export_to_video

MODEL = "wan_models/Wan2.2-TI2V-5B-Diffusers"
NEG = ("blurry, low quality, distorted, deformed, extra limbs, bad anatomy, "
       "watermark, text, cartoon, 3d render, flickering, morphing")

ap = argparse.ArgumentParser()
ap.add_argument("--prompt", required=True)
ap.add_argument("--out", default="test5b.mp4")
ap.add_argument("--frames", type=int, default=81, help="must be 4k+1 (81=~3.4s, 121=~5s @24fps)")
ap.add_argument("--steps", type=int, default=40)
ap.add_argument("--height", type=int, default=704)
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--guidance", type=float, default=5.0)
args = ap.parse_args()

print("loading Wan2.2-TI2V-5B ...", flush=True)
t = time.time()
vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae", torch_dtype=torch.float32)
pipe = WanPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=torch.bfloat16)
pipe.to("cuda")
print(f"loaded in {time.time()-t:.0f}s; generating {args.frames} frames @ {args.steps} steps "
      f"({args.width}x{args.height}) ...", flush=True)

t = time.time()
frames = pipe(prompt=args.prompt, negative_prompt=NEG, height=args.height, width=args.width,
              num_frames=args.frames, num_inference_steps=args.steps,
              guidance_scale=args.guidance).frames[0]
dt = time.time() - t
export_to_video(frames, args.out, fps=24)
print(f"[5B] {len(frames)} frames in {dt:.0f}s = {len(frames)/dt:.1f} FPS -> {args.out}", flush=True)
