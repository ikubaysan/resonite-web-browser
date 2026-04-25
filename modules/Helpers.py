import logging

log = logging.getLogger("BrowserAPI")

def parse_coordinates(text: str):
    """
    Parse coordinate input into (x, y) floats.

    Accepted formats:

        134.3 -252.2
        134.3, -252.2
        [134.3, -252.2]
        [134.3; -252.2]
        (134.3, -252.2)
        134.3;-252.2

    Rules:

    - Surrounding brackets [] or () are optional
    - Separator can be:
        space
        comma (,)
        semicolon (;)
    - Values may be integers or floats
    - Exactly two numbers are required

    Returns:
        (x, y) as floats

    Raises:
        ValueError if parsing fails
    """

    if not text:
        raise ValueError("Empty coordinate input")

    s = text.strip()

    # Remove surrounding brackets if present
    if (
        (s.startswith("[") and s.endswith("]")) or
        (s.startswith("(") and s.endswith(")"))
    ):
        s = s[1:-1].strip()

    # Normalize separators to spaces
    s = s.replace(",", " ")
    s = s.replace(";", " ")

    parts = s.split()

    if len(parts) != 2:
        raise ValueError(
            f"Invalid coordinate format: '{text}'"
        )

    try:
        x = float(parts[0])
        y = float(parts[1])
    except ValueError:
        raise ValueError(
            f"Invalid numeric values in: '{text}'"
        )

    return x, y



def log_screenshot_size(data: bytes, filename: str, label: str = ""):
    size_kb = len(data) / 1024
    tag = f" [{label}]" if label else ""
    log.info(f"[SCREENSHOT]{tag} {filename} size: {size_kb:.2f} KB")