"""Streamlit entry point for the pronunciation coach.

Scaffold only: this page renders and nothing else. No audio capture, no Azure
pronunciation assessment, no coaching model. UI code lives here; API calls never do.
"""

import logging

import streamlit as st

logger = logging.getLogger(__name__)

PAGE_TITLE = "Pronunciation Coach"
PAGE_ICON = "🗣️"

MODES: list[tuple[str, str]] = [
    ("Drill", "One or two scripted sentences, 20-30 s. The fast record-and-retry loop."),
    ("Paragraph", "Scripted, 100-200 words, 60-90 s. Connected speech and rhythm."),
    ("Unscripted", "No reference text, 3-4 min. Speaking and self-monitoring at once."),
]


def render() -> None:
    """Draw the placeholder page."""
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="centered")

    st.title(f"{PAGE_ICON} {PAGE_TITLE}")
    st.caption("Personal English pronunciation and delivery coach — en-US.")

    st.info(
        "Scaffold. The app starts, but no analysis is wired up yet: recording, "
        "pronunciation assessment, and coaching all still to come.",
        icon="🚧",
    )

    st.subheader("Planned modes")
    for name, description in MODES:
        st.markdown(f"**{name}** — {description}")

    logger.debug("Rendered scaffold page")


render()
