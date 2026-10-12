from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from .config import settings

engine = create_async_engine(settings().database_url, pool_size=3, max_overflow=2, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def session():
    async with Session() as db:
        async with db.begin():
            yield db
