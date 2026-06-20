import os
# Single H100 is plenty for the 5B model. Change to e.g. "3" to pick a different GPU.
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import numpy as np
import torch
import imageio
from PIL import Image
from diffusers import WanPipeline, AutoencoderKLWan, UniPCMultistepScheduler

# ---------------------------------------------------------------------------
# 1. Load the model
#    - VAE in float32 (bf16 VAE decode causes NaN/garbage frames)
#    - transformer in bfloat16 (fast on H100)
# ---------------------------------------------------------------------------
print("Loading Wan2.2 TI2V-5B...")
# model_id = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
model_id = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"

vae = AutoencoderKLWan.from_pretrained(model_id, subfolder="vae", torch_dtype=torch.float32)
pipe = WanPipeline.from_pretrained(model_id, vae=vae, torch_dtype=torch.bfloat16)
pipe.to("cuda")
pipe.vae.enable_tiling()   

# Apply flow_shift by rebuilding the scheduler (mutating .config alone is a no-op)
# pipe.scheduler.config.flow_shift = 12.0
# pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config)


# ---------------------------------------------------------------------------
# 2. Generate frames
# ---------------------------------------------------------------------------
prompt="A realistic, cinematic fantasy depiction of the tortoise and the hare race. An anthropomorphic hare with expressive features speeds past a towering oak tree on a winding forest path, looking back mockingly. In the background, a wise, weathered tortoise moves with slow, deliberate, powerful steps. Sunbeams filtering through the forest canopy, highly detailed fur and shell textures, dramatic depth of field, epic storytelling atmosphere, 8k resolution."
# with open("prompt.txt", "r") as file:
#     file_content = file.read()
    
#     # Split at the "=" sign to separate the variable name from the string
#     string_part = file_content.split("=", 1)[1].strip()
    
#     # Safely evaluate the Python string literal (handles the parentheses and quotes)
#     prompt = ast.literal_eval(string_part)

negative_prompt = (
    "Bright tones, overexposed, static, blurred details, subtitles, "
    "worst quality, low quality, JPEG compression residue"
)

print("Generating native 720p video frames...")
frames = pipe(
    prompt=prompt,
    negative_prompt=negative_prompt,
    width=1280,
    height=720,
    num_frames=81,
    guidance_scale=5.0,
    num_inference_steps=50,
).frames[0]

# ---------------------------------------------------------------------------
# 3. Encode to mp4 with yuv420p (default encoder produces green playback)
# ---------------------------------------------------------------------------
def to_uint8(fr):
    if isinstance(fr, Image.Image):
        return np.asarray(fr)
    a = np.asarray(fr, dtype=np.float32)
    if a.max() <= 1.01:        # frames in [0,1] -> [0,255]
        a = a * 255.0
    return np.clip(a, 0, 255).astype(np.uint8)

output_filename = "wan22_h100_output.mp4"
print(f"Saving rendering to {output_filename}...")

writer = imageio.get_writer(
    output_filename,
    fps=16,
    codec="libx264",
    pixelformat="yuv420p",                       # the actual green-screen fix
    output_params=["-crf", "18", "-movflags", "+faststart"],
)
for fr in frames:
    writer.append_data(to_uint8(fr))
writer.close()

print(f"Success! Wrote {output_filename} (yuv420p, plays everywhere).")