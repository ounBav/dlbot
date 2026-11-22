# Production Deployment Guide

## 🚀 Quick Start

### Local Development
```bash
# 1. Setup
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure local environment
cp .env.example .env
# Edit .env with your local values

# 3. Start services (requires Docker and docker-compose)
docker-compose up -d

# 4. Run the app
python -m uvicorn app.main:app --reload

# 5. Run worker (in another terminal)
python -m app.worker
```

### Testing
```bash
# Unit tests
pytest

# Health check
curl http://localhost:8000/health
```

---

## 📦 Production Deployment (Render)

### Prerequisites
- Repository pushed to GitHub
- Render.com account with a Blueprint
- PostgreSQL database created in Render
- Redis instance created in Render

### Deployment Process

#### **Step 1: Verify Configuration**
```bash
# Ensure no secrets are committed
git status
# Should NOT show: .env or any config files with real values

# Verify .gitignore protects secrets
cat .gitignore
# Should include: .env, .env.local, *.key, *.pem
```

#### **Step 2: Commit Changes**
```bash
git add .
git commit -m "deploy: update production configuration"
git push origin main
```

#### **Step 3: Monitor Deployment**

**Option A: Automatic (Recommended)**
- Render watches the GitHub repository
- Push triggers automatic redeploy
- Wait 5-10 minutes for build completion

**Option B: Manual Redeploy**
1. Go to [Render Dashboard](https://dashboard.render.com)
2. Select `bot-webhook` service
3. Click "Rerun Last Deploy" or "Manual Deploy"

#### **Step 4: Verify Health**
```bash
# Replace with your actual URL
curl https://bot-webhook-xxxx.onrender.com/health

# Expected response:
# {"status":"ok"}
```

#### **Step 5: Configure Webhook**
```bash
# Set environment variables
export BOT_TOKEN="your-bot-token"
export PUBLIC_BASE_URL="https://bot-webhook-xxxx.onrender.com"

# Register webhook with Telegram
python scripts/set_webhook.py
```

---

## 🔍 Monitoring & Troubleshooting

### Service Status Checklist
- [ ] `bot-webhook` shows "Active" (green) in Render dashboard
- [ ] `bot-worker` shows "Active"
- [ ] `bot-db` shows "Available"
- [ ] `bot-redis` shows "Active"

### Health Checks
```bash
# Check web service
curl https://bot-webhook-xxxx.onrender.com/health

# Check logs (if issues)
# Render Dashboard → Service → Logs

# Check database connectivity
# Render Dashboard → bot-db → Connections tab
```

### Common Issues

#### **Port binding failed**
- **Symptom**: "No open ports detected"
- **Cause**: Application not binding to port 8000
- **Fix**: 
  - Check Dockerfile EXPOSE and CMD
  - Verify PORT environment variable is set
  - Restart service

#### **Database connection refused**
- **Symptom**: "Name or service not known" for database host
- **Cause**: Using .env with localhost instead of Render's database
- **Fix**:
  - Verify `.env` is not in Docker image (removed in build)
  - Check environment variables in Render dashboard
  - Ensure bot-db service is active

#### **Migration fails on startup**
- **Symptom**: "Migration failed... couldn't get a connection after 60s"
- **Cause**: Database still initializing or connection pool exhausted
- **Fix**:
  - Wait 30 seconds and restart web service
  - Check database is fully initialized
  - View logs for specific error

#### **Webhook not receiving messages**
- **Symptom**: Bot doesn't respond to Telegram updates
- **Cause**: Webhook not registered or URL is wrong
- **Fix**:
  - Run `python scripts/set_webhook.py`
  - Verify PUBLIC_BASE_URL is set correctly
  - Check /health endpoint responds

#### **Worker not processing jobs**
- **Symptom**: Jobs enqueued but nothing happens
- **Cause**: Worker crashed or Redis connection lost
- **Fix**:
  - View bot-worker logs in Render dashboard
  - Check bot-redis is active
  - Restart worker service

---

## 🔐 Security

### Environment Variables
⚠️ **Never commit secrets to Git**

#### Production Secrets (in Render Dashboard)
- `BOT_TOKEN` - Telegram bot token
- `WEBHOOK_SECRET` - Auto-generated secure token
- `ADMIN_IDS` - Telegram user IDs

#### Auto-Injected by Render
- `DB_HOST` - From bot-db service
- `DB_PASSWORD` - From bot-db service  
- `REDIS_URL` - From bot-redis service

### .env Files
- `.env` - Local development only (in .gitignore)
- `.env.example` - Template for reference (safe to commit)
- Never commit `.env` to repository
- Never store real secrets in Docker image

---

## 📊 Deployment Checklist

### Pre-Deployment
- [ ] All tests passing locally: `pytest`
- [ ] Code reviewed and merged to main
- [ ] No debug code left in codebase
- [ ] No secrets in git history: `git log --grep=TOKEN`

### Deployment
- [ ] Commit pushed to GitHub
- [ ] Render dashboard shows new deploy in progress
- [ ] Docker build completes successfully

### Post-Deployment
- [ ] All services show "Active" status
- [ ] `/health` endpoint responds with 200 OK
- [ ] Webhook registered: `python scripts/set_webhook.py`
- [ ] Send test message to bot
- [ ] Bot responds within 2 seconds
- [ ] Download job completes in worker
- [ ] File received in Telegram
- [ ] No error logs in first 5 minutes

### Validation
- [ ] No "Connection refused" errors
- [ ] No "Migration failed" errors
- [ ] Database pool not maxed out
- [ ] Redis memory usage normal

---

## 🛠️ Manual Service Management

### Restart a Service
```
Render Dashboard → Service Name → "Restart"
```

### View Logs
```
Render Dashboard → Service Name → Logs
# Shows last 1000 lines of output
```

### Update Environment Variables
```
Render Dashboard → Service Name → Environment
# Edit and save - triggers restart
```

### Scale Resources
```
Render Dashboard → Service Name → Edit
# Change plan (affects cost and performance)
# Not recommended for this bot (single instance is sufficient)
```

---

## 📈 Performance Considerations

### Current Configuration
- Web service: Starter (0.5 CPU, 512 MB RAM)
- Worker: Standard (1 CPU, 512 MB RAM)
- Database: 256 MB
- Redis: Starter

### Scaling Strategy
If experiencing slowness:
1. Check metrics in Render dashboard (CPU %, memory %)
2. Scale web service if CPU > 80% consistently
3. Increase database plan if connections max out
4. Add more workers if job queue grows

### Monitoring Metrics
- Request latency (target: < 500ms)
- Database connection pool usage
- Redis memory usage
- Worker job queue depth

---

## 🔄 Rollback Procedure

If deployment fails:

1. **Check Render dashboard** for error details
2. **View logs** to identify root cause
3. **Fix the issue** in code or configuration
4. **Commit and push** to trigger redeploy
5. **If urgent**: Manually trigger deploy of previous commit

---

## 📞 Additional Resources

- [Render Documentation](https://render.com/docs)
- [FastAPI Production Deployment](https://fastapi.tiangolo.com/deployment)
- [PostgreSQL Connection Troubleshooting](https://www.postgresql.org/docs/current/runtime-config-connection.html)
- [Redis Best Practices](https://redis.io/topics/security)

---

**Last Updated**: 2026-11-20  
**Status**: Production Ready ✅
