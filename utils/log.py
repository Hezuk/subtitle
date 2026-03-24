import logging
import sys

_fmt = logging.Formatter(
    fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(_fmt)

_root = logging.getLogger("subtitle")
_root.setLevel(logging.INFO)
_root.addHandler(_handler)


def get_logger(name: str) -> logging.Logger:
    return _root.getChild(name)
