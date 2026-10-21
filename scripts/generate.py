from pathlib import Path
import subprocess
import sys

from openapi_spec_validator import validate
from datamodel_code_generator import (
    generate,
    InputFileType,
    DataModelType,
    PythonVersion,
)
from datamodel_code_generator.types import StrictTypes
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = ROOT / "openapi/marketplace.yaml"
validate(yaml.safe_load(spec.read_text()))
subprocess.run(
    [
        sys.executable,
        "-m",
        "fastapi_code_generator",
        "--input",
        str(spec),
        "--output",
        str(ROOT / "app/generated"),
        "--template-dir",
        str(ROOT / "codegen"),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--python-version",
        "3.12",
        "--strict-nullable",
        "--include-request-argument",
        "--disable-timestamp",
    ],
    check=True,
    cwd=ROOT,
)
(ROOT / "app/generated/__init__.py").touch()

validators = {
    name: {
        "validators": [
            {"fields": fields, "function": "app.validation.number", "mode": "before"}
        ]
    }
    for name, fields in {
        "ProductCreate": ["price"],
        "PromoCodeCreate": ["discount_value", "min_order_amount"],
    }.items()
}
for name in ("OrderCreate", "OrderUpdate"):
    validators[name] = {
        "validators": [{"field": "items", "function": "app.validation.unique_products"}]
    }
validators["PromoCodeCreate"]["validators"].append(
    {
        "fields": ["valid_from", "valid_until"],
        "function": "app.validation.iso_datetime",
        "mode": "before",
    }
)
generate(
    spec,
    input_file_type=InputFileType.OpenAPI,
    output=ROOT / "app/generated/models.py",
    output_model_type=DataModelType.PydanticV2BaseModel,
    target_python_version=PythonVersion.PY_312,
    strict_nullable=True,
    disable_timestamp=True,
    strict_types=[StrictTypes.int, StrictTypes.bool],
    validators=validators,
)

for path in (ROOT / "app/generated").glob("*.py"):
    path.write_text(
        "".join(
            line
            for line in path.read_text().splitlines(keepends=True)
            if not line.lstrip().startswith("#")
        ).lstrip("\n")
    )
