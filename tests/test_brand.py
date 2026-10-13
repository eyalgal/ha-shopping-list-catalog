"""Brand images served from the integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

BRAND = Path(__file__).parents[1] / "custom_components" / "shopping_list_catalog" / "brand"


async def test_brand_icon_is_served_locally(hass: HomeAssistant, hass_client: ClientSessionGenerator) -> None:
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    for image, file in (("icon.png", "icon.png"), ("icon@2x.png", "icon@2x.png"), ("logo.png", "icon.png")):
        response = await client.get(f"/api/brands/integration/shopping_list_catalog/{image}")
        assert response.status == 200, image
        assert await response.read() == (BRAND / file).read_bytes()
