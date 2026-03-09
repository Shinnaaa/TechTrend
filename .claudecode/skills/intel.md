# Skill: /intel

Run the full Intelligence-Node-1 pipeline:
fetch → analyze → push

## Steps

Execute the following bash commands **in sequence** from the project root directory.
Report status after each step. If any step fails, stop and show the error clearly.

```bash
# Step 1: Fetch raw data
python fetcher.py
```

If Step 1 succeeds (raw_intel.json created/updated), continue:

```bash
# Step 2: Analyze with GPT and generate report
python summarizer.py
```

If Step 2 succeeds (DAILY_REPORT.md created/updated), continue:

```bash
# Step 3: Push to Enterprise WeChat
python pusher.py
```

## Expected Output

After all three steps, display a summary:

```
📡 Intelligence-Node-1 Pipeline Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Step 1 — Fetcher   : raw_intel.json (N items)
✅ Step 2 — Summarizer: DAILY_REPORT.md (N chars)
✅ Step 3 — Pusher    : N message(s) sent to WeCom
```

## Prerequisites

Ensure the following environment variables are set:
- `OPENAI_API_KEY` — OpenAI or DeepSeek API key
- `OPENAI_BASE_URL` — (optional) custom API base URL, e.g. for DeepSeek
- `OPENAI_MODEL` — (optional) model name, default `gpt-4o-mini`
- `PUSHPLUS_TOKEN` — PushPlus 推送 Token
