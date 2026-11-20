# Render Deployment Guide

## Quick Summary
Your app is ready for Render! This document guides you through deploying the dlbot to production.

## Prerequisites
- GitHub account with the dlbot repository pushed to `main` branch
- Render.com account (free tier available)
- Telegram Bot Token (from BotFather)
- Your Telegram User ID (for admin access)

## Step 1: Push to GitHub

```bash
cd dlbot
git add .
git commit -m "chore: prepare for Render deployment"
git push origin main
```

**Important:** Never commit `.env` file. It's already in `.gitignore`.

## Step 2: Create Render Blueprint

1. Go to [https://render.com/dashboard](https://render.com/dashboard)
2. Click **"New"** → **"Blueprint"**
3. Select your GitHub repository
4. Configure:
   - **Name:** `dlbot`
   - **Branch:** `main`
   - **Render config file:** `render.yaml`
5. Click **"Create Blueprint"**

## Step 3: Set Environment Variables

Render will show a form to fill in variables marked with `sync: false`. Provide:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | Your Telegram bot token from BotFather |
| `WEBHOOK_SECRET` | Leave empty - Render generates this |
| `PUBLIC_BASE_URL` | Leave empty - Render will show you the URL after deploy |
| `ADMIN_IDS` | Your Telegram user ID (see below) |
| `TERMS_URL` | Link to your terms page (or placeholder URL) |

### How to find your Telegram User ID:
1. Send `/start` to @userinfobot on Telegram
2. It will show your user ID

## Step 4: Deploy

1. Click **"Deploy Blueprint"**
2. Wait for all services to start (5-10 minutes on first deploy)
3. Once complete, copy your web service URL: `https://bot-webhook-xxxx.onrender.com`

## Step 5: Register Webhook

After deployment completes:

1. Update `PUBLIC_BASE_URL` in Render dashboard with your service URL
2. In a local terminal, run:
   ```bash
   cd dlbot
   export BOT_TOKEN="your-bot-token"
   export PUBLIC_BASE_URL="https://bot-webhook-xxxx.onrender.com"
   export WEBHOOK_SECRET="use-the-value-from-render"
   python scripts/set_webhook.py
   ```

3. Verify output shows:
   ```
   setWebhook: True
   getWebhookInfo: {'url': 'https://bot-webhook-xxxx.onrender.com/telegram/webhook', ...}
   ```

## Step 6: Test

Send a TikTok, YouTube, or Facebook link to your bot on Telegram. It should:
1. Respond with 🎬 Video / 🎵 Audio buttons
2. Process your selection and download
3. Send the file back

## Monitoring

Go to Render Dashboard to:
- **View logs:** Click service → "Logs"
- **Check status:** See green checkmarks for all services
- **Monitor errors:** Watch for 5xx errors or worker crashes

### First 48 hours
Monitor that:
- Error rate stays below 20%
- Worker doesn't restart in loops
- Telegram messages are processed successfully

## Troubleshooting

### "Connection refused" in logs
**Cause:** Database/Redis not ready yet  
**Fix:** Wait 2 minutes, then restart web service from Render dashboard

### "password authentication failed"
**Cause:** Credentials mismatch  
**Fix:** Verify `DB_USERNAME` and `DB_PASSWORD` are auto-linked from Blueprint database

### Telegram not sending updates
**Cause:** Webhook not registered or URL is wrong  
**Fix:** 
1. Verify `PUBLIC_BASE_URL` matches your Render service URL
2. Run `python scripts/set_webhook.py` again
3. Check if old webhook is still registered: BotFather → `/mybots` → bot name → "Bot Settings"

### Worker not processing jobs
**Cause:** Redis or database connection failed  
**Fix:** Check worker logs, restart worker service from dashboard

## Production Checklist

- [ ] All services show green "Active" status
- [ ] `GET /health` returns 200
- [ ] Webhook registered (run `set_webhook.py`)
- [ ] Test message processed successfully
- [ ] Check logs for errors
- [ ] Enable Render alerts (web 5xx rate, worker restarts)

## Resources

- [Render Docs: Docker Deployment](https://render.com/docs/docker)
- [Render Docs: PostgreSQL](https://render.com/docs/databases)
- [Render Docs: Background Workers](https://render.com/docs/background-workers)
- [Telegram Bot API: setWebhook](https://core.telegram.org/bots/api#setwebhook)

## Next Steps (Post-Deployment)

After 48 hours of stable operation (error rate < 20%, no worker loops):

1. **Phase 5 - Operations:**
   - Add `scripts/cleanup_logs.py` (cleanup old requests every 90 days)
   - Add GitHub Actions CI workflow

2. **Monitoring:**
   - Set Render alerts for 5xx errors, worker restarts, Redis memory

3. **Future Enhancements:**
   - Phase 3: Run yt-dlp spike on Render worker to find watermark-free TikTok streams
   - Phase 4: Add `/language` command for Khmer support
   - Phase 6: Soft-launch with 20-50 users, monitor metrics

---

**Deployed by:** Claude  
**Deployment Date:** 2026-11-20  
**Status:** Ready for Render
