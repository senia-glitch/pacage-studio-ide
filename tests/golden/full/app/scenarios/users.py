from core import BaseScenario, register_scenario, run


@register_scenario("create_user")
class CreateUserScenario(BaseScenario):
    async def execute(self, dto):
        await self._load()

    async def _load(self):
        # studio: description start
        # Читает пользователей.
        # studio: description end
        await self._db.query(
            "SELECT * FROM users u JOIN orders o ON o.id = u.order_id"
        )
        await run("audit")


@register_scenario("audit")
class AuditScenario(BaseScenario):
    async def execute(self):
        await self._db.create("orders", {})
