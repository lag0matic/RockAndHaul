import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from lib.PluginBase import PluginBase
from .client import MiningClient
from .request_limits import ground_limits


class MiningQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    commodity: str = Field(default="platinum", min_length=1, max_length=40, description="Mineral: platinum, painite, LTD, osmium or one of the eight core minerals. Not engineering materials.")
    reference_system: str | None = Field(default=None, min_length=1, max_length=100, description="Omit for current location; set for questions such as near Sol. Exact system name, not coordinates.")
    site_mode: Literal["auto", "overlaps", "high-yield", "hotspots", "rings"] = Field(default="auto", description="Auto uses curated overlaps for Platinum/Painite/LTD, suitable rings for Osmium, ordinary hotspots for core minerals. High-yield is opt-in platinum only.")
    method: Literal["laser", "core"] | None = None
    minimum_price: int | None = Field(default=None, ge=0, le=1000000000, description="Credits per tonne. Omit for default: platinum 250000; other minerals no floor.")
    max_market_age_hours: float = Field(default=24, gt=0, le=168)
    pad: Literal["S", "M", "L"] | None = Field(default=None, description="Normally null: the tool reads the ship requirement. Set only for an explicit pad request.")
    route_priority: Literal["total", "loaded"] = Field(default="total", description="Minimize whole trip or prioritize mine-to-buyer distance.")
    max_total_ly: float | None = Field(default=None, gt=0, le=100000, description="Null unless Commander gives an explicit numeric whole-trip LY limit. 'Nearby', 'closest', 'short trip' mean rank by distance, NOT a 50 LY cap or any invented cap.")
    max_loaded_ly: float | None = Field(default=None, gt=0, le=100000, description="Null unless Commander gives an explicit numeric mine-to-buyer LY limit. Minimize travel by route_priority, not by inventing a distance cap.")
    max_approach_ls: float | None = Field(default=None, ge=0, le=100000000, description="Null unless Commander explicitly requests a numeric station-arrival limit in light-seconds. Do not fill this with an arbitrary large number.")


class RockAndHaul(PluginBase):
    settings_config = {"key": "RockAndHaul", "label": "Rock & Haul", "icon": "diamond", "grids": []}

    def __init__(self, plugin_manifest):
        super().__init__(plugin_manifest)
        self.settings = {}
        self._client = MiningClient()
        self._current_request = None

    def get_settings_config(self):
        return self.settings_config

    def on_settings_changed(self, settings):
        self.settings = dict(settings)

    def on_chat_start(self, helper):
        self._current_request = None
        helper.register_sideeffect(self.capture_request)
        helper.register_action("rock_and_haul", "Plan where to mine AND sell a mineral nearby or near a named system. "
                               "Use this for platinum/painite/LTD overlaps, osmium suitable rings or core-mining hotspots. "
                               "Filters minimum price, market freshness, pad and station approach; reports station demand as tonnes being bought. "
                               "minimizes whole-trip or loaded-leg distance. Returns up to three pairs, not every market. "
                               "NEARBY MEANS RANK CLOSEST FIRST, NOT A HARD RADIUS. Leave max_total_ly, max_loaded_ly, "
                               "max_approach_ls and pad null unless explicitly requested. Never invent numeric caps or loads. "
                               "Read-only, no route plotting. Public data cached for 20 minutes; do not repeat hoping to refresh it.",
                               MiningQuery, self.find_run, "global")

    def capture_request(self, event, projected_states):
        if getattr(event, "kind", None) == "user" and isinstance(getattr(event, "content", None), str):
            self._current_request = event.content

    def find_run(self, args, projected_states):
        try:
            grounded = ground_limits(args, self._current_request)
            result = self._client.lookup(grounded, projected_states)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                      "recommendations": [], "interpretation": "Lookup failed; do not invent a route or claim no markets exist. Do not retry during the 20-minute cooldown."}
        return json.dumps(result, ensure_ascii=True, allow_nan=False)
