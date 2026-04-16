import os
import uuid
from datetime import date, datetime
from sqlalchemy import create_engine, Column, String, Integer, Date, DateTime, Boolean, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./leads.db")

# Render provides postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=True)   # null for Google-only accounts
    google_id = Column(String, nullable=True, unique=True)
    tier = Column(String, default="free")           # free | starter | pro | business | unlimited
    scans_used = Column(Integer, default=0)         # free tier: counts scans
    leads_used = Column(Integer, default=0)         # paid tiers: counts leads returned
    usage_reset = Column(Date, default=date.today)  # reset on 1st of each month
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    reset_token = Column(String, nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)
    # Add new columns to existing tables if they don't exist (SQLite migration)
    with engine.connect() as conn:
        for col, typedef in [
            ("reset_token", "VARCHAR"),
            ("reset_token_expires", "DATETIME"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
                conn.commit()
            except Exception:
                pass  # column already exists


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
