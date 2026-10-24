from core import run as execute
from fastapi import APIRouter

router = APIRouter(tags=["users"])

SCENARIO = "create_user"


@router.post("/create")
@router.get("/list")
async def create_user():
    """Создаёт пользователя."""
    await _helper()


async def _helper():
    await execute(SCENARIO)


@router.api_route("/both", methods=["get", "post"])
async def both():
    return 1
