import os

# app.main reads Settings at import time (CORS origins). CI has no .env, so give it a dummy token.
os.environ.setdefault("BOT_TOKEN", "1:test-token")
