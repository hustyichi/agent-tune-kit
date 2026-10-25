import json


def emit(result, json_output=False):
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for key, value in result.items():
            if key == "browser_open":
                value = "ok" if value else "skipped"
            print(f"{key}={value}")
