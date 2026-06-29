# Demo Video — Structure (5:00)

**Total:** 1:00 + 2:30 + 1:00 + 0:30 = 5:00
**Running example:** pick one vivid, visual scene from our **public-domain corpus** (e.g. the
creation scene in *Frankenstein*, or a Poe set-piece) and reuse it in every section — it ties
the demo to our actual dataset and sidesteps any rights issues.

---

## 1 · Problem & Hook — [0:00–1:00]
- **Cold open (≤10s):** show the end artifact first — highlight a sentence from the chosen scene,
  the cinematic clip appears. Lead with the payoff, then earn the "how."
- **One-liner:** "Interactive book-to-video: highlight any passage, get a grounded cinematic clip."
- **Problem framing (2 fast beats):**
  - Reading has friction → this lowers the barrier to immersing in literature.
  - Studios sit on untouched literary IP → this makes adaptation cheap to prototype.
- **Goal + intended impact (1 line):** change how readers *and* studios interact with books.
- *Tip:* sell the feeling first — no architecture yet.

## 2 · Walkthrough — [1:00–3:30]  *(the core — this is where we win)*
- **Thesis up front (~15s):** "The model isn't the edge — the **data** is. Here's why."
- **Part A — Ingestion (speedrun, ~50s):**
  - Raw book text → ingestion pipeline → per-line **state** (characters, location, setting, situation).
  - **The money beat — RAG vs. ours (call it out explicitly):**
    - *RAG:* retrieves semantically *similar* chunks — no timeline; can resurface a dead character.
    - *Ours:* resolves the **exact world-state at that point in the story** — deterministic, plot-hole-proof.
  - Show the 8×H100s building state start → end.
- **Part B — Video generation (speedrun, ~50s):**
  - Selected text + resolved state → LLM prompt → open-source video model → clip.
  - Emphasize: **no architecture changes** — just a better *inference-time harness*
    (compute + context + autoregressive prompting).
- **Baseline comparison (~25s) — do this live on screen:**
  - Same highlight, two outputs: **bare prompt (weak baseline)** vs **state-grounded**.
  - Let the contrast carry the argument (wrong setting/characters vs. faithful scene).
- *Tip:* pre-render everything; narrate over it. Never generate live on stage.

## 3 · Audience & Use-Cases — [3:30–4:30]
- **Three users, one line + a visual each:**
  - **Readers** — immersion, scene comprehension, tracking the story.
  - **Studios** — drop a book into the pipeline → rapid adaptation / previz of dormant IP.
  - **Video-model training** — the structured story knowledge base as grounded training data.
- **Vs. competitors (one beat):** Higgsfield = freestyle/prompt-driven; **we're constrained to
  the book's canonical state** — that constraint *is* the product.
- **Trajectory line:** at scale this shifts both the reader community and the movie industry.

## 4 · Recap & Close — [4:30–5:00]
- **3-bullet recap:** (1) grounded, not freestyle; (2) state pipeline kills plot holes;
  (3) better output with zero model retraining.
- **One-sentence vision restatement** — bookend back to the cold open.
- **Close on the ask / what's next** (team, traction, or "try it").

---

## Cross-cutting tips
- Recurring thread = **one scene** in every section; repetition makes it stick.
- Sharpest differentiators = the **RAG-vs-state** contrast and the **baseline side-by-side** —
  give those the most screen time; trim everything else.
- Put the **best clip at 0:10**, not 4:00 — hook before explanation.
