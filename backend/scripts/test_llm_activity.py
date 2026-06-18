"""
Quick manual tester for the get_care_ranges_from_ai activity.

Usage (from the backend/ directory):
    uv run python scripts/test_llm_activity.py "Monstera deliciosa"
    uv run python scripts/test_llm_activity.py  # prompts for a species name
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Make sure backend/ package root is on the path when running directly
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

from activities.llm import get_care_ranges_from_ai


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    return f"{value:g} {unit}"


def _wrap(text: str, width: int, indent: str) -> str:
    """Wrap text to width, prefixing every line after the first with indent."""
    import textwrap
    lines = textwrap.wrap(text, width)
    return ("\n" + indent).join(lines)


async def main(species: str) -> None:
    print(f"\n🌿  Querying GPT-4o for care ranges: {species!r}\n")

    result = await get_care_ranges_from_ai(species)

    col_w = 20
    divider = "─" * 72
    reasoning_indent = "     "
    wrap_width = 72 - len(reasoning_indent)

    rows = [
        (
            "Soil moisture",
            f"{result.soil_moisture_min:g} – {result.soil_moisture_max:g} %",
            result.soil_moisture_reasoning,
        ),
        (
            "Temperature",
            f"{result.temperature_min:g} – {result.temperature_max:g} °C",
            result.temperature_reasoning,
        ),
        (
            "Air humidity",
            f"{result.air_humidity_min:g} – {result.air_humidity_max:g} %",
            result.air_humidity_reasoning,
        ),
        (
            "Light",
            f"{_fmt(result.light_lux_min, 'lux')} – {_fmt(result.light_lux_max, 'lux')}",
            result.light_lux_reasoning,
        ),
    ]

    print(divider)
    print(f"  {'Metric':<{col_w}} Range")
    print(divider)
    for label, value, reasoning in rows:
        print(f"  {label:<{col_w}} {value}")
        print(f"{reasoning_indent}↳ {_wrap(reasoning, wrap_width, reasoning_indent + '  ')}")
        print()
    print(divider)
    print()

    print("Raw model output:")
    print(result.model_dump_json(indent=2))
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        species_input = " ".join(sys.argv[1:])
    else:
        species_input = input("Enter plant species: ").strip()
        if not species_input:
            print("No species provided. Exiting.")
            sys.exit(1)

    asyncio.run(main(species_input))
