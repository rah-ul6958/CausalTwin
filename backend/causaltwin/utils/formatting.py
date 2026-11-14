"""Number formatting for explanation texts shown to people."""


def format_value(value: float) -> str:
    """A setting value for text: whole numbers above 100 (4,300 rather than 4.3e+03), else 3 significant digits."""
    if abs(value) >= 100:
        return f"{value:,.0f}"
    return f"{value:.3g}"
