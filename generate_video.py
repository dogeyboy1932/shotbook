import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"   # single GPU

import argparse
import json
import subprocess
import numpy as np
import torch
import imageio
import imageio_ffmpeg
from PIL import Image
from diffusers import WanPipeline, AutoencoderKLWan

# ===========================================================================
# CONFIG
# ===========================================================================
T2V_MODEL = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"

WIDTH       = 832     # divisible by 16
HEIGHT      = 480     # divisible by 16
NUM_FRAMES  = 81
STEPS       = 30
GUIDANCE    = 5.0
FPS         = 16
SEED        = 42      # FIXED across all scenes -> nudges the latent space toward
                      # the same "look" each clip, which helps cross-clip consistency.

# ===========================================================================
# LOAD PLAN FROM UI
# Pass the JSON saved from POST /api/compose-scene
# ===========================================================================
parser = argparse.ArgumentParser()
parser.add_argument("plan", help="Path to the VideoPlanPayload JSON from the UI.")
cli = parser.parse_args()

with open(cli.plan) as f:
    data = json.load(f)

# Accept either a raw VideoPlanPayload or the full ComposedScenePayload (video key)
video_plan = data.get("video") or data
SCENES = [(s["shot_id"], s["prompt"]) for s in video_plan["shots"]]
NEG    = video_plan["negative_prompt"]
print(f"Loaded {len(SCENES)} shot(s) from {cli.plan}")

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

def to_uint8(fr):
    if isinstance(fr, Image.Image): return np.asarray(fr)
    a = np.asarray(fr, dtype=np.float32)
    if a.max() <= 1.01: a = a * 255.0
    return np.clip(a, 0, 255).astype(np.uint8)

def save_clip(frames, path):
    writer = imageio.get_writer(
        path, fps=FPS, codec="libx264", pixelformat="yuv420p",
        output_params=["-crf", "18", "-movflags", "+faststart"],
    )
    for fr in frames:
        writer.append_data(to_uint8(fr))
    writer.close()

# ===========================================================================
# GENERATE ALL SCENES (Pure Text-to-Video)
# ===========================================================================
print("Loading T2V model...")
vae = AutoencoderKLWan.from_pretrained(T2V_MODEL, subfolder="vae", torch_dtype=torch.float32)
t2v = WanPipeline.from_pretrained(T2V_MODEL, vae=vae, torch_dtype=torch.bfloat16)
t2v.to("cuda")
t2v.vae.enable_tiling()

for name, prompt in SCENES:
    # Resumable: skip a scene that's already rendered.
    if os.path.exists(f"wan22_{name}.mp4"):
        print(f"[{name}] already exists -> skipping.")
        continue

    print(f"[{name}] generating...")
    gen = torch.Generator(device="cuda").manual_seed(SEED)   # same seed each scene
    frames = t2v(
        prompt=prompt, negative_prompt=NEG,
        width=WIDTH, height=HEIGHT, num_frames=NUM_FRAMES,
        guidance_scale=GUIDANCE, num_inference_steps=STEPS, generator=gen,
    ).frames[0]

    save_clip(frames, f"wan22_{name}.mp4")
    print(f"[{name}] saved.")

# ===========================================================================
# STITCH ALL FOUR INTO ONE CONTINUOUS VIDEO
# ===========================================================================
print("\nStitching into final_story.mp4...")
clips = [f"wan22_{n}.mp4" for n, _ in SCENES]
cmd = [FFMPEG, "-y"]
for c in clips:
    cmd += ["-i", c]
cmd += [
    "-filter_complex", f"{''.join(f'[{i}]' for i in range(len(clips)))}concat=n={len(clips)}:v=1:a=0[v]",
    "-map", "[v]", "-c:v", "libx264", "-crf", "18", "-movflags", "+faststart",
    "final_story.mp4",
]
subprocess.run(cmd, check=True)
print("Done -> final_story.mp4 (one continuous ~20s cinematic sequence)")