# Architecture

## Flow

1. **Ingest** (once per book) — `ingestion/` runs two Claude passes over the text
   and writes the 3-tier world-state to Supabase. Triggered from the UI
   (`POST /ingest` on the renderer) or the CLI.
2. **Read** — the frontend lists books/paragraphs straight from Supabase REST.
3. **Query** — a highlighted span calls `resolve_contexts(paragraph_ids[])`
   (Supabase RPC) to resolve the exact character/location state at that point.
4. **Generate** — the frontend posts those resolved contexts to the VM renderer
   `POST /generate`. The renderer asks Claude for a 1–4 shot breakdown grounded in
   the state, then renders the whole passage as **one seamless rollout** and streams
   it back as live MJPEG; the finished mp4 is saved.

No middle API tier: the browser talks only to Supabase and the VM renderer.

## The 3-tier world-state (Supabase)

- **Tier 1 — registry** (`characters`, `locations`): immutable baseline
  appearance/voice/lore extracted in ingestion Pass 1.
- **Tier 2 — temporal ledger** (`character_states`, `location_states`): append-only
  rows valid over `[valid_from_paragraph_id, valid_until_paragraph_id)`. Each row
  holds the entity's **full current state** (carry-forward), so reading is "take the
  latest row" — no backfill. This is where "Character A is now wounded / terrified /
  in darkness" lives. Written in Pass 2.
- **Tier 3 — paragraph beats** (`paragraphs`, `paragraph_characters`): per-paragraph
  text, active entities, camera framing, action, dialogue, SFX. `sequence_index`
  (not `paragraph_id`) is the timeline axis.

`resolve_contexts` joins Tier 1 + the latest Tier 2 row ≤ the target paragraph and
returns appearance (with the active delta), **emotional status**, location
appearance, **lighting/atmosphere**, dialogue, SFX, and profiles.

## Shot planning (renderer/planning.py)

- `compose_scene` merges the span's resolved contexts into one scene.
- `generate_video_plan` asks Claude (structured output) for camera/action/light +
  continuity per shot, then deterministically splices the **fixed world anchors**
  (appearance, current status, setting + atmosphere, style) into every shot's prompt
  so identity/look never drift and the full state is grounded in each clip.

## Rendering (renderer/renderer.py + vendor/cf_streaming.py)

The vendored Causal-Forcing **Wan2.1-T2V-1.3B** streaming model renders the passage
in continuous **segments**, split at every `cut_new_scene`:
- within a segment, one autoregressive rollout carries the KV-cache forward and
  `ramp_to` SLERP-morphs the prompt across same-scene shot changes — seamless, no
  stitch;
- at a genuine scene break, a **fresh rollout** (new noise + cleared KV/cross-attn/
  VAE caches) gives a real hard cut instead of one face morphing into another.

Frames stream as decoded (`multipart/x-mixed-replace`). A per-job `PromptBus`
(`POST /jobs/{id}/steer`) lets the viewer steer a running render: the loop appends
the typed text to the active shot prompt and morphs toward it, holding steady when
idle. The engine and its attribution are detailed in `_docs/NOVELTY.md`.

## Notable engineering
- Supabase reached via the **pooler** (VM is IPv4-only) with verified TLS against the
  extracted Supabase root CA.
- flash-attn installed from a prebuilt wheel (no nvcc on the box).
- Ingestion uses **prompt-based JSON** (not grammar-constrained structured output),
  which the deeply-nested beat schema requires.

## Paused / future
- **Audio** (dialogue TTS + SFX) is paused; the data is still captured. Old services
  are in the gitignored `archive/`.
- **M2 real-time injection**: start the rollout from a state-derived bootstrap prompt
  and inject Claude's shots into the running stream, dropping first-frame latency from
  ~11s to ~1–2s.
