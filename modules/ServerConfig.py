import configparser
import ipaddress
import logging
from pathlib import Path

log = logging.getLogger("BrowserAPI")


class ServerConfig:

    CONFIG_FILE = Path("../config.ini")

    def __init__(self):

        config = configparser.ConfigParser()

        if self.CONFIG_FILE.exists():
            config.read(self.CONFIG_FILE)
        else:
            log.warning(
                "config.ini not found — using defaults"
            )

        section = (
            config["server"]
            if "server" in config
            else {}
        )

        # -------------------------
        # Allowed IP rules
        # -------------------------

        self.allowed_ip_rules = self._parse_ip_rules(
            section.get(
                "allowed_ips",
                "localhost"
            )
        )

        # -------------------------
        # Optional string fields
        # -------------------------

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

        # -------------------------
        # Numeric fields
        # -------------------------

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

        self.port = int(
            section.get(
                "port",
                5049
            )
        )

        # -------------------------
        # Boolean fields
        # -------------------------

        if "server" in config:
            self.headless = config.getboolean(
                "server",
                "headless",
                fallback=False
            )
        else:
            self.headless = False

        # -------------------------
        # Validate values
        # -------------------------

        self._validate()

    # ==========================================================
    # IP Rules
    # ==========================================================

    @staticmethod
    def _parse_ip_rules(value: str):

        if not value:
            return []

        parts = [
            p.strip()
            for p in value.replace(",", "\n").splitlines()
            if p.strip()
        ]

        rules = []

        for part in parts:

            # Translate localhost
            if part.lower() == "localhost":
                part = "127.0.0.1"

            try:

                network = ipaddress.ip_network(
                    part,
                    strict=False
                )

                rules.append(network)

            except ValueError:

                log.error(
                    f"Invalid IP rule: {part}"
                )

        # Ensure localhost always allowed
        localhost_net = ipaddress.ip_network(
            "127.0.0.1"
        )

        if not any(
            localhost_net == r
            for r in rules
        ):
            rules.append(localhost_net)

        # Warn if allowing everything
        for net in rules:

            if net.prefixlen == 0:

                log.warning(
                    "WARNING: 0.0.0.0/0 allows ALL IPs"
                )

        return rules

    # ==========================================================
    # Optional string
    # ==========================================================

    @staticmethod
    def _parse_optional_str(value: str):

        if value is None:
            return None

        v = value.strip().lower()

        if v in ("", "none", "null"):
            return None

        return value.strip()

    # ==========================================================
    # Validation
    # ==========================================================

    def _validate(self):

        if not (1 <= self.port <= 65535):

            raise ValueError(
                f"Invalid port: {self.port}"
            )

        if self.browser_width <= 0:

            raise ValueError(
                "browser_width must be > 0"
            )

        if self.browser_height <= 0:

            raise ValueError(
                "browser_height must be > 0"
            )