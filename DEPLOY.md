# Setup and deploy guide

## 1. Get your API keys

You need at least one key. More keys mean more fallbacks when a free tier runs out.

| Order | Provider | Where to get the key | Variables to set |
| --- | --- | --- | --- |
| 1 | Google Gemini | [aistudio.google.com](https://aistudio.google.com/) → Get API key | `GEMINI_API_KEY` |
| 2 | Groq | [console.groq.com](https://console.groq.com/) → API Keys | `GROQ_API_KEY` |
| 3 | OpenRouter | [openrouter.ai/keys](https://openrouter.ai/keys) | `OPENROUTER_API_KEY` |
| 4 | Mistral | [console.mistral.ai](https://console.mistral.ai/) → API Keys (Experiment plan) | `MISTRAL_API_KEY` |
| 5 | Cloudflare Workers AI | [dash.cloudflare.com](https://dash.cloudflare.com/) → AI → Workers AI → Use REST API → Create a Workers AI API token. The Account ID is shown on the same page | `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` |

## 2. Edit environment variables locally

From the repo root:

```bash
cp .env.example .env
```

Open `.env` and paste each key after its `=`, with no quotes or spaces:

```env
GEMINI_API_KEY=AIza...
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-...
MISTRAL_API_KEY=...
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ACCOUNT_ID=...
```

Leave a line empty to skip that provider. `.env` is in `.gitignore`, so it never gets committed. Never put keys in frontend code.

Optional settings:

| Variable | Default | What it does |
| --- | --- | --- |
| `LLM_PROVIDERS` | `gemini,groq,openrouter,mistral,cloudflare` | Fallback order. Remove a name to stop using that provider |
| `LLM_TIMEOUT_S` | `30` | Seconds to wait before moving to the next provider |
| `GEMINI_MODEL`, `GROQ_MODEL`, `OPENROUTER_MODEL`, `MISTRAL_MODEL`, `CLOUDFLARE_MODEL` | see README | Use a different model on that provider |

Restart the backend after editing `.env`. To check which providers are live, open
http://localhost:8000/api/providers. A provider shows `"configured": true` once its key is set.

## Run it locally on Windows (PowerShell)

Run these from the repo folder, e.g. `C:\Users\arnav\nl-analytics-agent`:

```powershell
# 1. Create a virtual environment and install the backend packages (one time)
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # the prompt now starts with (.venv)
pip install -r backend\requirements-dev.txt

# 2. Start PostgreSQL. With Docker Desktop:
docker compose up db -d
#    or install PostgreSQL 16 for Windows and set DATABASE_URL in .env to match your password, e.g.
#    DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/analytics

# 3. Create the "analytics" database and load the sample data (one time)
cd backend
python -m app.seed

# 4. Start the API on http://localhost:8000
uvicorn app.main:app --reload
```

In a new terminal, run `.\.venv\Scripts\Activate.ps1` again before any Python command.
`ModuleNotFoundError: No module named 'pydantic'` (or any other package) means the virtual environment isn't
active, or step 1 was skipped.

If PowerShell says running scripts is disabled, run this once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

To run the evaluation, go back to the repo root with the venv active and run `python eval\run_eval.py`.

## 3. Deploy on Railway

1. Sign in at [railway.com](https://railway.com) with GitHub.
2. Click **New Project** → **Deploy from GitHub repo** → pick `Arnavsharma2005/nl-analytics-agent`.
   Railway reads `railway.json` and builds the `Dockerfile`.
3. In the same project click **+ Create** (or **New**) → **Database** → **PostgreSQL**.
4. Open the app service → **Variables** → **Raw Editor**, and paste:

   ```env
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   GEMINI_API_KEY=...
   GROQ_API_KEY=...
   OPENROUTER_API_KEY=...
   MISTRAL_API_KEY=...
   CLOUDFLARE_API_TOKEN=...
   CLOUDFLARE_ACCOUNT_ID=...
   ```

   `${{Postgres.DATABASE_URL}}` is typed literally. Railway fills it in from the database. If you renamed the
   database service, use its name instead of `Postgres`.
5. Click **Deploy** (Railway asks to apply the variable changes). The pre-deploy step loads the sample data into
   the database on the first deploy only.
6. Open the service → **Settings** → **Networking** → **Generate Domain**. Your site is live at that URL.
   Check `https://<your-domain>/api/health` returns `{"status":"ok"}`.

To change a key later, edit it under **Variables**. Railway redeploys automatically.

## 4. Change the frontend after deploying

The frontend is in `frontend/src` (React + Vite). Work on it locally:

```bash
# terminal 1: backend
cd backend && uvicorn app.main:app --reload

# terminal 2: frontend with hot reload
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Push to `main` when you're happy. Railway rebuilds and redeploys on every push, because the service is linked
to the GitHub repo. The `Dockerfile` builds the frontend, so you don't commit `frontend/dist`.
