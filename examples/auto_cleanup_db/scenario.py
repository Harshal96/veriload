"""Raw DB insert auto-cleanup scenario.

The scenario intentionally has no manual `on_stop()` cleanup. The paired YAML
files show delete and rollback cleanup strategies for the same INSERT traffic.
"""

from __future__ import annotations

from veriload import VeriUser, task


class RawDatabaseAutoCleanupUser(VeriUser):
    """Insert one persona-backed row and let auto-cleanup remove it."""

    async def on_start(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS cleanup_orders (
                email TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                segment TEXT NOT NULL
            )
            """,
            name="DB create cleanup_orders table",
        )

    @task(weight=1)
    async def insert_and_verify_order(self) -> None:
        await self.db.execute(
            """
            INSERT INTO cleanup_orders (email, persona_id, segment)
            VALUES (?, ?, ?)
            """,
            parameters=(
                self.persona.contact.email,
                self.persona.persona_id,
                self.persona_segment,
            ),
            name="DB insert cleanup order",
        )
        result = await self.db.query(
            """
            SELECT email, persona_id, segment
            FROM cleanup_orders
            WHERE email = ?
            """,
            parameters=(self.persona.contact.email,),
            name="DB select cleanup order",
        )
        if not result.rows:
            raise RuntimeError("Inserted cleanup order was not visible to the user connection")
        self.stop()
