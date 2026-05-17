"""Smoke scenario for https://httpbin.org."""

from veriload import VeriUser, task


class HttpBinSmokeUser(VeriUser):
    """Send one persona-backed request to httpbin."""

    @task(weight=1)
    async def get_with_persona_query(self) -> None:
        params = self.payload(
            {
                "email": "contact.email",
                "username": "person.username",
                "company": "company.name",
                "industry": "job.industry",
            }
        )
        await self.http.get("/get", name="GET /get", params=params)
        self.stop()
