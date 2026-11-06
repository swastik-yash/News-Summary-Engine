import threading
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from src.countries import load_countries
from src.triangulation_pipeline import NewsTriangulationEngine, TriangulationResult
from src.logger import setup_logger, LogCallbackHandler

load_dotenv()

st.set_page_config(
    page_title="News Summary",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Thread-safe in-memory log buffer (background thread safety)
if "_ui_log_lock" not in globals():
    _ui_log_lock = threading.Lock()
if "_ui_memory_logs" not in globals():
    _ui_memory_logs = []

def ui_log_callback(msg: str):
    with _ui_log_lock:
        _ui_memory_logs.append(msg)
        if len(_ui_memory_logs) > 600:
            _ui_memory_logs.pop(0)

# Session state init
if "tri_result" not in st.session_state:
    st.session_state["tri_result"] = None

# Attach log handler once
logger = setup_logger("news_engine")
if not any(isinstance(h, LogCallbackHandler) for h in logger.handlers):
    import logging
    log_handler = LogCallbackHandler(ui_log_callback)
    log_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(log_handler)

# Country catalog
all_countries = load_countries()
default_country = "India" if "India" in all_countries else all_countries[0]

# ==========================================
# Sidebar
# ==========================================
with st.sidebar:
    st.title("News Summary")
    st.divider()

    selected_country = st.selectbox(
        label="Country",
        options=all_countries,
        index=all_countries.index(default_country) if default_country in all_countries else 0,
    )

    with st.expander("Settings"):
        min_impressions_val = st.number_input(
            label="Minimum post popularity (views)",
            min_value=1000,
            max_value=500000,
            value=20000,
            step=5000,
            help="Only posts with this many views or more are included in the summary."
        )
        time_window_hours = st.slider(
            label="Time window (hours)",
            min_value=6,
            max_value=48,
            value=24,
            step=6,
            help="How far back to look for posts."
        )

    st.divider()
    run_btn = st.button(
        label="Get Today's News",
        type="primary",
        width="stretch"
    )

# ==========================================
# Execution
# ==========================================
if run_btn:
    with _ui_log_lock:
        _ui_memory_logs.clear()

    engine = NewsTriangulationEngine()

    with st.spinner(f"Fetching today's news for {selected_country}..."):
        res = engine.run(
            country=selected_country,
            min_impressions=min_impressions_val,
            hours=time_window_hours,
            top_n_channels=5,
            discovery_sources=4
        )
        st.session_state["tri_result"] = res

# ==========================================
# Main Content
# ==========================================
st.title("News Summary")

res: TriangulationResult = st.session_state.get("tri_result")

if res:
    # ---- Section 1: Summary ----
    st.subheader(f"Today in {res.country}")

    if 0 < len(res.posts) <= 3:
        st.info(
            f"Only {len(res.posts)} post(s) were popular enough to include. "
            "You can lower the minimum popularity threshold in Settings to capture more stories."
        )
    elif len(res.posts) == 0:
        st.warning(
            "No posts met the popularity threshold in this time window. "
            "Try lowering the minimum popularity threshold in Settings."
        )

    if res.summary:
        st.markdown(res.summary)

    st.divider()

    # ---- Section 2: Posts ----
    st.subheader("Posts Used for This Summary")

    if not res.posts_df.empty:
        display_posts = res.posts_df.copy()

        # Truncate post text for readability
        if "Text" in display_posts.columns:
            display_posts["Post"] = display_posts["Text"].str[:120].where(
                display_posts["Text"].str.len() <= 120,
                display_posts["Text"].str[:120] + "..."
            )

        # Build clean view
        cols_to_show = ["Channel", "Post", "Impressions", "Link"]
        available = [c for c in cols_to_show if c in display_posts.columns or c == "Post"]
        clean_posts = pd.DataFrame()
        if "Channel" in display_posts.columns:
            clean_posts["Channel"] = display_posts["Channel"]
        if "Post" in display_posts.columns:
            clean_posts["Post"] = display_posts["Post"]
        if "Impressions" in display_posts.columns:
            clean_posts["Views"] = display_posts["Impressions"]
        if "Link" in display_posts.columns:
            clean_posts["Link"] = display_posts["Link"]

        st.dataframe(
            clean_posts,
            hide_index=True,
            column_config={
                "Views": st.column_config.NumberColumn("Views", format="%d"),
                "Link": st.column_config.LinkColumn("Read on X", display_text="Read on X")
            }
        )

        # Downloads — right-aligned, unobtrusive
        _, dl1, dl2 = st.columns([4, 1, 1])
        with dl1:
            st.download_button(
                label="Download CSV",
                data=res.posts_df.to_csv(index=False).encode("utf-8"),
                file_name=f"news_{res.country.lower().replace(' ', '_')}.csv",
                mime="text/csv",
                width="stretch"
            )
        with dl2:
            st.download_button(
                label="Download JSON",
                data=res.posts_df.to_json(orient="records", indent=2).encode("utf-8"),
                file_name=f"news_{res.country.lower().replace(' ', '_')}.json",
                mime="application/json",
                width="stretch"
            )
    else:
        st.info("No posts are available to display.")

    st.divider()

    # ---- Section 3: Top Accounts ----
    st.subheader(f"Top News Accounts Covering {res.country}")

    if not res.ranked_df.empty:
        clean_channels = pd.DataFrame()
        if "Channel Name" in res.ranked_df.columns:
            clean_channels["Channel"] = res.ranked_df["Channel Name"]
        if "Followers" in res.ranked_df.columns:
            clean_channels["Followers"] = res.ranked_df["Followers"]
        if "Link" in res.ranked_df.columns:
            clean_channels["Link"] = res.ranked_df["Link"]

        st.dataframe(
            clean_channels,
            hide_index=True,
            column_config={
                "Followers": st.column_config.NumberColumn("Followers", format="%d"),
                "Link": st.column_config.LinkColumn("View Profile", display_text="View Profile")
            }
        )
    else:
        st.info("No account data available.")

else:
    # Empty state
    st.markdown(
        "Select a country in the sidebar and click **Get Today's News** to generate a summary."
    )
    st.divider()
    st.markdown(
        "Finds the most-followed national news accounts, reads their posts from the past 24 hours, "
        "and writes a plain-English summary of what happened — with links to every post."
    )
