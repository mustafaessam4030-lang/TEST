# 17 · Claude as Maia's voice

If Maia's answers are labelled **"local engine"**, no chat model is connected.
She is replying from built-in rules. Connecting Claude takes one file.

## Connect it

1. Copy `claude.example.txt` to `claude.txt` in `C:\SIS\maia\`.
2. Replace `sk-ant-PASTE-YOUR-KEY-HERE` with your Anthropic API key.
3. Double-click `START-MAIA.bat`. Step 3 should say `Claude: using claude.txt`,
   and answers now show the model name instead of "local engine".

Instead of the file you can set `ANTHROPIC_API_KEY` in the window that starts Maia.

## How it is wired

```
chat page ── {system, messages} ──▶ gateway /v1/llm/messages ── key ──▶ Claude
```

- The key lives only with the gateway. The page never receives it, and it is
  never logged or returned.
- The gateway chooses the model (`MAIA_LLM_MODEL`, default `claude-opus-5`) and
  caps the reply length. Whatever model or limit the page asks for is ignored.
- Server-side fallback is on (`fallbacks: "default"`). If a turn is declined,
  Anthropic re-runs it on its recommended fallback model instead of returning
  an empty answer.
- Effort is `low` (`MAIA_LLM_EFFORT`), because chat is latency-sensitive.
- If there is no key, the key is rejected, or the model is unreachable, the page
  falls back to its local engine. The chat never stops working.
- Equipment facts still come only from the tools. Claude is given the record
  and the rules for it (`promptBlock`), and the attribution stamp is added after
  it writes.

`claude.txt` is excluded from git and from the release package.
