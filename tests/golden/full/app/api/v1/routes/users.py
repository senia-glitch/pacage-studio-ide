from core import run as execute
from fastapi import APIRouter

router = APIRouter(tags=["users"])

SCENARIO = "create_user"


# studio: description start
# Создаёт пользователя.
# studio: description end
# studio: domain start
# users
# studio: domain end
@router.post("/create")
@router.get("/list")
async def create_user():
    await _helper()


async def _helper():
    await execute(SCENARIO)


@router.api_route("/both", methods=["get", "post"])
async def both():
    return 1
