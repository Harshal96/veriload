"""SQLite smoke scenario for database load testing."""

from __future__ import annotations

from veriload import VeriUser, task


class SQLiteSmokeUser(VeriUser):
    """Create, query, and clean up one persona-backed database row."""

    async def on_start(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS synthetic_users (
                persona_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                company TEXT NOT NULL,
                segment TEXT NOT NULL
            )
            """,
            name="DB create synthetic_users table",
        )
        await self.db.execute(
            """
            INSERT INTO synthetic_users (persona_id, email, company, segment)
            VALUES (?, ?, ?, ?)
            """,
            parameters=(
                self.persona.persona_id,
                self.persona.contact.email,
                self.persona.company.name,
                self.persona_segment,
            ),
            name="DB insert synthetic user",
        )
        self.state = {**self.state, "db_row_inserted": True}

    async def on_stop(self) -> None:
        if not self.state.get("db_row_inserted"):
            return
        await self.db.execute(
            "DELETE FROM synthetic_users WHERE persona_id = ?",
            parameters=(self.persona.persona_id,),
            name="DB delete synthetic user",
        )

    @task(weight=1)
    async def lookup_synthetic_user(self) -> None:
        result = await self.db.query(
            """
            SELECT persona_id, email, company
            FROM synthetic_users
            WHERE persona_id = ?
            """,
            parameters=(self.persona.persona_id,),
            name="DB select synthetic user",
        )
        expected = (
            self.persona.persona_id,
            self.persona.contact.email,
            self.persona.company.name,
        )
        if result.rows != (expected,):
            raise RuntimeError("Database row did not match the synthetic persona")
        self.stop()
