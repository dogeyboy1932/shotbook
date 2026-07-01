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
| `StreamingCF.steer` / `restore_window` | ~220 | **The _SPED "post-swap KV-window shrink" strength lever, applied as a CONTINUOUS edit.** A plain prompt swap RESISTS (the self-attn KV cache holds the old frame's momentum, so "baseball cap" changes nothing); a hard cut over-corrects (resets cross-attn → the model reinvents a different person every chunk = flicker). `steer` instead SLERP-morphs (`ramp_to`) toward the new description so the SAME frame transforms, AND shrinks the self-attn window (`_set_window`, ~6 frames) so the change actually takes without flushing the subject. Paired with a target prompt (from `compose_steer_prompt`) that shares the subject and differs only in the change, this gives a focused hood→cap morph. `restore_window` settles it. `realtime_steer_window` = edit-strength knob. | **_SPED findings** (gino/findings/FINDINGS.md "★ WORKING METHOD"), wired by ShotBook |
| `PromptBus` | ~60 | Thread-safe "current prompt + version" — the channel an external UI/ASR thread uses to **steer a running render**. | upstream, wired up by ShotBook |
| `_slerp` / `_minjerk` | ~70 | The interpolation + easing math behind `ramp_to`. | upstream |

## What ShotBook adds on top (the application of the novelty)

- **`renderer/renderer.py::stream_plan`** — drives the engine for a whole
  highlighted passage as a **sequence of beats**. It partitions the planned shots
  into continuous **segments** split at every `cut_new_scene`: within a segment it
  `ramp_to`-morphs between beats (one unbroken take); at a genuine scene break it
  starts a **fresh rollout** (new noise + cleared KV/cross-attn/VAE caches) = a real
  hard cut. This is what makes "leaves the house → gets in the car → drives away"
  read as sequential beats, and "narrator's face → a separate insert of the eye"
  two distinct scenes instead of one face melting into another.
- **Live control (`RenderControl`, built on the PromptBus idea)** — `renderer/main.py`
  creates a `RenderControl` per job and exposes
  `POST /jobs/{id}/{takeover,steer,pause,resume,finish}`; `stream_plan` polls it each
  chunk and publishes a `phase` on `GET /jobs/{id}`. Three phases:
  - **running** — the planned beats generate in order.
  - **buffering** — planned beats done; a countdown runs before composing. The user
    can **pause/resume** the countdown, **skip** (finish → compose now), or
    **takeover**. Lapsing untouched composes.
  - **takeover** — the user pressed Takeover: each **steer** is **queued** and drained
    in order — one at a time, `gen.steer` (SLERP-morph + `_SPED` window-shrink) onto
    the target then render one scene — and between/after the queue the rollout **holds
    on the last frame**. The steer text is first merged with the **running description
    of the current frame** (`compose_steer_prompt`, seeded from the handoff) via Claude,
    so the model edits the same frame (hood→cap on the same man) instead of cutting to a
    new character. The ONLY thing that composes is **Finish**; idling never does. Steers
    are capped at `max_steers_per_session`.
    Each rollout is allocated to `max_session_seconds` up front (deterministic seed →
    the planned beats render identically) so there's headroom for the queued scenes.
  This is the "video generates as the model is prompted" interaction.
- **Planner → engine contract** — `renderer/planning.py` (Claude) decides each
  shot's `continuity`; the engine honours it (morph vs. hard cut). The planner is
  *what* to show; the engine is *how* to render it in real time.

## If you have to point at one thing in a demo

Point at **`StreamingCF` in `vendor/cf_streaming.py`** for the engine, and
**`stream_plan` in `renderer/renderer.py`** for how ShotBook turns a shot plan
into a live, steerable, hard-cut-aware rollout.
