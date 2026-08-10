"""merge platform site directory and web sync scheduling heads"""

from collections.abc import Sequence

revision: str = "e5f6a7b8c9d0"
down_revision: tuple[str, str] = ("a1b2c3d4e5f6", "d4e5f6a7b8c9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
