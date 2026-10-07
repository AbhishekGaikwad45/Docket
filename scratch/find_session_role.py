with open("app.py", "r", encoding="utf-8") as f:
    for idx, line in enumerate(f, 1):
        if "session" in line and "role" in line:
            print(f"{idx}: {line.strip()[:100]}")
