"""Human-readable byte sizes (CLI display)."""


def format_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{n} B"
        value /= 1024
    return f"{n} B"  # unreachable
