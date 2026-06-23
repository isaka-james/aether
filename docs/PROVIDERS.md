# Choosing your AI model

Aether works with whatever model you like. Switch by editing `.env` and restarting the
backend (`docker compose restart backend`), or by re-running `scripts/install.sh`.

Which should you pick?

- **Local**: free and fully private. Best with a good GPU. Recommended.
- **DeepSeek**: the cheapest cloud option, about 2 dollars a month even with heavy use.
- **Claude or OpenAI**: the most capable, and the most expensive.

```ini
AETHER_LLM_PROVIDER=deepseek   # deepseek, openai, anthropic, or local
AETHER_LLM_MODEL=              # optional, override the default model
```

Only the selected provider's key is needed. Open `http://localhost:8473/api/health` to see
which model is active.

### DeepSeek (default, cheap, fast)

```ini
AETHER_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
```

Get a key at <https://platform.deepseek.com>

### OpenAI

```ini
AETHER_LLM_PROVIDER=openai
AETHER_LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
```

Get a key at <https://platform.openai.com>

### Anthropic Claude

```ini
AETHER_LLM_PROVIDER=anthropic
AETHER_LLM_MODEL=claude-opus-4-8
ANTHROPIC_API_KEY=sk-ant-...
```

Get a key at <https://console.anthropic.com>

### Local (runs on your own machine, no key)

Use [Ollama](https://ollama.com), LM Studio, llama.cpp, or any OpenAI-compatible server.

```bash
ollama serve && ollama pull llama3.1
```

```ini
AETHER_LLM_PROVIDER=local
AETHER_LLM_MODEL=llama3.1
# Using a different server? add: AETHER_LLM_BASE_URL=http://host.docker.internal:1234/v1
```

A local model keeps everything on your computer. It works best with a good GPU, or with some
patience on a CPU. That is why the default is a cloud provider.
