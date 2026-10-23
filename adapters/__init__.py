"""
Course booking-platform adapters.

Each adapter knows how to read availability from one tee-sheet platform.
Courses are mapped to an adapter in courses.json; adding a course that
runs on a platform we already support is a config change, not code.

An adapter is a class with:

    name     — the platform key used in courses.json
    fetch(course) -> dict

`course` is the course's entry from courses.json. `fetch` returns:

    {
      "days":   [{"date": "MM/DD/YYYY", "slots": [
                    {"time": "7:38 am", "spots": 4, "holes": "18 (Front)",
                     "bookUrl": "https://..."}]}],
      "window": {"min": "MM/DD/YYYY", "max": "MM/DD/YYYY"},
    }

Raise any exception to signal failure — the collector records the course
as errored and keeps the previous data rather than blanking the site.
"""

from .chronogolf import ChronogolfAdapter
from .foreup import ForeUpAdapter
from .golfnow import GolfNowAdapter
from .quick18 import Quick18Adapter
from .webtrac import WebTracAdapter

_ADAPTERS = {
    ChronogolfAdapter.name: ChronogolfAdapter,
    ForeUpAdapter.name: ForeUpAdapter,
    GolfNowAdapter.name: GolfNowAdapter,
    Quick18Adapter.name: Quick18Adapter,
    WebTracAdapter.name: WebTracAdapter,
}


def get_adapter(name):
    """Return an adapter instance for `name`, or None if unsupported."""
    cls = _ADAPTERS.get(name)
    return cls() if cls else None


def supported():
    """Platform keys this build can read."""
    return sorted(_ADAPTERS)
