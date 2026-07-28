from __future__ import annotations

import json
from pprint import pprint

from dotenv import load_dotenv

from app.tools.mysql_info import get_mysql_table_info

load_dotenv()


def test_local_mysql_info():
    # raw = mysql_table_info(
    #     host="localhost",
    #     port=3306,
    #     user="root",
    #     password="hupeiyuan654",
    #     database="xcode",
    #     table_name=""
    # )
    raw = get_mysql_table_info.invoke({"table_name": ""})
    result = json.loads(raw)
    pprint(result)


if __name__ == "__main__":
    test_local_mysql_info()
