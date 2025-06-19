import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import requests
import os
import dropbox
from dotenv import load_dotenv


# ---------------
# Load Secrets from .env
# ---------------
load_dotenv()
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")
DROPBOX_UPLOAD_PATH = os.getenv("DROPBOX_UPLOAD_PATH")

# ---------------
# Dropbox Connection with Refresh Token
# ---------------

def create_dropbox_client():
    dbx = dropbox.Dropbox(
        oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
        app_key=DROPBOX_APP_KEY,
        app_secret=DROPBOX_APP_SECRET
    )
    return dbx


# ---------------
# Load Data (Fresh Dropbox Download)
# ---------------
@st.cache_data
def load_data():
    local_path = "/tmp/dummy_air_quality.duckdb"
    rev_path = "/tmp/dummy_air_quality.rev"

    st.info("Checking Dropbox for latest data...")

    dbx = create_dropbox_client()

    # Get remote file metadata
    metadata = dbx.files_get_metadata(DROPBOX_UPLOAD_PATH)
    remote_rev = metadata.rev

    # Check if local file exists and has matching rev
    if os.path.exists(local_path) and os.path.exists(rev_path):
        with open(rev_path, "r") as f:
            local_rev = f.read().strip()
        if local_rev == remote_rev:
            st.success("Local data is up-to-date.")
            conn = duckdb.connect(local_path, read_only=True)
            df_hourly = conn.execute("SELECT * FROM air_quality_hourly").fetchdf()
            conn.close()
            return df_hourly

    # Download fresh copy if new revision detected
    st.info("New version detected. Downloading updated data from Dropbox...")

    with open(local_path, "wb") as f:
        metadata, res = dbx.files_download(DROPBOX_UPLOAD_PATH)
        f.write(res.content)

    # Store new rev locally
    with open(rev_path, "w") as f:
        f.write(remote_rev)

    st.success("Download complete.")

    conn = duckdb.connect(local_path, read_only=True)
    df_hourly = conn.execute("SELECT * FROM air_quality_hourly").fetchdf()
    conn.close()
    return df_hourly

# ---------------
# Load Data
# ---------------
df = load_data()
df['Hour_Timestamp'] = pd.to_datetime(df['Hour_Timestamp'])
zip_codes = sorted(df["Zip_Code"].unique())

# ---------------- Title ----------------
st.title("🌫 FHA - Air Quality Dashboard")

# ------------ Global Filters -----------
st.markdown("""<h6 style='margin-bottom:5px;'>\U0001F50E <u>Filters</u></h6>""", unsafe_allow_html=True)

# First row: ZIP Code Filter
with st.expander("Filter ZIP Codes", expanded=False):
    if "selected_zips" not in st.session_state:
        st.session_state.selected_zips = zip_codes.copy()

    selected_zips = st.multiselect(
        "ZIP Codes:", zip_codes,
        default=st.session_state.selected_zips,
        key="zip_filter"
    )

    if st.button("Reset ZIPs"):
        selected_zips = zip_codes
        st.session_state.selected_zips = zip_codes.copy()
# After ZIP Code multiselect

# Second row: Date Filter (All 4 dropdowns in one row)
df_dates = df["Hour_Timestamp"].dt.to_period("M").drop_duplicates().sort_values()
year_month_pairs = [(p.year, p.month) for p in df_dates]
years = sorted(set(y for y, m in year_month_pairs))
months_lookup = {year: sorted(m for y, m in year_month_pairs if y == year) for year in years}

col1, col2, col3, col4 = st.columns([1, 1, 1, 1])

with col1:
    year_start = st.selectbox("Start Year", years, key="start_year")
with col2:
    start_month_options = [datetime(1900, m, 1).strftime('%B') for m in months_lookup[year_start]]
    month_start = st.selectbox("Start Month", start_month_options, key="start_month")
with col3:
    year_end = st.selectbox("End Year", years, index=len(years)-1, key="end_year")
with col4:
    end_month_options = [datetime(1900, m, 1).strftime('%B') for m in months_lookup[year_end]]
    month_end = st.selectbox("End Month", end_month_options, index=len(end_month_options)-1, key="end_month")

start_dt = datetime.strptime(f"{month_start} {year_start}", "%B %Y")
end_dt = datetime.strptime(f"{month_end} {year_end}", "%B %Y")
end_dt = end_dt.replace(day=1) + pd.offsets.MonthEnd(1)
st.markdown(f"<h5 style='color: grey; margin-top: -8px;'font-size:0.6em;'>Time Period: ({start_dt.strftime('%b %Y')} - {end_dt.strftime('%b %Y')})</h5>", unsafe_allow_html=True)

filtered_df = df[(df["Zip_Code"].isin(selected_zips)) & (df["Hour_Timestamp"] >= start_dt) & (df["Hour_Timestamp"] <= end_dt)]

# ---------------
# Main Tabs
# ---------------
tab1, tab2, tab3, tab4 = st.tabs(["📊 Overview", "📈 Trends", "🗺 Map", "ℹ️ About"])

# ---------------------------
# Overview Tab
# ---------------------------
with tab1:
    subtab1, subtab2 = st.tabs(["🔢 Summary Metrics", "🎯 AQI Categories"])

    # -------- Summary Metrics --------
    with subtab1:
        if filtered_df.empty:
            st.warning("No data available for selected filters.")
        else:
            # ---------------- Summary metrics ----------------
            latest = filtered_df.sort_values("Hour_Timestamp").groupby("Zip_Code").tail(1)
            avg_aqi = round(filtered_df["Avg_AQI"].mean(), 1)
            
            best_zip = latest.loc[latest["Avg_AQI"].idxmin()]
            worst_zip = latest.loc[latest["Avg_AQI"].idxmax()]

            col1, col2, col3 = st.columns(3)
            col1.metric("🌡 Avg AQI", avg_aqi)
            col2.metric("✅ Best ZIP", f"{best_zip['Zip_Code']} ({round(best_zip['Avg_AQI'],1)})")
            col3.metric("🔥 Worst ZIP", f"{worst_zip['Zip_Code']} ({round(worst_zip['Avg_AQI'],1)})")

            # Convert to daily summaries (truncate timestamp to date)
            filtered_df["Date"] = filtered_df["Hour_Timestamp"].dt.date
            daily_aqi = filtered_df.groupby("Date")["Avg_AQI"].mean().reset_index()

            total_days = daily_aqi.shape[0]
            good_days = daily_aqi[daily_aqi["Avg_AQI"] <= 50].shape[0]
            unhealthy_days = daily_aqi[daily_aqi["Avg_AQI"] >= 101].shape[0]

            pct_good_days = round((good_days / total_days) * 100, 1) if total_days > 0 else 0
            pct_unhealthy_days = round((unhealthy_days / total_days) * 100, 1) if total_days > 0 else 0

            total_observations = filtered_df.shape[0]

            st.divider()

            col4, col5, col6 = st.columns(3)
            col4.metric("✅ Good Days (≤50 AQI)", f"{good_days} ({pct_good_days}%)")
            col5.metric("🚩 Unhealthy Days (≥101 AQI)", f"{unhealthy_days} ({pct_unhealthy_days}%)")
            col6.metric("📊 Total Readings (Hourly)", total_observations)


    # -------- AQI Category Distribution --------
    with subtab2:
        if filtered_df.empty:
            st.warning("No data available for selected filters.")
        else:
            def categorize_aqi(aqi):
                if aqi <= 50: return "Good"
                elif aqi <= 100: return "Moderate"
                elif aqi <= 150: return "Unhealthy (Sensitive)"
                elif aqi <= 200: return "Unhealthy"
                elif aqi <= 300: return "Very Unhealthy"
                else: return "Hazardous"

            filtered_df["AQI_Category"] = filtered_df["Avg_AQI"].apply(categorize_aqi)
            cat_counts = filtered_df["AQI_Category"].value_counts().reset_index()
            cat_counts.columns = ["Category", "Count"]

            category_order = [
                "Good", "Moderate", "Unhealthy (Sensitive)", 
                "Unhealthy", "Very Unhealthy", "Hazardous"
            ]

            cat_counts["Category"] = pd.Categorical(
                cat_counts["Category"], 
                categories=category_order, 
                ordered=True
            )
            cat_counts = cat_counts.sort_values("Category")

            color_map = {
                "Good": "#00e400",
                "Moderate": "#ffff00",
                "Unhealthy (Sensitive)": "#ff7e00",
                "Unhealthy": "#ff0000",
                "Very Unhealthy": "#8f3f97",
                "Hazardous": "#7e0023"
            }

            fig = px.pie(
                cat_counts,
                names="Category",
                values="Count",
                color="Category",
                color_discrete_map=color_map,
                title="AQI Category Distribution"
            )
            fig.update_traces(sort=False)  # <-- Fully locks legend + slices
            st.plotly_chart(fig, use_container_width=True)
            st.markdown("""
            <p style='font-size:0.9em; color:grey;'>
            <b>How often air was clean or polluted:</b><br>
            This chart shows what percentage of all hourly air quality readings landed in each health category across your selected ZIP codes and dates.
            </p>
            """, unsafe_allow_html=True)
            
# ---------------
# Trends Tab
# ---------------
with tab2:
    st.header("Time Trends")
    subtab1, subtab2 = st.tabs(["📅 Monthly AQI Trends", "Time-of-Day Heatmap"])

    with subtab1:
        if filtered_df.empty:
            st.warning("No data available for selected filters.")
        else:
            st.subheader("📅 Monthly Trends (Average Across ZIPs)")

            # Extract year/month pairs
            df_dates = filtered_df["Hour_Timestamp"].dt.to_period("M").drop_duplicates().sort_values()
            year_month_pairs = [(p.year, p.month) for p in df_dates]
            years = sorted(set(y for y, m in year_month_pairs))
            months_lookup = {year: sorted(m for y, m in year_month_pairs if y == year) for year in years}

            # Month selection
            col1, col2 = st.columns([1, 1])
            with col1:
                selected_year = st.selectbox("Select Year", years, key="trends_year")
            with col2:
                month_options = [datetime(1900, m, 1).strftime('%B') for m in months_lookup[selected_year]]
                selected_month = st.selectbox("Select Month", month_options, key="trends_month")

            # Filter to selected month
            month_num = datetime.strptime(selected_month, "%B").month
            start_month_dt = datetime(selected_year, month_num, 1)
            end_month_dt = start_month_dt + pd.offsets.MonthEnd(0)

            month_df = filtered_df[
                (filtered_df["Hour_Timestamp"].dt.date >= start_month_dt.date()) &
                (filtered_df["Hour_Timestamp"].dt.date <= end_month_dt.date())
            ]

            if month_df.empty:
                st.warning("No data available for this month.")
            else:
                # Daily average across all ZIP codes
                month_df["Date"] = month_df["Hour_Timestamp"].dt.date
                daily_avg = month_df.groupby("Date")["Avg_AQI"].mean().reset_index()

                fig = px.line(
                    daily_avg, 
                    x="Date", y="Avg_AQI",
                    title=f"Daily Average AQI - {selected_month} {selected_year}",
                    markers=True
                )
                fig.update_layout(yaxis_title="AQI", xaxis_title="Date")
                st.plotly_chart(fig, use_container_width=True)

# ---------- Chart Summary -----------
                st.markdown(
                    "<p style='font-size:0.9em; color:grey;'><b>What this chart shows:</b> This daily trend displays the average air quality across selected ZIP codes for the chosen month. Each point reflects the average AQI of all hourly readings for that day.</p>", 
                    unsafe_allow_html=True
                )
                
                # Display applied filters
                month_display = f"{selected_month} {selected_year}"
                num_zips = len(selected_zips)
                zip_list = ', '.join(selected_zips)

                # Find highest and lowest AQI dates
                max_row = daily_avg.loc[daily_avg['Avg_AQI'].idxmax()]
                min_row = daily_avg.loc[daily_avg['Avg_AQI'].idxmin()]

                st.markdown("---")
                st.markdown("""
                    <p style='font-size:0.95em;'>       
                    <b>Summary for {month_display}</b><br>
                    
                    <b>Applied ZIP Codes<b>:
                    [{zip_list}] <b>({num_zips} total)<b><br>
                    <b>Highest AQI:</b> {max_row['Avg_AQI']:.1f} ({max_row['Date'].strftime('%m/%d/%Y')})<br>
                    <b>Lowest AQI:</b> {min_row['Avg_AQI']:.1f}  ({min_row['Date'].strftime('%m/%d/%Y')})
                    </p>
                    """, 
                    unsafe_allow_html=True
                )

# ---------------
# Map Tab
# ---------------
with tab3:
    st.header("Sensor Locations")

    if filtered_df.empty:
        st.warning("No data available for selected filters.")
    else:
        latest_locations = filtered_df.sort_values("Hour_Timestamp").groupby("Sensor_ID").tail(1)

        fig_map = px.scatter_mapbox(
            latest_locations, lat="Latitude", lon="Longitude", color="Avg_AQI",
            size="Avg_PM2_5", hover_name="Zip_Code",
            color_continuous_scale="RdYlGn_r", zoom=10, height=500
        )
        fig_map.update_layout(mapbox_style="open-street-map")
        st.plotly_chart(fig_map, use_container_width=True)


# ---------------
# About Tab
# ---------------
with tab4:
    st.header("About This Dashboard")
    st.markdown("""
    This interactive dashboard displays simulated air quality data for Fresno, CA using PM2.5 and AQI metrics.
    
    **AQI Categories:**
    - Good (0–50)
    - Moderate (51–100)
    - Unhealthy for Sensitive Groups (101–150)
    - Unhealthy (151–200)
    - Very Unhealthy (201–300)
    - Hazardous (301+)
    
    The data is fully synthetic but modeled to reflect realistic seasonal air quality patterns in Fresno County.

    Created by Chinedu Justin Okonkwo (Fresno Healthy Air).
    """)
