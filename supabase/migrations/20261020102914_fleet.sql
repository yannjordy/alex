-- ============================================================================
-- Alex Fleet — jumelage visible et consenti d'instances Alex (mère / fille)
-- Transport non fiable assumé : la sécurité est dans les signatures Ed25519
-- côté cerveau, PAS dans cette base. Toutes les tables sont écrites avec la
-- clé service role depuis les cerveaux — RLS désactivée pour l'instant
-- (note d'audit : activer RLS + policies avant toute exposition publique).
-- ============================================================================

-- Paires en attente d'approbation (fille → mère).
-- code_hash : sha256(code à 8 chiffres affiché à l'écran de la fille) —
-- le code en clair n'est JAMAIS stocké ; il prouve la présence physique.
CREATE TABLE IF NOT EXISTS pairings (
  id BIGSERIAL PRIMARY KEY,
  code_hash TEXT UNIQUE NOT NULL,          -- sha256 du code de jumelage
  pubkey TEXT,                             -- clé publique Ed25519 de la fille
  username TEXT,                           -- nom d'utilisateur de la fille
  device_name TEXT,                        -- hostname de la fille
  expires_at TIMESTAMPTZ,                  -- expiration (10 min)
  approved_cert TEXT,                      -- cert signé par la mère (JSON)
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pairings_expires ON pairings(expires_at);
CREATE INDEX IF NOT EXISTS idx_pairings_created ON pairings(created_at DESC);

-- Appareils jumelés (vue mère de la flotte).
CREATE TABLE IF NOT EXISTS devices (
  id UUID PRIMARY KEY,                     -- device_id local de la fille
  username TEXT,
  device_name TEXT,
  pubkey TEXT,                             -- clé publique Ed25519
  status TEXT DEFAULT 'active',            -- active | revoked
  last_seen TIMESTAMPTZ,                   -- heartbeat (~ toutes les 30 s)
  version TEXT,                            -- version de l'app (package.json)
  workspace TEXT,                          -- chemin du projet de la fille
  frozen BOOLEAN DEFAULT FALSE,            -- suspension en cours
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);

-- Commandes signées stop/start (mère → fille).
-- sig = base64(ed25519(mother_priv, canonical_json{device_id,seq,type})).
-- seq strictement croissant par appareil : rejette les rejeux.
CREATE TABLE IF NOT EXISTS commands (
  id BIGSERIAL PRIMARY KEY,
  device_id UUID,
  seq BIGINT NOT NULL,
  type TEXT NOT NULL,                      -- stop | start
  sig TEXT,                                -- signature Ed25519 base64
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_commands_device_seq ON commands(device_id, seq);

-- Journal d'audit chaîné (miroir des ~/.alex/fleet/audit.jsonl locaux).
-- hash = sha256(prev_hash + canonical_json(event)) — la fille vérifie la
-- chaîne ; un cloud compromis est détecté (cassure de la chaîne).
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY,
  device_id UUID,
  seq BIGINT NOT NULL,
  ts TIMESTAMPTZ,
  event JSONB,
  prev_hash TEXT,
  hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_device_seq ON audit_log(device_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(ts DESC);

-- Alertes affichées dans la console (info | warn | crit).
-- NB DoS documenté : un cloud compromis peut insérer de fausses alertes
-- (désinformation UI) — les alertes ne déclenchent AUCUNE action.
CREATE TABLE IF NOT EXISTS alerts (
  id BIGSERIAL PRIMARY KEY,
  device_id UUID,
  level TEXT,                              -- info | warn | crit
  message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_device ON alerts(device_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);

-- RLS : volontairement NON activée pour l'instant — les tables sont écrites
-- exclusivement avec la clé service role depuis les cerveaux (localhost).
-- À activer avant toute exposition réseau de l'API Supabase.
