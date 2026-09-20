from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import database_url


def create_session_factory() -> sessionmaker[Session]:
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True,
                           connect_args={"connect_timeout": 5})
    return sessionmaker(engine, expire_on_commit=False)
