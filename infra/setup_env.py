"""Create a private local environment without replacing existing configuration."""
import os
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    if target.exists():
        print("Existing .env preserved; configure it privately if credentials are missing.")
        return
    password = secrets.token_hex(24)
    values = {
        "POSTGRES_PASSWORD": password,
        "S3_SECRET_KEY": secrets.token_hex(24),
        "DATABASE_URL": f"postgres://framesearch:{password}@localhost:5432/framesearch?sslmode=disable",
    }
    lines = (root / ".env.example").read_text().splitlines()
    text = "\n".join(
        f"{line.split('=', 1)[0]}={values[line.split('=', 1)[0]]}"
        if "=" in line and line.split("=", 1)[0] in values else line
        for line in lines
    ) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    print("Created private .env with unique local credentials; values are not displayed.")


if __name__ == "__main__":
    main()
