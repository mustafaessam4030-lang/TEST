import pytest


@pytest.fixture(scope="session")
def chromium_available():
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium not available: {exc}")
