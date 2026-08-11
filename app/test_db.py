from sqlalchemy import text
from sqlalchemy import create_engine

from app.core.config import settings

engine = create_engine(settings.DATABASE_URL)

with engine.connect() as conn:
    print("Connected to:", conn.execute(text("SELECT inet_server_addr(), inet_server_port()")).fetchone())
    print("DB:", conn.execute(text("SELECT current_database()")).fetchone())

if __name__ == '__main__':
    print("Hello")