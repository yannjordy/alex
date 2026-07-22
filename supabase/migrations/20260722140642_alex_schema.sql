-- Alex Assistant Database Schema
-- Tous droits réservés

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
  id BIGSERIAL PRIMARY KEY,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  source TEXT,
  model TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at DESC);

-- Mémoires (souvenirs qu'Alex garde de l'utilisateur)
CREATE TABLE IF NOT EXISTS memories (
  id BIGSERIAL PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  value TEXT NOT NULL,
  category TEXT DEFAULT 'general',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Configuration
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Base de connaissance personnalisée
CREATE TABLE IF NOT EXISTS knowledge (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT[] DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_tags ON knowledge USING GIN(tags);

-- Journal d'utilisation des outils
CREATE TABLE IF NOT EXISTS tool_logs (
  id BIGSERIAL PRIMARY KEY,
  tool_name TEXT NOT NULL,
  params JSONB,
  result TEXT,
  success BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Row Level Security
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_logs ENABLE ROW LEVEL SECURITY;
