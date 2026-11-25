import os

import requests


def query(sql, data=None, **parameters):
    response = requests.post(
        os.environ["CLICKHOUSE_URL"],
        auth=(os.environ["CLICKHOUSE_USER"], os.environ["CLICKHOUSE_PASSWORD"]),
        params={"query": sql, **{f"param_{key}": value for key, value in parameters.items()}},
        data=data,
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(response.text)
    return response.text
