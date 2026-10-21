from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def error_response(error):
    from app.generated.models import ApiError as ErrorDTO

    body = ErrorDTO(error_code=error.code, message=error.message, details=error.details)
    return JSONResponse(
        status_code=error.status,
        content=body.model_dump(mode="json", exclude_none=True),
    )


def invalid(field, message):
    raise ApiError(
        400,
        "VALIDATION_ERROR",
        "Некорректные входные данные",
        {
            "violations": [{"field": field, "message": message}],
        },
    )
