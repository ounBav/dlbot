CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username    TEXT,
    language    TEXT NOT NULL DEFAULT 'en',
    is_banned   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS requests (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id  BIGINT NOT NULL REFERENCES users (telegram_id),
    url          TEXT NOT NULL,
    url_hash     TEXT NOT NULL,
    platform     TEXT NOT NULL,
    format       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'received',
    error_code   TEXT,
    file_size    BIGINT,
    duration_ms  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_requests_user_created ON requests (telegram_id, created_at);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests (created_at);

CREATE TABLE IF NOT EXISTS media_cache (
    url_hash         TEXT NOT NULL,
    format           TEXT NOT NULL,
    telegram_file_id TEXT NOT NULL,
    title            TEXT,
    size             BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (url_hash, format)
);

CREATE TABLE IF NOT EXISTS admin_audit (
    id          BIGSERIAL PRIMARY KEY,
    admin_id    BIGINT NOT NULL,
    action      TEXT NOT NULL,
    target_id   BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
