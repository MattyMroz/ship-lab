def normalize_newlines(text: str) -> str:
    """Return the text with Windows line endings replaced by LF."""
    return text.replace("\r\n", "\n")


def parse_lines(text: str) -> list[str]:
    """Return the non-empty lines of the text that are not comments."""
    return [line for line in normalize_newlines(text).split("\n") if line.strip() and not line.startswith("#")]
