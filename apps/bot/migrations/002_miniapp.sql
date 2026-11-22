-- Mini App: show titles in the download history and record where a request came from.
ALTER TABLE requests ADD COLUMN IF NOT EXISTS title  TEXT;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'chat';
