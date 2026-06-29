"""
Activities for using OpenAI GPT-4o to determine plant care ranges when
OpenPlantbook doesn't have data for a given species.
"""

import os

from openai import AsyncOpenAI, AuthenticationError, RateLimitError, APIStatusError, APIConnectionError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from models.config import settings
from models.plant import CareRanges, CareRangesWithReasoning

# ---------------------------------------------------------------------------
# OpenAI client — retries disabled; Temporal handles retries
# ---------------------------------------------------------------------------

def _get_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        max_retries=0,  # CRITICAL: let Temporal handle retries
        timeout=60.0,
    )


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

@activity.defn
async def get_care_ranges_from_ai(species: str) -> CareRangesWithReasoning:
    """
    Use GPT-4o with structured outputs to determine acceptable care ranges
    for a plant species not found in OpenPlantbook.

    Returns a CareRangesWithReasoning object with AI-suggested values and
    a plain-English explanation for each range.
    """
    activity.logger.info(f"Asking GPT-4o for care ranges for species: {species!r}")

    client = _get_openai_client()

    system_prompt = (
        "You are a botanist and plant care expert. "
        "When given a plant species name, you return the ideal care ranges "
        "for that plant as structured data. "
        "All temperature values should be in Celsius. "
        "Soil moisture and humidity values should be percentages (0-100). "
        "Light values should be in lux. "
        "For soil moisture specifically: "
        "  soil_moisture_min is the dry threshold — the level at which the plant "
        "  should next be watered. For drought-tolerant or xeric species (cacti, "
        "  succulents, etc.) this should be very low, reflecting that the soil must dry out completely before the next watering. "
        "  It should always be higher than 0, however, because 0 always means that the plant needs to be watered. "
        "  soil_moisture_max is the level reached immediately after a thorough "
        "  watering. Do not return a range that represents a 'comfortable steady "
        "  state' — return the actual min/max that the watering cycle produces. "
        "For watering_interval_days: estimate the typical number of days between waterings "
        "for this species under normal indoor conditions. For example, cacti and succulents "
        "may be 14-30 days, tropical plants 7-10 days, and moisture-loving plants 3-5 days. "
        "If you are uncertain of a value, provide a reasonable typical range for the species. "
        "For each metric, also include a brief plain-English explanation (1-2 sentences) "
        "of why you chose that range for the given species — covering its natural habitat "
        "or growth requirements as appropriate."
    )

    user_prompt = (
        f"Provide the ideal care ranges for the following plant: {species}\n\n"
        "Include: soil moisture range (%), temperature range (°C), "
        "air humidity range (%), light level range (lux), and watering interval (days between waterings)."
    )

    try:
        response = await client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=CareRangesWithReasoning,
            temperature=0.2,
        )

        result = response.choices[0].message.parsed
        if result is None:
            raise ApplicationError(
                "GPT-4o returned empty structured output",
                type="ParseError",
            )

        activity.logger.info(
            f"AI care ranges for {species!r}: "
            f"moisture {result.soil_moisture_min}-{result.soil_moisture_max}%, "
            f"temp {result.temperature_min}-{result.temperature_max}°C"
        )
        return result

    except AuthenticationError as e:
        raise ApplicationError(
            f"Invalid OpenAI API key: {e}",
            type="AuthenticationError",
            non_retryable=True,
        )

    except RateLimitError as e:
        # Parse Retry-After if available
        retry_after = None
        if hasattr(e, "response") and e.response is not None:
            retry_after_str = e.response.headers.get("Retry-After")
            if retry_after_str:
                try:
                    from datetime import timedelta
                    retry_after = timedelta(seconds=int(retry_after_str))
                except ValueError:
                    pass

        raise ApplicationError(
            f"OpenAI rate limited: {e}",
            type="RateLimitError",
            **({"next_retry_delay": retry_after} if retry_after else {}),
        )

    except APIStatusError as e:
        if e.status_code >= 500:
            raise ApplicationError(
                f"OpenAI server error ({e.status_code}): {e}",
                type="ServerError",
            )
        raise ApplicationError(
            f"OpenAI client error ({e.status_code}): {e}",
            type="ClientError",
            non_retryable=True,
        )

    except APIConnectionError as e:
        raise ApplicationError(
            f"OpenAI connection error: {e}",
            type="ConnectionError",
        )
