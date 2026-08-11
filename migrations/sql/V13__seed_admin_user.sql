-- V16: Seed default admin user (password: admin)
INSERT INTO users (username, email, full_name, password_hash, role, is_active, avatar_color)
VALUES (
    'admin',
    'admin@unidocverse.local',
    'Administrator',
    '$2b$12$QfJ879kI8NmXVm8g5ajQFuvVJd2T2HYm3nh/v4.o6I13dxoig1bE6',
    'admin',
    TRUE,
    '#6366f1'
)
ON CONFLICT (username) DO NOTHING;
