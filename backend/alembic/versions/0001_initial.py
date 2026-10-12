"""Initial normalized task manager schema."""
from alembic import op
from app.schema_v1 import Base

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    Base.metadata.drop_all(op.get_bind())
