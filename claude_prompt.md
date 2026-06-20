You are a Principal Software Architect and Systems Engineer specializing in distributed AI pipelines and high-throughput LLM orchestration.

### THE SYSTEM OBJECTIVE
We are building a backend for an interactive book-to-video platform. The end-user application lets a reader highlight any paragraph in a book and instantly generate a 30-second cinematic video with cloned voice dialogue (XTTS) and abstract sound effects (Stable Audio/SFX). 

To support non-linear, on-demand generation, we need an ingestion pipeline that parses a book paragraph-by-paragraph and saves the world state into a 3-tier relational database schema. This schema guarantees that any isolated paragraph can be queried to build a 100% self-contained prompt payload for the downstream video/audio models.

### OUR INFRASTRUCTURE
- Database: PostgreSQL
- Backend API: Python (FastAPI)
- Compute: 8x H100 GPUs running local inference (via vLLM)
- Processing Model: Meta-Llama-3-70B-Instruct or Qwen2-72B-Instruct

### YOUR TASK
Generate the complete blueprint and foundational code implementation for this system. Provide this in three distinct sections:

#### 1. POSTGRESQL DATABASE SCHEMA (SQL)
Write the complete SQL DDL schema representing our 3-Tier architecture. Ensure proper foreign keys, constraints, and optimization indexes (especially on paragraph boundaries for sequential queries):
- Tier 1: Global Registry (`characters`, `locations`) storing baseline immutable visuals and baseline audio/vocal reference prompts.
- Tier 2: Temporal Ledger (`character_states`, `location_states`) tracking delta changes over time, using `valid_from_paragraph_id` and an optional `valid_until_paragraph_id`.
- Tier 3: Paragraph Beats (`paragraphs`) tracking the exact text, active entity lists, immediate actions, framing, dialogue scripts, and discrete SFX prompts.

#### 2. PARALLEL PREPROCESSING ORCHESTRATOR (Python)
Write a Python orchestration script that manages the ingestion of a book. It must:
- Use data parallelism to split the book chunks efficiently across the 8x H100 cluster (mocking or implementing an engine like `vLLM` or an async worker pool).
- Implement a two-pass architecture: Pass 1 populates the Tier 1 Global Registry. Pass 2 injects this registry as system context to dynamically extract Tier 2 (Temporal Ledger) and Tier 3 (Paragraph Beats) via structured JSON outputs.
- Include robust error-handling, database session injection, and JSON validation logic.

#### 3. ON-DEMAND COMPILE API ENDPOINT (FastAPI)
Write a FastAPI endpoint `/api/generate-context/{paragraph_id}`. When a user requests a video for a specific paragraph, this endpoint must:
- Execute a single optimized query (or transaction) to fetch the paragraph beat.
- Resolve the exact character and location visual/vocal states at that exact timeline slice by combining Tier 1 baseline data with the active Tier 2 modifiers for that paragraph index.
- Return a single, fully pre-compiled JSON payload ready to be passed directly to video diffusion, XTTS, and SFX generation pipelines.

Provide fully documented, production-grade code without missing placeholders or shorthand logic.
