import configparser
from pathlib import Path
import logging

log = logging.getLogger("BrowserAPI")

class ServerConfig:
    """
    Strongly-typed configuration loader.

    Handles:
    - Default values
    - Type conversion
    - Optional None values
    - List parsing
    """

    CONFIG_FILE = Path("config.ini")

    def __init__(self):

        config = configparser.ConfigParser()

        if self.CONFIG_FILE.exists():
            config.read(self.CONFIG_FILE)
        else:
            log.warning("config.ini not found — using defaults")

        section = config["server"] if "server" in config else {}

        # ---- values ----

        self.allowed_ips = self._parse_list(
            section.get(
                "allowed_ips",
                "127.0.0.1, localhost"
            )
        )

        self.public_base_url = self._parse_optional_str(
            section.get(
                "public_base_url",
                "none"
            )
        )

        self.search_engine_url = section.get(
            "search_engine_url",
            "https://duckduckgo.com/?q={}"
        )

        self.browser_width = int(
            section.get(
                "browser_width",
                720
            )
        )

        self.browser_height = int(
            section.get(
                "browser_height",
                1280
            )
        )

        self.headless = config.getboolean(
            "server",
            "headless",
            fallback=False
        )

        self.port = int(
            section.get(
                "port",
                5049
            )
        )

        # Always allow localhost (safety)
        self.allowed_ips |= {
            "127.0.0.1",
            "localhost"
        }

    # -------------------------
    # Helpers
    # -------------------------

    @staticmethod
    def _parse_list(value: str):
        """Convert comma-separated list to set."""
        if not value:
            return set()

        return {
            item.strip()
            for item in value.split(",")
            if item.strip()
        }

    @staticmethod
    def _parse_optional_str(value: str):
        """
        Convert string to None if:
            none / null / empty
        """

        if value is None:
            return None

        v = value.strip().lower()

        if v in ("", "none", "null"):
            return None

        return value.strip()