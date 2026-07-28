-- PZ AI DAB ALL — Cloudflare relay: D1 schéma (metadata zakázek).
-- Aplikuj:  wrangler d1 execute pz_dab --remote --file=schema.sql
CREATE TABLE IF NOT EXISTS jobs (
  id         TEXT PRIMARY KEY,   -- 12hex
  status     TEXT NOT NULL,      -- uploading|pending|processing|review|approved|done|error
  created_at TEXT,
  updated_at TEXT,
  data       TEXT NOT NULL       -- celý job jako JSON (stejný tvar jako Forpsi relay)
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
