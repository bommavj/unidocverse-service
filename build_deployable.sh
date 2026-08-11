#!/bin/bash
set -e

echo "🚀 Building UniDocVerse deployable bundle..."

# Paths
BACKEND_DIR="$(pwd)"
FRONTEND_DIR="$(dirname "$BACKEND_DIR")/unidocverse-dashboard"

echo "📁 Backend directory: $BACKEND_DIR"
echo "📁 Frontend directory: $FRONTEND_DIR"

# 1. Clean old deploy folder
rm -rf deploy
mkdir -p deploy/backend deploy/frontend deploy/postgres deploy/migrations deploy/supervisor

# 2. Copy backend
echo "📦 Copying backend..."
cp -r app deploy/backend/
cp requirements.txt deploy/backend/

echo "🐍 Creating backend venv..."
python3 -m venv deploy/backend/.venv
source deploy/backend/.venv/bin/activate
pip install -r deploy/backend/requirements.txt
deactivate

# 3. Copy frontend build
echo "🖼️ Copying frontend dist..."

# Angular dist is nested: dist/<project-name>/*
DIST_PATH="$FRONTEND_DIR/dist"

if [ ! -d "$DIST_PATH" ]; then
    echo "❌ ERROR: Frontend dist folder not found at: $DIST_PATH"
    echo "➡️ Run 'npm run build' inside unidocverse-dashboard first."
    exit 1
fi

# Create destination
mkdir -p deploy/frontend/dist

# Copy nested dist folder automatically
cp -r "$DIST_PATH"/*/* deploy/frontend/dist/

echo "✅ Frontend copied from: $DIST_PATH"

# 4. Copy PostgreSQL runtime
echo "🐘 Copying PostgreSQL binaries..."
cp -r bin/postgres/bin deploy/postgres/
cp -r bin/postgres/data deploy/postgres/
cp bin/initdb.sh deploy/postgres/initdb.sh
cp bin/start_database.sh deploy/postgres/start_postgres.sh
chmod +x deploy/postgres/*.sh

# 5. Copy migrations
echo "📜 Copying Flyway migrations..."
# Create required directories
mkdir -p deploy/migrations/sql
mkdir -p deploy/migrations/flyway
# Copy SQL migrations
cp -r migrations/*.sql deploy/migrations/sql/
# Copy Flyway runner script
cp bin/flyway.sh deploy/migrations/flyway/
chmod +x deploy/migrations/flyway/flyway.sh

# 6. Create backend runner
echo "🧠 Creating backend runner..."
cat << 'EOF' > deploy/backend/run_backend.sh
#!/bin/bash
source .venv/bin/activate
python app/main.py
deactivate
EOF
chmod +x deploy/backend/run_backend.sh

# 7. Create supervisor script
echo "🧩 Creating supervisor script..."
cat << 'EOF' > deploy/supervisor/start_all.sh
#!/bin/bash
set -e

echo "🔄 Starting PostgreSQL..."
./postgres/start_postgres.sh &

sleep 3

echo "📜 Running migrations..."
./migrations/flyway.sh

echo "🧠 Starting backend..."
./backend/run_backend.sh &

echo "🖼️ Starting frontend..."
python3 -m http.server --directory frontend/dist 8080 &
EOF
chmod +x deploy/supervisor/start_all.sh

# 8. Create root launcher
echo "🎯 Creating root launcher..."
cat << 'EOF' > deploy/run.sh
#!/bin/bash
./supervisor/start_all.sh
EOF
chmod +x deploy/run.sh

echo "✅ UniDocVerse deployable bundle ready at ./deploy"
