# Where the novelty lives

**Short answer: [`renderer/vendor/cf_streaming.py`](../renderer/vendor/cf_streaming.py).**

ShotBook's defensible, real-time idea is *perceived-immediate, steerable* video
generation: frames appear as the model is prompted, the picture morphs smoothly
between shots, cuts hard at genuine scene breaks, and a viewer can steer the
render live with text. All of that is one file — the streaming engine
`cf_streaming.py` — plus the thin orchestration that drives it.

It is an **adaptation of the SPED reference repo**
(<https://github.com/bryandong24/SPED>) and gino's causal-forcing fork, not a
library we import. The novelty is in *how* we drive a distilled autoregressive
video model in a single continuous rollout and inject prompts into it mid-flight.

> Attribution note: the engine header also credits "SPEED"
> (<https://howardxiao.ca/speed/>) for the progressive-resolution trick. If
> `bryandong24/SPED` and that "SPEED" are the same work under a different name,
> they should be collapsed into one citation — please confirm and we'll dedupe.

## The key pieces (all in `vendor/cf_streaming.py`)

| Symbol | Lines (approx) | What it does | Source |
|---|---|---|---|
| `StreamingCF.start` | ~180 | Allocates the full noise tensor and **inits the KV self-attention + cross-attention caches**; encodes the first prompt. A fresh `start()` = a clean slate. | upstream + adapted |
| `StreamingCF.step` | ~210 | One denoising chunk: advances an in-progress ramp, runs the first-chunk 4-step / later 2-step schedule, applies **SPEED** low-res leading steps, commits the chunk's K/V at `context_noise`. | upstream (SPED) |
| `StreamingCF.decode_chunk` | ~285 | Streaming VAE decode of one chunk to uint8 frames. | upstream |
| `StreamingCF.ramp_to` | ~205 | **Smooth shot transition**: per-token SLERP of the prompt embedding old→new over `k` chunks (min-jerk eased) so the picture morphs without a seam. | upstream |
| `StreamingCF.hardcut` | ~195 | Re-encode + reset cross-attn for an abrupt conditioning swap. | upstream |
| `PromptBus` | ~60 | Thread-safe "current prompt + version" — the channel an external UI/ASR thread uses to **steer a running render**. | upstream, wired up by ShotBook |
| `_slerp` / `_minjerk` | ~70 | The interpolation + easing math behind `ramp_to`. | upstream |

## What ShotBook adds on top (the application of the novelty)

- **`renderer/renderer.py::stream_plan`** — drives the engine for a whole
  highlighted passage. It partitions the planned shots into continuous
  **segments** split at every `cut_new_scene`: within a segment it `ramp_to`-morphs
  between shots (one unbroken take); at a genuine scene break it starts a **fresh
  rollout** (new noise + cleared KV/cross-attn/VAE caches) = a real hard cut. This
  is what makes "narrator's face → a separate insert of the eye" two distinct
  scenes instead of one face melting into another.
- **Live steering (`PromptBus` wired through)** — `renderer/main.py` creates a
  `PromptBus` per job and exposes `POST /jobs/{id}/steer`; `stream_plan` reads the
  bus each chunk and `ramp_to`s toward `"<active shot prompt> <user steer>"` when
  the version changes, holding steady otherwise. This is the "video generates as
  the model is prompted" interaction.
- **Planner → engine contract** — `renderer/planning.py` (Claude) decides each
  shot's `continuity`; the engine honours it (morph vs. hard cut). The planner is
  *what* to show; the engine is *how* to render it in real time.

## If you have to point at one thing in a demo

Point at **`StreamingCF` in `vendor/cf_streaming.py`** for the engine, and
**`stream_plan` in `renderer/renderer.py`** for how ShotBook turns a shot plan
into a live, steerable, hard-cut-aware rollout.
