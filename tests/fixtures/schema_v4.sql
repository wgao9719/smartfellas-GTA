-- Frozen real v4 database produced by Repository at f84de44.

-- Includes durable memories, relationship facts, and active/ended sessions.

PRAGMA foreign_keys=ON;

BEGIN IMMEDIATE;

CREATE TABLE profiles (
          profile_id TEXT PRIMARY KEY, save_slot TEXT NOT NULL, settings TEXT NOT NULL,
          created_at TEXT NOT NULL, active_branch TEXT
        );

CREATE TABLE branches (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          branch_id TEXT NOT NULL, parent_id TEXT, fork_time TEXT NOT NULL,
          latest_time TEXT NOT NULL, PRIMARY KEY(profile_id,branch_id)
        );

CREATE TABLE game_sessions (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          session_id TEXT NOT NULL, branch_id TEXT NOT NULL, started_at TEXT NOT NULL,
          ended_at TEXT, PRIMARY KEY(profile_id,session_id)
        );

CREATE TABLE actors (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          actor_id TEXT NOT NULL, actor_kind TEXT NOT NULL, session_id TEXT,
          data TEXT NOT NULL, PRIMARY KEY(profile_id,actor_id)
        );

CREATE TABLE actor_instances (
          profile_id TEXT NOT NULL, session_id TEXT NOT NULL, handle INTEGER NOT NULL,
          generation INTEGER NOT NULL, actor_id TEXT NOT NULL, confidence REAL NOT NULL,
          status TEXT NOT NULL, PRIMARY KEY(profile_id,session_id,handle,generation),
          FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE
        );

CREATE TABLE events (
          profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          event_id TEXT NOT NULL, branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL,
          protagonist_id TEXT, data TEXT NOT NULL, PRIMARY KEY(profile_id,event_id),
          FOREIGN KEY(profile_id,branch_id) REFERENCES branches
        );

CREATE TABLE memories (
          memory_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, actor_id TEXT NOT NULL,
          protagonist_id TEXT, branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL,
          text TEXT NOT NULL, provenance TEXT NOT NULL, importance REAL NOT NULL,
          confidence REAL NOT NULL, visibility_scope TEXT NOT NULL, session_id TEXT,
          expires_at TEXT, decay_days REAL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE,
          FOREIGN KEY(profile_id,branch_id) REFERENCES branches
        );

CREATE INDEX memory_actor ON memories(profile_id,actor_id,game_datetime);

CREATE VIRTUAL TABLE memory_fts USING fts5(text, content='memories',
          content_rowid='rowid', tokenize='porter unicode61');

CREATE TRIGGER memory_insert AFTER INSERT ON memories BEGIN
          INSERT INTO memory_fts(rowid,text) VALUES(new.rowid,new.text);
        END;

CREATE TRIGGER memory_delete AFTER DELETE ON memories BEGIN
          INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.rowid,old.text);
        END;

CREATE TRIGGER memory_update AFTER UPDATE OF text ON memories BEGIN
          INSERT INTO memory_fts(memory_fts,rowid,text) VALUES('delete',old.rowid,old.text);
          INSERT INTO memory_fts(rowid,text) VALUES(new.rowid,new.text);
        END;

CREATE TABLE memory_sources (
          memory_id TEXT NOT NULL REFERENCES memories ON DELETE CASCADE,
          profile_id TEXT NOT NULL, event_id TEXT NOT NULL,
          PRIMARY KEY(memory_id,event_id),
          FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
        );

CREATE TABLE memory_vectors (
          memory_id TEXT PRIMARY KEY REFERENCES memories ON DELETE CASCADE,
          model TEXT NOT NULL, vector BLOB NOT NULL
        );

CREATE TABLE summary_sources (
          summary_id TEXT NOT NULL REFERENCES memories ON DELETE CASCADE,
          memory_id TEXT NOT NULL REFERENCES memories ON DELETE CASCADE,
          PRIMARY KEY(summary_id,memory_id)
        );

CREATE TABLE relationship_updates (
          profile_id TEXT NOT NULL, actor_id TEXT NOT NULL, protagonist_id TEXT NOT NULL,
          branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL, event_id TEXT NOT NULL,
          reason TEXT NOT NULL, deltas TEXT NOT NULL, session_id TEXT,
          PRIMARY KEY(profile_id,actor_id,protagonist_id,event_id),
          FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE,
          FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
        );

CREATE TABLE knowledge_cards (
          card_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
          owner_id TEXT, scope TEXT NOT NULL, scope_id TEXT, protagonist_id TEXT,
          branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL, expires_at TEXT,
          text TEXT NOT NULL, provenance TEXT NOT NULL, confidence REAL NOT NULL,
          session_id TEXT, FOREIGN KEY(profile_id,branch_id) REFERENCES branches,
          FOREIGN KEY(profile_id,owner_id) REFERENCES actors ON DELETE CASCADE
        );

CREATE TABLE knowledge_sources (
          card_id TEXT NOT NULL REFERENCES knowledge_cards ON DELETE CASCADE,
          profile_id TEXT NOT NULL, event_id TEXT NOT NULL,
          PRIMARY KEY(card_id,event_id),
          FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
        );

CREATE TABLE scrub_audit (
          audit_id INTEGER PRIMARY KEY, profile_id TEXT NOT NULL,
          operation TEXT NOT NULL, object_id TEXT NOT NULL, created_at TEXT NOT NULL
        );

CREATE TABLE ai_requests (
              request_id TEXT PRIMARY KEY, day TEXT NOT NULL, budget_class TEXT NOT NULL,
              provider TEXT NOT NULL, model TEXT NOT NULL, reserved_micro_usd INTEGER NOT NULL,
              charged_micro_usd INTEGER NOT NULL, state TEXT NOT NULL,
              input_tokens INTEGER, output_tokens INTEGER, price_json TEXT,
              created_at TEXT NOT NULL
            );

CREATE INDEX ai_requests_day_class ON ai_requests(day,budget_class);

CREATE TABLE service_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE skill_versions (
              profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
              skill_id TEXT NOT NULL, version INTEGER NOT NULL, source TEXT NOT NULL,
              fingerprint TEXT NOT NULL, registry_fingerprint TEXT NOT NULL,
              dependencies TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY(profile_id,skill_id,version)
            );

CREATE TABLE skill_records (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL,
              record_id TEXT NOT NULL, actor_id TEXT NOT NULL, protagonist_id TEXT NOT NULL,
              branch_id TEXT NOT NULL, game_datetime TEXT NOT NULL,
              session_id TEXT NOT NULL, runtime_generation INTEGER NOT NULL,
              runtime_instance_id TEXT NOT NULL, kind TEXT NOT NULL,
              skill_id TEXT NOT NULL, version INTEGER NOT NULL,
              fingerprint TEXT NOT NULL, parent_id TEXT, data TEXT NOT NULL,
              UNIQUE(profile_id,record_id),
              FOREIGN KEY(profile_id,actor_id) REFERENCES actors ON DELETE CASCADE,
              FOREIGN KEY(profile_id,branch_id) REFERENCES branches,
              FOREIGN KEY(profile_id,skill_id,version) REFERENCES skill_versions,
              FOREIGN KEY(profile_id,parent_id) REFERENCES skill_records(profile_id,record_id)
                ON DELETE CASCADE
            );

CREATE INDEX skill_actor_history
              ON skill_records(profile_id,actor_id,protagonist_id,game_datetime);

CREATE TABLE skill_record_sources (
              profile_id TEXT NOT NULL, record_id TEXT NOT NULL, event_id TEXT NOT NULL,
              PRIMARY KEY(profile_id,record_id,event_id),
              FOREIGN KEY(profile_id,record_id) REFERENCES skill_records(profile_id,record_id)
                ON DELETE CASCADE,
              FOREIGN KEY(profile_id,event_id) REFERENCES events ON DELETE CASCADE
            );

CREATE INDEX scrub_lookup ON scrub_audit(profile_id,operation,object_id);

CREATE INDEX memory_social_window
              ON memories(profile_id,actor_id,protagonist_id,provenance,game_datetime)
              WHERE provenance='system_fact' AND substr(text,1,11)='I chose to '
              OR provenance='reported' AND instr(text,' told me: ')>1;

CREATE INDEX memory_source_event ON memory_sources(event_id,profile_id);

CREATE TABLE memory_derivations (
              profile_id TEXT NOT NULL REFERENCES profiles ON DELETE CASCADE,
              actor_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
              derived_event_id TEXT NOT NULL,
              PRIMARY KEY(profile_id,actor_id,source_event_id,derived_event_id)
            );

INSERT INTO profiles VALUES('save-a','','{}','2026-10-11T07:38:18.582642+00:00','884fea8c-8ce9-4b39-9e28-55af06b1d336');

INSERT INTO branches VALUES('save-a','884fea8c-8ce9-4b39-9e28-55af06b1d336',NULL,'2025-01-01T09:00:00.000000+00:00','2025-01-01T10:00:00.000000+00:00');

INSERT INTO game_sessions VALUES('save-a','active','884fea8c-8ce9-4b39-9e28-55af06b1d336','2025-01-01T09:00:00.000000+00:00',NULL);

INSERT INTO game_sessions VALUES('save-a','ended','884fea8c-8ce9-4b39-9e28-55af06b1d336','2025-01-01T10:00:00.000000+00:00','2026-10-11T07:38:18.582642+00:00');

INSERT INTO actors VALUES('save-a','avery','curated_persistent',NULL,'{"actor_id":"avery","actor_kind":"curated_persistent","display_name":"Avery","id":"avery","identity_confidence":1.0,"profile_id":"save-a","session_id":null}');

INSERT INTO events VALUES('save-a','event-1','884fea8c-8ce9-4b39-9e28-55af06b1d336','2025-01-01T10:00:00.000000+00:00','player:franklin','{"confidence":0.9,"event_id":"event-1","event_type":"conversation_completed","game_datetime":"2025-01-01T10:00:00.000000+00:00","observer_ids":["avery"],"payload":{"text":"We discussed the broken bicycle."},"protagonist_id":"player:franklin","provenance":"directly_observed"}');

INSERT INTO memories VALUES('9581a0a3-d4c4-4d53-8011-cb85cab782c5','save-a','avery','player:franklin','884fea8c-8ce9-4b39-9e28-55af06b1d336','2025-01-01T10:00:00.000000+00:00','I discussed the broken bicycle with Franklin.','directly_observed',0.5,0.9,'private',NULL,NULL,NULL,'de34a003c16b9226357eeb02e76a01a103948290798d610bd315cc08a343d1f0','2026-10-11T07:38:18.582642+00:00');

INSERT INTO memory_sources VALUES('9581a0a3-d4c4-4d53-8011-cb85cab782c5','save-a','event-1');

INSERT INTO relationship_updates VALUES('save-a','avery','player:franklin','884fea8c-8ce9-4b39-9e28-55af06b1d336','2025-01-01T10:00:00.000000+00:00','event-1','conversation_completed','{"familiarity":0.02}',NULL);

INSERT INTO knowledge_cards VALUES('18c6a46f-246f-473d-9806-52d5f219f8e5','save-a','avery','actor',NULL,'player:franklin','884fea8c-8ce9-4b39-9e28-55af06b1d336','2025-01-01T10:00:00.000000+00:00',NULL,'Bicycle conversation','directly_observed',0.9,NULL);

INSERT INTO knowledge_sources VALUES('18c6a46f-246f-473d-9806-52d5f219f8e5','save-a','event-1');

PRAGMA user_version=4;

COMMIT;
