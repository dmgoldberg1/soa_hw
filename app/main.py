from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.contract import SPEC
from app.errors import ApiError, error_response
from app.generated.routes import router


app = FastAPI(title=SPEC["info"]["title"])
app.include_router(router)
app.openapi = lambda: SPEC


@app.exception_handler(ApiError)
async def business_error(request, error):
    return error_response(error)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    violations = [
        {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
        for e in error.errors()
    ]
    return error_response(
        ApiError(
            400,
            "VALIDATION_ERROR",
            "Некорректные входные данные",
            {"violations": violations},
        )
    )


@app.exception_handler(HTTPException)
async def http_error(request, error):
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
        error.status_code, "VALIDATION_ERROR"
    )
    return error_response(ApiError(error.status_code, code, str(error.detail)))


@app.exception_handler(Exception)
async def unexpected_error(request, error):
    return error_response(ApiError(500, "INTERNAL_ERROR", "Внутренняя ошибка сервера"))
