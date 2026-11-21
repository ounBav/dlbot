# Production Troubleshooting Quick Reference

## 🆘 Service Won't Start

### Symptom
- Render dashboard shows "Failed" status
- Build logs show errors

### Quick Diagnosis
1. Check Docker build logs
2. Look for first ERROR in output
3. Check if secrets are set

### Common Causes & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `unable to prepare context` | .env or secrets in image | Verify `.env` is in `.gitignore` |
| `ModuleNotFoundError` | Missing dependency | Check `requirements.txt` |
| `No module named uvicorn` | Bad requirements.txt | Reinstall: `pip install -r requirements.txt` |
| `Connection refused` | Port already in use | Restart service |

---

## 🔴 "Port binding failed" or "No open ports detected"

### What This Means
Render couldn't find the application listening on port 8000

### Diagnosis Checklist
```bash
# 1. Check if service is running
# Render Dashboard → Service → Status

# 2. Check Docker logs
# Render Dashboard → Service → Logs
# Look for: "Uvicorn running on"

# 3. Check application crashed
# Look for: Traceback, ImportError, ValueError
```

### Common Causes

**A. Missing PORT environment variable**
- Dockerfile must have: `ENV PORT=8000`
- Uvicorn command must use: `--port ${PORT}`
- Check: `Render Dashboard → Environment`

**B. Application won't start**
- Look for Python errors in logs
- Check database connection
- Verify configuration is valid

**C. Health check failing**
- Endpoint returns 500 error
- Render can't reach /health
- Check Redis connection

### How to Fix
```bash
# Step 1: Verify Dockerfile
grep "EXPOSE 8000" Dockerfile
grep "PORT=" Dockerfile

# Step 2: Check logs for errors
# (In Render dashboard)

# Step 3: Restart service
# (In Render dashboard → Service → Restart)

# Step 4: Verify health check
curl https://your-service.onrender.com/health
```

---

## 🔴 Database Connection Failed

### Symptom
```
"error connecting in 'pool-1': failed to resolve host 'dpg-xxx.render-db.local'"
```

### Root Causes

**A. Database credentials not injected**
- Render blueprint references wrong database name
- Database service not running
- Environment variables not set

**Fix:**
```
Render Dashboard → Service → Environment
Verify: DB_HOST, DB_PORT, DB_USERNAME, DB_PASSWORD show actual values
(NOT "fromDatabase: bot-db")
```

**B. Using .env with localhost**
- .env file baked into Docker image
- Local config overriding Render's variables

**Fix:**
```bash
# Verify .env is NOT in image
grep "COPY . ." Dockerfile | head -1
# Should see: RUN rm -f .env

# Or verify in docker-compose.yml
# Never mount .env in production
```

**C. Database initializing**
- PostgreSQL still starting up
- Connection pool timed out

**Fix:**
```
Wait 30 seconds and restart web service
Render Dashboard → Service → Restart
```

### Verification
```bash
# Check database is running
# Render Dashboard → bot-db → Status: "Available"

# Check connection pooling
# Render Dashboard → bot-db → Connections: 2-5 (normal)

# View app logs for connection errors
# Render Dashboard → bot-webhook → Logs
```

---

## 🔴 "WEBHOOK_SECRET not set" Warning

### What This Means
Application is using default webhook secret (not production-safe)

### How to Fix

**Step 1: Set in Render Dashboard**
```
Render Dashboard → bot-webhook → Environment
Key: WEBHOOK_SECRET
Value: generateValue (Render will auto-generate)
OR: Your own 32+ character secret
```

**Step 2: Redeploy**
```
Application will read new value on restart
Render Dashboard → Service → Restart
```

**Step 3: Verify**
```bash
# No more warning in logs
curl https://your-service.onrender.com/health | grep -i warning
```

---

## 🔴 Webhook Not Receiving Messages

### Symptom
- Bot responds to commands
- But doesn't respond to media links
- No messages in logs

### Causes & Fixes

| Cause | Check | Fix |
|-------|-------|-----|
| Webhook URL wrong | `PUBLIC_BASE_URL` env var | Update in dashboard |
| Webhook not registered | Run `set_webhook.py` | See step 3 below |
| Old webhook still active | Telegram bot settings | Deregister old one |
| Bot token wrong | Env var value | Verify in dashboard |

### How to Fix

**Step 1: Verify webhook URL**
```
Render Dashboard → bot-webhook → Environment
PUBLIC_BASE_URL should be: https://bot-webhook-xxxx.onrender.com
(NOT http://, NOT localhost, NOT old URL)
```

**Step 2: Register webhook**
```bash
export BOT_TOKEN="your-bot-token"
export PUBLIC_BASE_URL="https://bot-webhook-xxxx.onrender.com"
python scripts/set_webhook.py
# Should print: "Webhook set successfully"
```

**Step 3: Test**
```bash
# Check health endpoint
curl https://bot-webhook-xxxx.onrender.com/health
# Should return: {"status":"ok"}

# Send test message to bot on Telegram
# Should receive response within 2 seconds
```

---

## 🔴 Worker Not Processing Jobs

### Symptom
```
- Download enqueued but nothing happens
- Worker logs empty
- Job not completed
```

### Causes

| Cause | Symptom | Fix |
|-------|---------|-----|
| Redis not connected | ERROR in worker logs | Check REDIS_URL env var |
| Worker crashed | No new log entries | Check logs for traceback |
| Queue corrupted | Jobs stuck forever | Check Redis connection |
| Config wrong | Worker never starts | Verify WORKER_CONCURRENCY |

### Diagnosis

**Step 1: Check Redis**
```
Render Dashboard → bot-redis → Status
Should show: "Active" (green)
```

**Step 2: Check worker logs**
```
Render Dashboard → bot-worker → Logs
Should see: "Job processed" or "Starting worker"
```

**Step 3: Check job queue**
```
Render Dashboard → bot-redis
Watch memory usage (should be normal for queue size)
```

### How to Fix

**A. Worker crashed**
```
Render Dashboard → bot-worker → Restart
```

**B. Redis disconnected**
```
Render Dashboard → bot-redis → Restart
Then restart worker
```

**C. Job stuck**
```bash
# View Redis state (requires redis-cli or Render console)
# Check if old job is still there
# Manual restart of worker usually resolves
```

---

## 🔴 High Memory Usage or CPU Spikes

### Diagnosis
```
Render Dashboard → Service → Metrics
Watch: CPU %, Memory %, Request count
```

### Common Causes

| Issue | Cause | Fix |
|-------|-------|-----|
| Memory leak | App holding objects | Restart service |
| CPU spike | Large download | Monitor queue size |
| Slow query | Database query | Check logs for duration |

### Quick Fixes
```bash
# Restart web service (clears memory)
Render Dashboard → bot-webhook → Restart

# Restart worker
Render Dashboard → bot-worker → Restart

# Check if downloads are queued
Render Dashboard → bot-redis → Check memory
```

---

## 🔴 "Timed Out" or "Unhealthy"

### Symptom
- Render shows service as "Unhealthy"
- Deployment times out after 15+ minutes

### Causes
1. Application won't start (Python error)
2. Health endpoint not responding (500 error)
3. Port not opened (binding failed)
4. Database/Redis not available

### Diagnostic Steps

**Step 1: Check deployment logs**
```
Render Dashboard → Service → Deployment
Click most recent build
Scroll to end for errors
```

**Step 2: Check application logs**
```
Render Dashboard → Service → Logs
Look for first ERROR after deployment
```

**Step 3: Check health endpoint**
```bash
curl -v https://your-service.onrender.com/health
# Should return 200 OK within 2 seconds
```

**Step 4: Restart in this order**
```
1. Render Dashboard → bot-db → Restart
   (Wait until Status shows "Available")
2. Render Dashboard → bot-redis → Restart
   (Wait until Status shows "Active")
3. Render Dashboard → bot-webhook → Restart
4. Render Dashboard → bot-worker → Restart
```

---

## 🆘 Still Not Working?

### Information to Gather
1. Full error from logs (copy-paste)
2. Service status (web/worker/db/redis)
3. When did it last work?
4. What changed recently?

### Advanced Debugging

**Check application code for issues**
```bash
# Run tests locally
python -m pytest -v

# Check for import errors
python -c "from app import main"

# Verify dependencies installed
pip list | grep -E "fastapi|psycopg|redis"
```

**Manual deployment verification**
```bash
# Build docker image locally
docker build -t dlbot:test .

# Run image to check startup
docker run -it -p 8000:8000 \
  -e BOT_TOKEN="test" \
  -e WEBHOOK_SECRET="test" \
  -e DB_HOST="localhost" \
  -e REDIS_URL="redis://localhost" \
  dlbot:test

# Check if it starts and /health responds
```

---

## 📞 Getting Help

### Resources
- [Render Documentation](https://render.com/docs)
- [FastAPI Docs](https://fastapi.tiangolo.com)
- [PostgreSQL Docs](https://www.postgresql.org/docs)
- [Redis Docs](https://redis.io/documentation)

### Information to Include When Asking for Help
1. Full error message/traceback
2. Service status (all 4 services)
3. Recent deployment logs (last 100 lines)
4. What you changed recently
5. When it last worked

---

**Quick Links**
- [Production Deployment Guide](DEPLOY.md)
- [Improvements Explained](IMPROVEMENTS.md)
- [Configuration Reference](app/config.py)
- [Application Code](app/main.py)

---

**Last Updated**: 2026-11-20  
**Status**: Complete ✅
