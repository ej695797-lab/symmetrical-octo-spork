"""Create server/.env from .env.example on first run, with a random headset-key
pepper. Does nothing if .env already exists.
start.bat / start.sh run this; you can also run it by hand: python setup_env.py"""
import os
import secrets
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")
EXAMPLE = os.path.join(HERE, ".env.example")


def main() -> None:
    if os.path.exists(ENV):
        return
    shutil.copyfile(EXAMPLE, ENV)
    text = open(ENV, encoding="utf-8").read()
    text = text.replace("DEVICE_KEY_PEPPER=\n", f"DEVICE_KEY_PEPPER={secrets.token_hex(32)}\n", 1)
    with open(ENV, "w", encoding="utf-8") as f:
        f.write(text)
    print("=" * 64)
    print(" Created server/.env (your server settings).")
    print(" Using a local MongoDB by default: edit MONGO_URI in .env for Atlas.")
    print("=" * 64)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
