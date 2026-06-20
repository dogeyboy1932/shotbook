# Data Pipeline — Temporal World-State Storage for Book→Video Generation

## 1. Objective

Support **non-linear, on-demand** generation: a reader highlights any paragraph and we
generate a 30-second cinematic clip (video + XTTS cloned voice + SFX). To do that without
plot holes, any isolated paragraph must resolve to a **100%-self-contained context payload**
describing the *exact* world state at that point in the story — which characters are alive
and present, what they look/sound like, where they are, their motivations, and the
hard continuity facts that constrain the scene.

Canonical failure to prevent: a character who died in paragraph 58 must **not** appear when
we visualize paragraph 240.

### Why this design over a temporal database
A temporal/bitemporal DB is purpose-built for "as-of N" interval queries, so it looks like the
obvious fit — but it optimizes the wrong half of the system. The request-time artifact
(`context_payloads`) is an **immutable, pre-resolved document**: once materialized, temporality
is irrelevant to it and a plain document/KV read is already optimal. A temporal engine only helps
the *write-side ledger*, and at a real cost — either a niche datastore (XTDB/Datomic) with a small
ecosystem and learning curve, or a second engine bolted alongside the document store. The current
design instead keeps the timeline as a plain integer (`reading_index`), owns the single interval
invariant in a small single-writer reducer, and serves everything from one store — cheaper to
operate and faster to ship. At MVP we are also **unitemporal** (one axis; the second `story_time`
axis only matters once flashbacks land), so the bitemporal machinery a temporal DB provides buys
little today. Engine-enforced interval correctness (PostgreSQL `int4range` + GiST exclusion
constraints) remains the documented upgrade path (§10) if extraction-side errors ever make
hand-maintained intervals a liability.

---

## 2. Validation Verdict (is this feasible / does it make sense?)

**Yes — the approach is sound and feasible.** The architecture below is the right pattern;
the risks are upstream of the database, not in it.

### What is sound
- **Event-sourcing + materialized read model (CQRS)** is a well-established fit for temporal,
  non-linearly-accessed state. The temporal ledger gives correctness + auditability +
  cheap re-derivation; the per-paragraph materialized payload gives O(1) plot-hole-proof serving.
- **MongoDB is an appropriate choice** despite the original spec saying PostgreSQL. The
  serving artifact is a self-contained nested document — a natural Mongo document. Temporal
  interval queries work fine with compound indexes. (Trade-off in §10.)
- **Compute is not a bottleneck.** A ~3,000-paragraph book through a 70B model on 8×H100
  (vLLM, tensor-parallel replicas, batched structured decoding) is minutes-to-tens-of-minutes.
  We materialize at ingestion, so request-time cost is a single document read.

### Where the real risk is (and the mitigation)
| Risk | Why it's the weak link | Mitigation |
|---|---|---|
| **Extraction accuracy** | LLM may mislabel a state delta (e.g. "dead") and it propagates | Confidence scores + source span grounding; schema-guided decoding; human-correctable ledger; re-materialize after fixes |
| **Coreference / alias resolution** | "he", same-named characters, nicknames | Pass-1 registry is *small* (dozens of entities/book) → **human-review checkpoint** before Pass 2 |
| **Flashback / non-linear narration** | Tense-based detection is noisy | Default to reading order; treat flashback as a *flagged, confidence-scored, overridable* enhancement — never a silent assumption |

### Honesty on guarantees
The continuity guarantee is **deterministic given correct extraction** — not "100% no plot
holes" in the absolute. The DB layer is exact; the LLM layer is probabilistic. We therefore
ship correction tooling and confidence gating, and we frame downstream continuity facts as
**hard constraints/negatives** to the generators (which maximizes, but cannot 100% force,
fidelity of a diffusion model).

**Conclusion:** proceed. Build the storage/pipeline as designed; invest remaining effort in
extraction quality and the registry-review checkpoint, which is where correctness is won or lost.

---

## 3. Core Design Decisions

1. **Two representations of one truth (CQRS):**
   - *Write model (source of truth):* append-only **temporal ledger** of state changes with
     validity intervals. Auditable, correctable, re-derivable.
   - *Read model (serving layer):* a fully-resolved `context_payload` document **per paragraph**,
     materialized at ingestion. The API serves these with a single point read.
2. **Timeline axis = `reading_index` (discourse order), integer, monotonic.** Not wall-clock.
   Plus a secondary `story_time` (narrative chronology) and `is_flashback` flag so flashbacks
   resolve against the *past* chronology and may legitimately show now-dead characters.
3. **Materialize up front.** Spend the 8×H100 budget at ingestion; never run an LLM at request time.
4. **Derived read model.** Any payload can be rebuilt from the ledger — extraction can improve
   without data loss, and a stale payload falls back to live resolution.

---

## 4. Collections & Document Shapes (MongoDB)

### `entities` — Tier 1, immutable baseline registry (characters + locations)
```json
{
  "_id": "char_patrick",
  "book_id": "lamb_to_the_slaughter",
  "type": "character",
  "canonical_name": "Patrick Maloney",
  "aliases": ["Patrick", "her husband", "he"],
  "baseline_visual_prompt": "late-30s man, police detective, tired eyes, 1950s suit",
  "baseline_vocal_ref": { "xtts_speaker_id": "spk_0012", "pitch": "low", "accent": "neutral-US" },
  "first_seen_index": 5
}
```

### `state_events` — Tier 2, temporal ledger (SCD-2 closed intervals, one per attribute)
```json
{
  "book_id": "lamb_to_the_slaughter",
  "entity_id": "char_patrick",
  "attribute": "lifecycle",          // lifecycle | location | presence | appearance | emotion | knowledge | possession
  "value": { "status": "dead", "cause": "struck with frozen lamb leg" },
  "valid_from": 58,                  // reading_index where it takes effect
  "valid_until": null,               // null = still in effect; closed when next change for same (entity,attribute) lands
  "story_time": 58,
  "source_paragraph_id": "p_058",
  "confidence": 0.94,
  "run_id": "ingest_v3"
}
```
`lifecycle` + `presence` + `location` are the plot-hole-critical attributes;
`emotion`/`knowledge`/`possession` drive motivation and props.

### `relations` — temporal edges (motivation is usually relational)
Same interval model: `{ subject_id, predicate: "killed"|"loves"|"suspects", object_id, valid_from, valid_until }`.
Optional but high-value for "why is this character acting this way."

### `context_payloads` — Tier 3, materialized read model (what the API serves)
```json
{
  "_id": "p_058",
  "book_id": "lamb_to_the_slaughter",
  "reading_index": 58,
  "story_time": 58,
  "is_flashback": false,
  "chapter": 1,
  "text": "<paragraph text>",
  "active_characters": [
    { "entity_id": "char_mary", "name": "Mary Maloney",
      "resolved_visual_prompt": "<baseline + appearance deltas merged>",
      "resolved_vocal_ref": { "xtts_speaker_id": "spk_0007" },
      "status": "alive", "emotion": "numb, calculating",
      "location_id": "loc_kitchen", "current_goal": "establish an alibi" }
  ],
  "active_location": { "entity_id": "loc_kitchen",
                       "resolved_visual_prompt": "1950s suburban kitchen, evening",
                       "ambient_audio_prompt": "quiet kitchen, faint clock tick" },
  "beat": {
    "actions": ["Mary slides the lamb roast into the oven"],
    "framing": { "shot": "medium close-up", "camera": "slow push-in", "mood": "eerie calm" },
    "dialogue": [{ "speaker": "char_mary", "line": "<line>", "vocal_ref": { "xtts_speaker_id": "spk_0007" } }],
    "sfx": [{ "prompt": "oven door clunk, low hum", "t_start_s": 0.0 }]
  },
  "continuity": {
    "present_entity_ids": ["char_mary"],
    "absent_or_unavailable": [{ "entity_id": "char_patrick", "reason": "dead@58" }],
    "canon_facts_in_effect": ["Patrick is dead", "weapon = frozen lamb leg", "evening, indoors"]
  },
  "retrieval": { "embedding": [/* 1024-d, optional */], "salient_prior_beats": ["p_041", "p_055"] },
  "ledger_version": "ingest_v3"
}
```
The `continuity` block is the **plot-hole firewall**: `absent_or_unavailable` +
`canon_facts_in_effect` are injected as hard negative/constraint context into the
video and dialogue prompts.

---

## 5. State Resolution ("state as-of N")

```python
def resolve_entity_state(db, book_id, entity_id, axis_value):
    base = db.entities.find_one({"_id": entity_id})
    state = {"visual": base["baseline_visual_prompt"], "vocal": base["baseline_vocal_ref"]}
    events = db.state_events.find({
        "book_id": book_id, "entity_id": entity_id,
        "valid_from": {"$lte": axis_value},
        "$or": [{"valid_until": None}, {"valid_until": {"$gt": axis_value}}],
    }).sort("valid_from", 1)
    for ev in events:           # later events overwrite earlier within the window
        state[ev["attribute"]] = ev["value"]
    return merge_into_prompts(base, state)
```
`axis_value = story_time if beat.is_flashback else reading_index`. Closed intervals
(maintained at ingestion) make this a few indexed point lookups per active entity — fast
enough to also run live as the fallback path.

---

## 6. Relevance Budget (how "relevant context" is measured)

Highlighting paragraph N should not dump *all* prior state — assemble a ranked, bounded set:

- **MUST (hard continuity, never dropped):** resolved states of currently-present entities,
  active location, `canon_facts_in_effect`. Deterministic, no model judgment.
- **SHOULD (causal / motivational):** each present character's `current_goal` + the most
  recent event that set it. Ranked by recency.
- **NICE (semantic, optional):** top-k prior beats by **vector similarity** to the highlighted
  text, **pre-filtered to `reading_index ≤ N`** — catches foreshadowing/callbacks.

`score = w1·presence + w2·recency_of_last_change + w3·cosine_sim + w4·relational_distance`.
Fill a token budget MUST → SHOULD → NICE. Correctness (MUST/SHOULD) needs **no** vector store;
semantic retrieval is purely additive.

---

## 7. Ingestion Pipeline (8×H100 / vLLM)

Key principle: **separate parallel GPU-heavy extraction from cheap sequential state-closing.**

- **Pass 0 — Segment (CPU):** split into paragraphs/beats; assign `reading_index`; detect
  chapters; flag candidate flashbacks (tense shifts, temporal cue phrases). Merge trivially
  short paragraphs, split over-long ones into ≤30s beats. Idempotent on `(book_id, reading_index)`.
- **Pass 1 — Global Registry (GPU, data-parallel):** shard chunks across vLLM replicas,
  extract entity mentions + candidate visuals/voices → **reduce/canonicalize** (alias + coref
  clustering) into `entities`. **→ Human-review checkpoint** (registry is small) before Pass 2.
- **Pass 2 — Temporal extraction (GPU parallel) → interval-close (CPU sequential):** inject the
  reviewed registry as system context; per paragraph window emit **structured JSON** (active
  entities, state deltas, beat = actions/framing/dialogue/sfx) via vLLM **guided decoding against
  a Pydantic/JSON schema**. Extraction is embarrassingly parallel. A **single-writer reducer**
  then walks paragraphs in order and closes intervals (`valid_until = next.valid_from`).
- **Pass 3 — Materialize (parallel):** per paragraph, run `resolve_entity_state` + relations
  (+ embeddings if enabled) → write `context_payloads`. Re-runnable any time extraction improves.

**Robustness:** every write is an idempotent upsert keyed by stable id and tagged with `run_id`;
JSON validated against schema with a bounded repair/retry loop; failed paragraphs quarantined,
not fatal; read model fully re-derivable from the ledger without re-running GPUs.

---

## 8. On-Demand Compile API (FastAPI)

`GET /api/generate-context/{paragraph_id}`:
1. Single `find_one` on `context_payloads` by `_id` → already self-contained.
   *(Fallback: if `ledger_version` is stale, resolve live via §5 and lazily re-cache.)*
2. Split the payload into three downstream envelopes and return in one response:
   - `video_prompt` — visual + framing, with `absent_or_unavailable` as **negative** prompts
   - `xtts_jobs` — per-dialogue `{ speaker_ref, line }`
   - `sfx_jobs` — `{ prompt, t_start_s }`

No joins, no replay, no LLM at request time → predictable low latency for 30s on-demand gen.

---

## 9. Indexes

- `context_payloads`: `{ book_id: 1, reading_index: 1 }` unique; `_id` is the paragraph id;
  (optional) Atlas Vector index on `retrieval.embedding`.
- `state_events`: `{ book_id: 1, entity_id: 1, attribute: 1, valid_from: 1 }`;
  `{ book_id: 1, valid_from: 1, valid_until: 1 }` for "who is active at N" scans.
- `entities`: `{ book_id: 1, type: 1 }`.
- `relations`: `{ book_id: 1, valid_from: 1, valid_until: 1 }`.

---

## 10. Trade-off: MongoDB vs PostgreSQL

The original spec said PostgreSQL. Relational rigor would buy native FK constraints and
multi-row transactional consistency for the interval ledger. We compensate in Mongo with a
**single-writer reducer** (correct interval closure), **schema validation**, and idempotent
upserts. Mongo wins on the serving side: the payload is a nested document served as-is.
*If* strict ledger integrity later dominates, a hybrid (Postgres ledger + Mongo read model)
is a clean evolution — but it is over-engineering for now. **Recommendation: Mongo-only.**

---

## 11. Phased Rollout (buildable in hackathon time)

- **MVP (correctness path):** `entities` + `state_events` + materialized `context_payloads`;
  deterministic MUST/SHOULD tiers; single vLLM replica; **skip** flashbacks + vector search.
  Proves the dead-character guarantee end-to-end on the existing `data/` corpus.
- **V1:** scale to 8×H100 data-parallel; add the registry-review checkpoint; relations.
- **V2:** flashback axis; Atlas Vector Search NICE tier; live-resolution fallback + re-materialization.

---

## 12. Open Decisions
- **MongoDB Atlas vs self-hosted** — Atlas gives turnkey Vector Search (NICE tier);
  self-hosted needs a bolt-on vector index (Qdrant/pgvector) or skips semantic retrieval.
- **Beat granularity** — strict paragraph vs ≤30s semantic beat windows (affects segmentation in Pass 0).
- **Extraction model** — Llama-3-70B-Instruct vs Qwen2-72B-Instruct (both fine; pick on JSON-adherence benchmarks).
