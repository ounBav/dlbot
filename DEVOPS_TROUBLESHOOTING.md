# DevOps Troubleshooting Guide

## Error: "Connection refused" on localhost:5432

### 🔴 What Went Wrong

```
Connection refused
Is the server running on that host and accepting TCP/IP connections?
connection to server at "127.0.0.1", port 5432 failed
```

**Root Cause:** The Docker container was trying to connect to PostgreSQL on `localhost` (127.0.0.1), which only loops back to the same container. Render's database is a **remote service** with a different hostname.

**Why It Happened:**
1. The `.env` file was baked into the Docker image during build
2. Pydantic-settings loaded `.env` file first, using `DB_HOST=localhost`
3. This .env configuration overrode the runtime environment variables from Render's Blueprint
4. The app tried to connect to `localhost:5432` which doesn't exist

---

## ✅ The Fix (Already Applied)

### **What Changed**

**1. Dockerfile**
```dockerfile
# BEFORE: .env was included in image
COPY . .

# AFTER: Remove .env, use only runtime environment variables
COPY . .
RUN rm -f .env
```

**2. app/config.py**
```python
# BEFORE: Always loaded .env, even in production
model_config = SettingsConfigDict(env_file=".env", extra="ignore")

# AFTER: Only load .env if it exists (graceful fallback)
env_file = ".env" if os.path.exists(".env") else None
model_config = SettingsConfigDict(
    env_file=env_file,
    extra="ignore",
    case_sensitive=False,
)
```

### **How This Fixes It**

1. **Local Development:** `.env` exists locally, so app loads it
2. **Production (Render):** `.env` doesn't exist in image, so app uses only Render's environment variables
3. **Render injects via Blueprint:** Database host is auto-linked from the managed database service
4. **Connection succeeds:** App connects to Render's actual database hostname

---

## 🚀 How to Redeploy

### **Step 1: Pull the Latest Code**

```bash
cd dlbot
git pull origin main
```

Verify the commit:
```
git log --oneline | head -1
# Should show: fix: remove .env from Docker image to use Render env vars
```

### **Step 2: Redeploy on Render**

Option A: **Automatic redeploy (Recommended)**
- Render will auto-detect the push and redeploy
- Wait 5-10 minutes for new build to complete
- Check Render dashboard for green checkmarks

Option B: **Manual redeploy**
1. Go to Render Dashboard → bot-webhook service
2. Click "Manual Deploy" or "Rerun Last Deploy"
3. Wait for build and deploy to complete

### **Step 3: Verify Deployment**

```bash
# Check health endpoint (replace with your URL)
curl https://bot-webhook-xxxx.onrender.com/health

# Should return: {"status":"ok"}
```

### **Step 4: Test Bot**

Send a TikTok/YouTube/Facebook link to your bot. It should:
1. Respond with 🎬 Video / 🎵 Audio buttons
2. Process your selection
3. Send the file back

---

## 🔍 How Environment Variables Work in Render

### **Render Blueprint Environment Variable Priority**

```
1. Runtime Environment Variables (Render Dashboard) ← HIGHEST PRIORITY
2. Environment Variables from Services (database, redis)
3. .env file in Docker image (if present) ← LOWEST PRIORITY
```

**Before Fix:**
- `.env` (localhost) overrode Render's database connection
- Result: Connection refused ❌

**After Fix:**
- `.env` doesn't exist in image
- Render's environment variables are used
- Result: Connection succeeds ✅

### **Render's Auto-Linked Variables**

From `render.yaml`:

```yaml
- key: DB_HOST
  fromDatabase:
    name: bot-db
    property: host          # Render provides: dpg-xxx.render-db.local
- key: DB_PASSWORD
  fromDatabase:
    name: bot-db
    property: password      # Render provides: secure-generated-password
```

Render **automatically injects** these values at runtime.

---

## 🛠️ Common Render Deployment Issues & Fixes

### **Issue 1: "Connection refused" (Just Fixed)**

| Symptom | Cause | Fix |
|---------|-------|-----|
| PostgreSQL won't connect | App using localhost instead of Render's host | Update Dockerfile to remove .env (done) |

### **Issue 2: "FATAL: password authentication failed"**

```json
{"level": "ERROR", "msg": "password authentication failed for user \"bot\""}
```

| Cause | Fix |
|-------|-----|
| Wrong password in .env being used | Environment variable override |
| Database not fully initialized | Wait 30s, restart web service |
| Credentials not synced from Blueprint | Verify in Render dashboard |

**Fix Steps:**
1. Go to Render Dashboard → bot-webhook → Environment
2. Verify `DB_USERNAME`, `DB_PASSWORD` show actual values (not "fromDatabase: bot-db")
3. If blank, check bot-db service is active
4. Restart web service

### **Issue 3: "Could not bind to 0.0.0.0:8000"**

```
Bind for 0.0.0.0:8000 failed: port already in use
```

| Cause | Fix |
|-------|-----|
| Port collision | Use $PORT environment variable (already done) |
| Old service not stopped | Restart service from dashboard |

### **Issue 4: Webhook not receiving Telegram updates**

```
No messages coming through, but bot responds to /start
```

| Cause | Fix |
|-------|-----|
| Webhook URL not registered | Run `scripts/set_webhook.py` |
| Wrong `PUBLIC_BASE_URL` | Update with actual Render URL |
| Old webhook still active in Telegram | Deregister old webhook first |

**Fix:**
```bash
export BOT_TOKEN="your-token"
export PUBLIC_BASE_URL="https://bot-webhook-xxxx.onrender.com"
python scripts/set_webhook.py
```

### **Issue 5: Worker not processing jobs**

```
Jobs enqueued but nothing happens, worker logs empty
```

| Cause | Fix |
|-------|-----|
| Redis not connected | Check `REDIS_URL` env var |
| Worker crashed | View logs, restart |
| Job queue corrupted | Redis eviction policy wrong |

**Check:**
1. Render Dashboard → bot-redis → check Status is "Active"
2. Render Dashboard → bot-worker → Logs (view last 50 lines)
3. Restart worker service

---

## 📊 Deployment Checklist

- [ ] Code changes committed and pushed to GitHub
- [ ] Render detected new commit (auto or manual deploy started)
- [ ] Docker build completed (5-10 min)
- [ ] All 4 services show green "Active" status:
  - bot-webhook (web)
  - bot-worker
  - bot-db
  - bot-redis
- [ ] Health check passes: `GET /health` → 200 OK
- [ ] `PUBLIC_BASE_URL` environment variable is set
- [ ] Webhook registered: `python scripts/set_webhook.py` runs successfully
- [ ] Test message sent to bot → Receives response
- [ ] Worker processes download job → Bot sends file back
- [ ] No errors in logs after 1 minute

---

## 🔐 Security Notes

### **Never commit secrets**
```bash
# ❌ BAD: Secrets in .env
git add .env
git commit "add env"

# ✅ GOOD: Secrets only in Render dashboard
# .env is in .gitignore
# Render injects at runtime
```

### **Environment variable isolation**
- Each Render service gets its own env vars
- web service: needs BOT_TOKEN, WEBHOOK_SECRET
- worker service: only needs BOT_TOKEN (no secrets needed)
- Database credentials: auto-injected from managed service

### **Render database security**
- Database is on private network (not public internet)
- Connections use TLS encryption
- Credentials auto-managed by Render
- Never expose `DB_PASSWORD` in logs

---

## 📈 Monitoring After Fix

### **What to watch in first 24 hours**

1. **Error rate** (should be < 5%)
   - Render Dashboard → Metrics
   - Graph shows request success rate

2. **Worker health**
   - Render Dashboard → bot-worker → Logs
   - Should see "Job processed" messages

3. **Database connections**
   - Render Dashboard → bot-db → Connections tab
   - Should see 2-5 active connections (normal)

4. **Redis memory**
   - Render Dashboard → bot-redis
   - Maxmemory policy is "noeviction" ✓

---

## 📞 Still Having Issues?

### **Debugging Steps**

1. **Check Service Status**
   ```
   Render Dashboard → Click each service
   Status should show: "Active" with green checkmark
   ```

2. **View Logs**
   ```
   Render Dashboard → Service → Logs
   Look for first ERROR after deploy
   ```

3. **Test Connectivity**
   - Local machine: `curl https://your-url/health`
   - If fails: service is down or DNS issue
   - If works: app is running

4. **Check Environment Variables**
   ```
   Render Dashboard → Service → Environment
   Verify all auto-linked vars have actual values, not "fromDatabase"
   ```

5. **Restart Services**
   - Most issues resolve with a restart
   - Render Dashboard → Service → "Restart"

---

## 📚 Additional Resources

- [Render Deployment Best Practices](https://render.com/docs/deploys)
- [Render Environment Variables](https://render.com/docs/environment-variables)
- [PostgreSQL Connection Troubleshooting](https://www.postgresql.org/docs/current/runtime-config-connection.html)
- [Docker Multi-stage Builds](https://docs.docker.com/build/building/multi-stage/)

---

**Deployment Fixed:** ✅ 2026-11-20  
**Issue:** .env baked into Docker image using localhost  
**Solution:** Remove .env from image, use Render's runtime env vars  
**Status:** Ready for production
