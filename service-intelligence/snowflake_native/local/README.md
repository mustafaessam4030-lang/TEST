# VELA AI local — Ollama on your computer + Snowflake AI Brain

The same VELA app (Analysis, Recommendations, Assistant) running on your computer. A local **Ollama** model
understands the questions, plans, writes the answers and reads photos. **Snowflake** stays the knowledge: fleet
data, training library, repair history, videos, rules, web search — through a **read-only** user.

| Runs on your computer | Runs in Snowflake |
|---|---|
| Ollama chat model (default `qwen2.5:14b`) | Every data query (read-only role `VELA_LOCAL_READER`) |
| Ollama vision model for photos (default `qwen2.5vl:7b`) | Cortex Search: training pages, video clips, repairs |
| Photos you attach (never uploaded) | Cortex web search, training pictures and videos |

Nothing is written to Snowflake from the local app (saving plans and chat logs is skipped).

## One-time setup (about 20 minutes)

1. **Ollama** — install from https://ollama.com, then in a terminal:
   ```
   ollama pull qwen2.5:14b
   ollama pull qwen2.5vl:7b
   ```
   Needs about 16 GB RAM (32 GB or a GPU is smoother). Smaller computers: `qwen2.5:7b`.
2. **Python 3.10–3.12** — from https://python.org (tick "Add to PATH").
3. **Key for Snowflake** — in a terminal inside `service-intelligence/snowflake_native`:
   ```
   pip install cryptography
   python local/make_key.py
   ```
   It saves the private key in your `.snowflake` folder and prints a public key.
4. **Snowflake user** — an admin opens `sql/30_local_vela_reader.sql` in a Snowsight worksheet, pastes the
   public key where it says so, and runs it (ACCOUNTADMIN).
5. **Connection** — copy `local/connections.toml.example` into `C:\Users\<you>\.snowflake\connections.toml`
   (Mac: `~/.snowflake/connections.toml`) and put your Windows user name in the key path.

## Start

- Windows: double-click `local/start_vela_local.bat` (first start installs the packages).
- Mac / Linux: `bash local/start_vela_local.sh`

The browser opens VELA. The model chip at the top of the Assistant shows `qwen2.5:14b · Ollama`.

## Options (set before starting)

| Setting | Default | Meaning |
|---|---|---|
| `VELA_OLLAMA_MODEL` | `qwen2.5:14b` | any Ollama chat model, e.g. `llama3.1:8b`, `qwen2.5:32b` |
| `VELA_VISION_MODEL` | `qwen2.5vl:7b` | photo model; empty = photos not read |
| `VELA_LLM` | `ollama` | `cortex` = use Snowflake's Claude instead (to compare answers) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama on another machine / server |
| `VELA_NUM_CTX` | `16384` | context window; lower it if the computer runs out of memory |
| `VELA_SNOWFLAKE_CONNECTION` | `vela` | connection name in connections.toml |

## Good to know
- Local models are weaker than Claude at planning, SQL, Arabic and photos; every figure is still checked against
  the query results, and unmatched figures are flagged under the answer.
- The data VELA retrieves for a question is sent to your computer (to the local model); it is not stored.
- If something is missing: the app shows what to do (start Ollama, `ollama pull <model>`, check connections.toml).
