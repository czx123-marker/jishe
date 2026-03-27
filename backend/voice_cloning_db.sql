-- Voice Cloning Database Schema
CREATE TABLE IF NOT EXISTS user_voice_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    sample_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_size INTEGER,
    upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    duration_seconds REAL,
    is_active BOOLEAN DEFAULT 1
);

-- Index for faster queries by user
CREATE INDEX IF NOT EXISTS idx_user_voice_samples ON user_voice_samples(user_id, is_active);

-- Table to store voice cloning jobs
CREATE TABLE IF NOT EXISTS voice_cloning_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    target_voice_sample_id INTEGER,
    output_file_path TEXT,
    status TEXT DEFAULT 'pending', -- pending, processing, completed, failed
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    FOREIGN KEY (target_voice_sample_id) REFERENCES user_voice_samples(id)
);