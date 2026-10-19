# Virtual Horse Auction 🐴

Global multi-user horse auction with screenshot OCR, radar charts, timed cut-offs, **$25 minimum bid increments**, settable starting prices, and **Discord webhook notifications**.

## What’s included

- Upload in-game screenshots → OCR extracts Health / Stamina / Courage / Speed / Acceleration (editable)
- Radar chart for every horse
- Timed auctions (you set the duration)
- Starting price you choose
- Minimum bid raise of **$25**
- **Reserve price** (optional) – if final bid is below reserve the horse does not sell
- Listing page is **password protected** (password: EmeLay193)
- Discord webhook:
  - Posts a rich embed when a **new horse is listed**
  - Also notifies on **new high bids**
- Multi-user: anyone with the public URL can list and bid

---

## 1. Create a Discord Webhook (2 minutes)

1. Open your Discord server → the channel where you want notifications.
2. Edit Channel → Integrations → Webhooks → **New Webhook**.
3. Name it e.g. “Horse Auction”, copy the **Webhook URL**.
4. Keep this URL secret – anyone with it can post to your channel.

You will paste this URL into the app secrets in the next step.

---

## 2. Make it live (recommended free options)

### Option A – Streamlit Community Cloud (fastest)

1. Create a free GitHub repository and upload everything in this folder.
2. Go to [https://share.streamlit.io](https://share.streamlit.io) → sign in with GitHub.
3. **New app** → select your repo → Main file path: `app.py` → Deploy.
4. After it deploys, click the three dots → **Settings** → **Secrets**.
5. Add this (replace with your real webhook):

```toml
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/your/webhook/url"
```

6. Save. The app will now send Discord notifications.

**Public URL** will look like: `https://your-app-name.streamlit.app`  
Anyone in the world can open it.

> Note: Streamlit Cloud’s free tier has an ephemeral filesystem. The SQLite database and images can reset when the app sleeps. Fine for short auctions / testing. For long-running auctions use Option B.

### Option B – Railway (better persistence, still free tier)

1. Push the code to GitHub.
2. Go to [https://railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. Add a variable:
   - `DISCORD_WEBHOOK_URL` = your webhook URL
4. (Optional but recommended) Add a volume mounted at `/app/data` and `/app/uploads` so the database and images survive restarts.
5. Start command:
   ```
   streamlit run app.py --server.port $PORT --server.address 0.0.0.0
   ```
6. Railway gives you a public URL.

### Option C – Any small VPS

```bash
git clone <your-repo>
cd horse_auction
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
sudo apt install -y tesseract-ocr tesseract-ocr-eng   # Ubuntu/Debian

export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

---

## Local testing

```bash
pip install -r requirements.txt
# Ubuntu/Debian: sudo apt install tesseract-ocr tesseract-ocr-eng

# Optional local Discord test
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

streamlit run app.py
```

Or create `.streamlit/secrets.toml`:

```toml
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/your/url"
```

---

## How people use it

1. Open the public URL.
2. Enter a display name in the left sidebar.
3. **List a horse** (password required: `EmeLay193`):
   - Upload screenshot
   - Review / edit the five stats
   - Set **starting price**, **reserve price** (optional), and auction duration
   - Submit → Discord channel is notified automatically
4. **Bid**:
   - On Active Auctions, the next allowed bid is always current + $25
   - Place bid → Discord is also notified of the new high bid

---

## Requirements

```
streamlit>=1.30.0
pillow>=10.0.0
pytesseract>=0.3.10
plotly>=5.18.0
requests>=2.31.0
```

Plus system package `tesseract-ocr` (+ English data).

---

## Files

```
horse_auction/
├── app.py
├── requirements.txt
├── README.md
├── .streamlit/config.toml
├── data/          # SQLite database (auto-created)
└── uploads/       # Horse screenshots (auto-created)
```

---

Need anything else (Postgres for permanent storage, buy-now price, reserve, admin panel, better OCR for your specific game, etc.)? Just say the word.
