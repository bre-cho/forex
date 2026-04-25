-- Forex Platform — Initial Seed Data
-- Run: psql "$DATABASE_URL" -f db/seeds/001_initial_seed.sql
--
-- WARNING: FOR DEVELOPMENT/TESTING ONLY. NEVER RUN IN PRODUCTION.
-- The admin password below is a known bcrypt hash. Change or remove this
-- account immediately after any non-local deployment.

-- Sample admin user (password: Admin1234!)
INSERT INTO users (id, email, hashed_password, full_name, is_active, is_superuser, email_verified)
VALUES (
    'usr_admin_001',
    'admin@forexplatform.dev',
    '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', -- Admin1234!
    'Admin User',
    true,
    true,
    true
) ON CONFLICT (email) DO NOTHING;

-- Sample workspace
INSERT INTO workspaces (id, name, slug, owner_id, plan)
VALUES (
    'ws_demo_001',
    'Demo Workspace',
    'demo',
    'usr_admin_001',
    'pro'
) ON CONFLICT DO NOTHING;

-- Workspace owner membership
INSERT INTO workspace_members (id, workspace_id, user_id, role)
VALUES (
    'wm_demo_001',
    'ws_demo_001',
    'usr_admin_001',
    'owner'
) ON CONFLICT DO NOTHING;

-- Sample strategy
INSERT INTO strategies (id, workspace_id, name, description, is_public, config)
VALUES (
    'stg_demo_001',
    'ws_demo_001',
    'Wave + AI Demo Strategy',
    'Elliott Wave + LLM signal confirmation demo strategy',
    true,
    '{"wave_mode": "auto", "ai_enabled": false, "risk_pct": 1.0}'::jsonb
) ON CONFLICT DO NOTHING;

-- Sample bot instance
INSERT INTO bot_instances (id, workspace_id, strategy_id, name, symbol, timeframe, mode, status)
VALUES (
    'bot_demo_001',
    'ws_demo_001',
    'stg_demo_001',
    'Demo EURUSD Bot',
    'EURUSD',
    'M5',
    'paper',
    'stopped'
) ON CONFLICT DO NOTHING;

-- Bot config
INSERT INTO bot_instance_configs (id, bot_instance_id, risk_json, strategy_config, ai_json)
VALUES (
    'cfg_demo_001',
    'bot_demo_001',
    '{"risk_pct": 1.0, "max_drawdown_pct": 10.0, "max_daily_loss_pct": 3.0}'::jsonb,
    '{"wave_mode": "auto", "min_confidence": 0.65}'::jsonb,
    '{"enabled": false}'::jsonb
) ON CONFLICT DO NOTHING;

-- Free subscription for admin
INSERT INTO subscriptions (id, user_id, plan, status)
VALUES (
    'sub_admin_001',
    'usr_admin_001',
    'pro',
    'active'
) ON CONFLICT DO NOTHING;
