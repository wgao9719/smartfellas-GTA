-- Unchanged v4 to v5 migration from b9ecd01 (session cleanup).
BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS session_cleanup_pending (
              profile_id TEXT NOT NULL, session_id TEXT NOT NULL,
              PRIMARY KEY(profile_id,session_id),
              FOREIGN KEY(profile_id,session_id) REFERENCES game_sessions ON DELETE CASCADE
            );
            PRAGMA user_version=5;
            COMMIT;
