import re
from typing import Optional

MAX_FILE_SIZE = 50 * 1024 * 1024

def is_valid_url(url: str) -> bool:
    pattern = re.compile(r"^https?://[^\s]+$")
    return bool(pattern.match(url))

def file_size_exceeded(size_bytes: int) -> bool:
    return size_bytes > MAX_FILE_SIZE

def format_size(size_bytes: int) -> str:
    mb = size_bytes / (1024 * 1024)
    return f"{mb:.1f}MB"
