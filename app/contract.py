from decimal import Decimal
from pathlib import Path

from fastapi import Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import yaml

SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "openapi/marketplace.yaml").read_text()
)


def contract_response(data, model, status):
    if model is None:
        return Response(status_code=status)
    dto = model.model_validate(data)
    content = jsonable_encoder(
        dto.model_dump(mode="python"), custom_encoder={Decimal: float}
    )
    return JSONResponse(content, status_code=status)
