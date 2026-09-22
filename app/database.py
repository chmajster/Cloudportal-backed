from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    url = settings().database_url
    return create_engine(url, pool_pre_ping=True,
                         connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})


def session():
    from app.resource_scope.database import ScopedSession
    return sessionmaker(engine(), class_=ScopedSession, expire_on_commit=False)()


def get_db():
    with session() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
