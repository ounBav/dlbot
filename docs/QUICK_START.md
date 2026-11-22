# Production Deployment - Quick Start Guide

## 🚀 Deploy in 5 Minutes

### Prerequisites
- Code pushed to GitHub
- Render.com account with Blueprint

### Step 1: Validate Configuration (1 min)
```bash
# Run validation script
./scripts/validate.sh

# Expected output: ✓ All validation checks passed!
```

### Step 2: Pre-Deployment Check (1 min)
```bash
# Check everything is ready
./scripts/deploy.sh --check-only

# Expected output: ✓ Pre-deployment checks passed
```

### Step 3: Deploy (Wait 3-5 min)
```bash
# Option A: Automatic (recommended)
git push origin main
# Render watches GitHub and auto-deploys

# Option B: Manual
# Go to: Render Dashboard → bot-webhook → Rerun Last Deploy
```

### Step 4: Verify Deployment (1 min)
```bash
# Check health
./scripts/deploy.sh --url https://bot-webhook-xxxx.onrender.com

# Expected output: ✓ Deployment verified successfully
```

### Step 5: Register Webhook (1 min)
```bash
# Set environment variables
export BOT_TOKEN="your-bot-token"
export PUBLIC_BASE_URL="https://bot-webhook-xxxx.onrender.com"

# Register webhook
python scripts/set_webhook.py
```

---

## ✅ Post-Deployment Checklist

- [ ] All 4 services show "Active" in Render dashboard
  - bot-webhook (web)
  - bot-worker
  - bot-db
  - bot-redis
  
- [ ] Health check passes: `curl https://bot-webhook-xxxx.onrender.com/health`

- [ ] Send test message to bot on Telegram

- [ ] Bot responds within 2 seconds

- [ ] Download job is processed

- [ ] File received in Telegram

- [ ] No errors in logs

---

## 📊 Key Improvements

| Issue | Before | After |
|-------|--------|-------|
| Deployment time | 15+ min (timeout) | 3-5 min ✅ |
| Port binding | Failed | Works ✅ |
| DB connection | Refused | Retries gracefully ✅ |
| Security | .env in image | .env removed ✅ |
| Health checks | None | Docker + Render ✅ |
| Debugging | Unclear | Clear error messages ✅ |

---

## 📚 Documentation

| Document | When to Read |
|----------|--------------|
| [DEPLOY.md](DEPLOY.md) | Complete deployment guide |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Something went wrong |
| [IMPROVEMENTS.md](IMPROVEMENTS.md) | Understand what was fixed |
| [DEPLOYMENT_SUMMARY.md](DEPLOYMENT_SUMMARY.md) | Full overview |

---

## 🆘 Something Went Wrong?

### Service shows "Failed"
```bash
# Check logs
# Render Dashboard → Service → Logs

# Most common fixes:
./scripts/validate.sh          # Check configuration
./scripts/monitor.sh <url>     # Check health
# Then: Render Dashboard → Restart
```

### Health check failing
```bash
# Test the endpoint
curl https://bot-webhook-xxxx.onrender.com/health

# If fails: Check Redis is connected
# Render Dashboard → bot-redis → Status: should be "Active"
```

### Database not connecting
```bash
# Check database status
# Render Dashboard → bot-db → Status: should be "Available"

# If not: Wait 30 seconds and restart web service
# Render Dashboard → bot-webhook → Restart
```

### Still stuck?
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed guides

---

## 🔄 Monitor Production

### Quick Health Check
```bash
./scripts/monitor.sh https://bot-webhook-xxxx.onrender.com
```

### Continuous Monitoring
```bash
./scripts/monitor.sh https://bot-webhook-xxxx.onrender.com --continuous
```

### In Render Dashboard
- Go to each service (bot-webhook, bot-worker, bot-db, bot-redis)
- Check Status = "Active"
- Check Metrics for CPU/Memory usage
- Check Logs for errors

---

## 💡 Tips

**Before Deploying**
- Run all tests: `pytest`
- Commit all changes: `git status` (should be empty)
- Double-check secrets are NOT in .env: `git ls-files | grep .env`

**During Deployment**
- Watch Render dashboard for progress
- Check Docker build logs for errors
- Be patient (3-5 minutes is normal)

**After Deployment**
- Test with real Telegram message
- Monitor for first 5 minutes
- Check logs for any WARNING/ERROR entries

---

## 🎯 Success Criteria

Your deployment is successful when:
1. ✅ All services show "Active"
2. ✅ `/health` returns 200 OK
3. ✅ Bot responds to messages < 2 sec
4. ✅ Downloads process correctly
5. ✅ Files received in Telegram
6. ✅ No errors in logs after 5 min

---

## 📞 Quick Reference

| Command | Purpose |
|---------|---------|
| `./scripts/validate.sh` | Check configuration |
| `./scripts/deploy.sh --check-only` | Pre-deployment checks |
| `./scripts/deploy.sh --url <url>` | Verify deployment |
| `./scripts/monitor.sh <url>` | Check health |
| `git push origin main` | Trigger auto-deploy |
| `python scripts/set_webhook.py` | Register webhook |

---

**Ready to deploy? Start with:** `./scripts/validate.sh`
