ALTER TABLE user_operations
    ALTER COLUMN created_at SET DEFAULT clock_timestamp();
