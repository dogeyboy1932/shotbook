import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"   # single GPU

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
# THE WORLD BIBLE  (single source of truth — the persistent context)
# ---------------------------------------------------------------------------
# This is the whole trick for continuity in pure text-to-video. Each clip is
# generated independently, so the ONLY thing that keeps the hare, the tortoise,
# the forest and the color grade looking identical across all four shots is
# using the *exact same wording* every time. So we never hand-write character
# or setting descriptions inside a scene — we pull them verbatim from here.
#
# Edit a fact once here -> it changes consistently in all four clips.
# The descriptions are deliberately loaded with distinctive, repeatable details
# (white chest blaze, torn left ear, mossy cracked shell) so the model latches
# onto the SAME individual animal each time instead of a generic one.
# ===========================================================================
WORLD = {
    "walton": (
        "the same individual Robert Walton in every shot — a determined, ambitious explorer "
        "in his late twenties, dressed in heavy fur clothing for harsh northern climates, "
        "rugged and weathered from travel, with a thoughtful, somewhat melancholic expression"
    ),
    "stranger": (
        "the same individual gaunt stranger in every shot — a man with a hollow, emaciated "
        "face, wearing tattered ragged clothing, showing severe physical and emotional distress"
    ),
    "location": (
        "the same vast icy Arctic expanse of white, the deck and rail of a wooden sailing "
        "ship locked in ice, with occasional breaks in the ice revealing dark waters beneath"
    ),
    "look": (
        "photorealistic cinematic film still, 35mm lens, dramatic volumetric lighting, "
        "consistent color grade, highly detailed, film-like, steady continuous footage"
    ),
}

# Fixed template. Order matters in T2V: identity tokens go early, style anchors
# at the end, and the same connective phrasing is reused for every scene so the
# prompts differ ONLY by the action, camera and light — nothing else drifts.
def build_prompt(camera, action, light):
    return (
        f"{camera} Part of one continuous cinematic sequence. "
        f"{action} "
        f"The explorer is {WORLD['walton']}. The rescued man is {WORLD['stranger']}. "
        f"Setting: {WORLD['location']}. {light} {WORLD['look']}."
    )

# Lighting deliberately walks dawn -> morning -> midday -> sunset so the day
# progresses naturally, but the GRADE (WORLD['look']) stays identical, so it
# reads as one continuous piece rather than four disconnected clips.
SCENES = [
    ("01_the_question",
        build_prompt(
            camera="Medium shot from the deck rail, slight low angle, the camera holding steady.",
            action=(
                "The gaunt man down on the ice calls up across the gap, lifting his head to "
                "address the explorer at the rail, who leans forward in astonishment to answer him."
            ),
            light="Flat pale Arctic daylight.",
        ),
    ),
    ("02_coming_aboard",
        build_prompt(
            camera="Medium tracking shot following the figure as crew hands haul him over the rail onto the deck.",
            action=(
                "Crew members grip the frail man under the arms and pull him aboard; his legs "
                "buckle and he collapses, fainting against the deck planks."
            ),
            light="Cold overcast daylight.",
        ),
    ),
    ("03_the_revival",
        build_prompt(
            camera="Medium shot slowly pushing in, the crew gathered around the prone figure.",
            action=(
                "Kneeling crew rub the man's limbs and tip brandy to his lips; he stirs, is "
                "wrapped in blankets near the stove, and weakly sips a little soup as the "
                "explorer watches over him."
            ),
            light="Dim daylight warmed by the orange glow of the cabin stove.",
        ),
    ),
]

# Negative prompt is also part of the persistent context: the same "never do
# this" list applied to every clip keeps failure modes (bipedal stance, morphing,
# style/color jumps between shots) consistent and suppressed everywhere.
NEG = (
    "morphing, warping, melting, distortion, flickering, sudden cuts, jump cut, teleporting, "
    "disappearing objects, extra limbs, deformed, mutated, identity change between shots, "
    "color shift between shots, inconsistent lighting, overexposed, static frame, text, "
    "subtitles, watermark, worst quality, low quality, cartoon, 3d render, cgi, anime"
)

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