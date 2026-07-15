# Optional Ollama Integration Plan

This project is not using Ollama in production right now.

The live assistant continues to use the current Wikipedia/database/RAG flow because the app is deployed on Vercel and cannot directly call an Ollama server running on a local machine via `localhost`.

## Why it was deferred

- Vercel Functions cannot reach a developer PC's local `http://localhost:11434`.
- A live Ollama integration would require a reachable external URL for the Ollama host.
- The requirement for free resources makes managed hosted inference a poor fit.
- The current assistant issues were mostly routing and retrieval problems, not just absence of an LLM.

## Practical free architecture for later

Use a self-hosted Ollama machine and expose it safely over HTTPS:

`Vercel app -> public HTTPS tunnel -> self-hosted machine -> Ollama on localhost:11434`

Recommended path:

1. Run Ollama on a Windows/Linux machine that stays online.
2. Expose it through Cloudflare Tunnel.
3. Protect the tunnel with Cloudflare Access service tokens.
4. Configure the backend to call the public Ollama URL instead of `localhost`.
5. Keep the current assistant as fallback when Ollama is unavailable.

## Models already selected

- Chat/classification/synthesis: `qwen3.5:4b`
- Embeddings for semantic retrieval: `qwen3-embedding:0.6b`

These were chosen because they are relatively lightweight and support multilingual usage, including Arabic.

## Intended usage in this project

Ollama should be optional, not a hard dependency.

Preferred roles:

- semantic retrieval over `playerInfo/*.md`
- query rewriting for paraphrased Arabic/English questions
- answer synthesis from retrieved evidence
- fallback classification for hard-to-route questions

Avoid using Ollama as:

- the sole source of truth
- a replacement for deterministic totals/stat rows
- a required dependency for every live request

## Backend configuration shape

If implemented later, use environment variables similar to:

```env
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=https://your-ollama-endpoint.example.com
OLLAMA_CHAT_MODEL=qwen3.5:4b
OLLAMA_EMBED_MODEL=qwen3-embedding:0.6b
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_ACCESS_CLIENT_ID=...
OLLAMA_ACCESS_CLIENT_SECRET=...
```

## Safe rollout plan

1. Add an `ollama_client.py` module for `/api/chat` and `/api/embed`.
2. Add feature-flagged backend integration using `OLLAMA_ENABLED`.
3. Generate embeddings for player markdown chunks and store them in a local index file.
4. Use embedding search before lexical fallback.
5. Use the chat model only for synthesis/classification after retrieval.
6. Preserve the current non-Ollama path as default fallback.
7. Enable it in production only after the public tunnel is stable and authenticated.

## Operational constraints

- The host machine must remain on and connected.
- If the tunnel goes down, Ollama becomes unavailable.
- Latency will be higher than local-only usage.
- Rate and uptime will depend on the machine hosting Ollama.

## Decision

Current status: deferred.

Reason: the current live assistant works without Ollama, and a free production-grade bridge requires extra infrastructure outside the Vercel app itself.
