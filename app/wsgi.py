"""Entry point: `python -m wsgi` (production via waitress) or `flask --app wsgi run` (development)."""
import logging
import os
import sys

from kmsconsole import config, create_app

app = create_app(start_scheduler=os.environ.get("KMS_CONSOLE_SCHEDULER", "1") == "1")

if __name__ == "__main__":
    from waitress import serve
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(name)s: %(message)s")
    serve(app, host=config.LISTEN, port=config.PORT, threads=8, ident="KMS Console")
