import logging
from sqlalchemy import String, cast, create_engine, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria
from app.core.config import settings

logger = logging.getLogger(__name__)

# Database engine
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"client_encoding": "utf8"},
    pool_size=20,        # base pool — handles 20 concurrent requests
    max_overflow=40,     # burst up to 60 total connections
    pool_timeout=60,     # wait up to 60s for a free connection before error
    pool_recycle=1800,   # recycle connections every 30min (prevents stale connections)
    pool_pre_ping=True,  # test connection health before using — prevents "connection closed" errors
    echo=False,
)

_scope_role_exists = None


@event.listens_for(engine, "before_cursor_execute")
def _scope_connection_to_tenant(conn, cursor, statement, parameters, context, executemany):
    # Keep the connection's app.current_tenant_id equal to the current organisation so the RLS
    # policies (V73) hide other organisations' rows. Superusers skip RLS, so scoped connections also
    # SET ROLE udv_tenant_scope (no superuser, owner privileges). Set at session level and tracked
    # per pooled connection, so it costs a round trip only when the organisation changes.
    global _scope_role_exists
    from app.core.tenant import get_db_tenant_id
    tid = get_db_tenant_id() or ""
    if conn.info.get("udv_tenant") == tid:
        return
    cur = cursor.connection.cursor()
    try:
        if _scope_role_exists is None:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'udv_tenant_scope'")
            _scope_role_exists = cur.fetchone() is not None
            if not _scope_role_exists:
                logger.warning("Role udv_tenant_scope missing (migration V73 not applied): "
                               "organisation isolation relies on each query's own filter")
        if _scope_role_exists:
            cur.execute("SET ROLE udv_tenant_scope" if tid else "RESET ROLE")
        cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (tid,))
    finally:
        cur.close()
    conn.info["udv_tenant"] = tid


# A rolled-back transaction also undoes set_config, so forget what was set and set it again.
@event.listens_for(engine, "rollback")
@event.listens_for(engine, "rollback_savepoint")
def _forget_tenant_on_rollback(conn, *args):
    conn.info.pop("udv_tenant", None)


@event.listens_for(engine.pool, "reset")
def _forget_tenant_on_reset(dbapi_connection, connection_record, reset_state):
    connection_record.info.pop("udv_tenant", None)


@event.listens_for(Session, "do_orm_execute")
def _scope_orm_to_tenant(state):
    # Second layer beside RLS: ORM selects/updates/deletes made for a signed-in organisation only
    # touch rows of models that carry tenant_id and belong to it. Cast because the column is
    # mapped as String while the database type is uuid on some tables and text on others.
    from app.core.tenant import get_db_tenant_id
    tid = get_db_tenant_id()
    if not tid or state.is_column_load or state.is_relationship_load:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    for mapper in state.all_mappers:
        cls = mapper.class_
        if "tenant_id" in mapper.columns:
            state.statement = state.statement.options(with_loader_criteria(
                cls, lambda c: cast(c.tenant_id, String) == tid, include_aliases=True))


# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class for models
Base = declarative_base()


def get_db():
    """
    Database dependency for FastAPI

    Usage:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_connection():
    """
    Test database connection

    Raises:
        Exception if connection fails
    """
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            result.fetchone()
        logger.info("✅ PostgreSQL connection successful")
        return True
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


def init_db():
    """
    Initialize database (create all tables)

    This should be called on startup
    """
    try:
        # Import all models here to ensure they're registered
        from app.models.document import Document
        from app.models.personal import Note, PlannerTask
        from app.models import Base as ModelsBase

        # Create all tables using the models Base
        ModelsBase.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created/verified via ModelsBase")

        # Ensure settings table exists before altering it
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS settings (
                    key        VARCHAR(120) PRIMARY KEY,
                    value      TEXT         NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    agency_mode       BOOLEAN NOT NULL DEFAULT FALSE,
                    legal_mode        BOOLEAN NOT NULL DEFAULT FALSE,
                    medical_mode      BOOLEAN NOT NULL DEFAULT FALSE,
                    finance_mode      BOOLEAN NOT NULL DEFAULT FALSE,
                    real_estate_mode  BOOLEAN NOT NULL DEFAULT FALSE,
                    hr_mode           BOOLEAN NOT NULL DEFAULT FALSE,
                    supply_chain_mode BOOLEAN NOT NULL DEFAULT FALSE
                )
            """))

        # Upgrade settings table columns for feature flags
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS legal_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS medical_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS finance_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS real_estate_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS hr_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS supply_chain_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS compliance_mode BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.execute(text("ALTER TABLE settings ADD COLUMN IF NOT EXISTS operations_mode BOOLEAN NOT NULL DEFAULT FALSE"))

            # Multi-domain tables
            conn.execute(text("CREATE TABLE IF NOT EXISTS legal_clauses (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, clause_type VARCHAR(100), clause_name VARCHAR(100), clause_text TEXT, is_deviation BOOLEAN NOT NULL DEFAULT FALSE, governing_law VARCHAR(100), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS legal_contracts (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, contract_name VARCHAR(200), counterparty VARCHAR(200), notice_period VARCHAR(100), expiry_date DATE, risk_rating VARCHAR(50), status VARCHAR(50), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            
            conn.execute(text("CREATE TABLE IF NOT EXISTS patient_charts (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, patient_name VARCHAR(200), patient_dob DATE, chart_score INT, consent_signed BOOLEAN NOT NULL DEFAULT FALSE, insurance_verified BOOLEAN NOT NULL DEFAULT FALSE, history_filed BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS patient_biomarkers (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, biomarker_name VARCHAR(100), biomarker_value FLOAT, unit VARCHAR(50), reference_range VARCHAR(100), is_abnormal BOOLEAN NOT NULL DEFAULT FALSE, physician VARCHAR(200), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            
            conn.execute(text("CREATE TABLE IF NOT EXISTS finance_matches (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, invoice_id VARCHAR(100), vendor_name VARCHAR(200), invoice_val NUMERIC(12, 2), po_val NUMERIC(12, 2), receipt_val NUMERIC(12, 2), match_status VARCHAR(50), is_approved BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS finance_anomalies (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, anomaly_type VARCHAR(100), title VARCHAR(200), ref_code VARCHAR(100), amount NUMERIC(12, 2), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            
            conn.execute(text("CREATE TABLE IF NOT EXISTS real_estate_properties (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, address VARCHAR(255), buyer_name VARCHAR(200), price NUMERIC(12, 2), progress INT, has_appraisal BOOLEAN NOT NULL DEFAULT FALSE, has_clear_title BOOLEAN NOT NULL DEFAULT FALSE, is_escrow_funded BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS real_estate_milestones (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), property_id UUID, name VARCHAR(200), due_date DATE, is_completed BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            
            conn.execute(text("CREATE TABLE IF NOT EXISTS hr_candidates (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, candidate_name VARCHAR(200), role VARCHAR(200), experience_years INT, match_score INT, skills TEXT[], feedback TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS hr_onboarding_tasks (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, title VARCHAR(200), employee_name VARCHAR(200), due_date DATE, is_done BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            
            conn.execute(text("CREATE TABLE IF NOT EXISTS supply_chain_shipments (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, bol_number VARCHAR(100), carrier_name VARCHAR(200), origin VARCHAR(200), destination VARCHAR(200), customs_status VARCHAR(50), is_reconciled BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS supply_chain_suppliers (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id VARCHAR REFERENCES documents(id) ON DELETE CASCADE, name VARCHAR(200), type VARCHAR(200), expiry_date DATE, sanctions_status VARCHAR(50), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))

            # Generic key-value settings store (used by integrations, etc.)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key   VARCHAR(120) PRIMARY KEY,
                    value TEXT         NOT NULL DEFAULT '{}'
                )
            """))

            # Chatbot session isolation (one session per bank customer)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS chatbot_sessions (
                    token           VARCHAR(64)  PRIMARY KEY,
                    doc_ids         JSONB        NOT NULL DEFAULT '[]',
                    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    last_active_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    expires_at      TIMESTAMPTZ  NOT NULL DEFAULT (NOW() + INTERVAL '2 hours')
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_chatbot_sessions_expires
                ON chatbot_sessions (expires_at)
            """))
        logger.info("✅ Settings table columns checked/created")

    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise