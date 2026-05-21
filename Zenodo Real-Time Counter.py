import streamlit as st
import requests
import re

# Set web app configuration
st.set_page_config(page_title="Zenodo Real-Time Counter", page_icon="📊", layout="centered")

st.title("📊 Zenodo Real-Time Counter")
st.markdown("Fetch the **actual primary metrics** directly from Zenodo's internal API, bypassing web interface lag or display issues.")

# User input field
url_input = st.text_input(
    "Enter Zenodo Record URL or Record ID:",
    placeholder="e.g., https://zenodo.org/records/18813202 or 18813202"
)

if url_input:
    # Extract only the numerical record ID using regular expressions
    record_id_match = re.search(r'\d+', url_input)
    
    if record_id_match:
        record_id = record_id_match.group()
        api_url = f"https://zenodo.org/api/records/{record_id}"
        
        with st.spinner("Fetching live tracking data from Zenodo server..."):
            try:
                response = requests.get(api_url)
                
                # Fixed the typo from status_size to status_code
                if response.status_code == 200:
                    data = response.json()
                    stats = data.get("stats", {})
                    metadata = data.get("metadata", {})
                    title = metadata.get("title", f"Record #{record_id}")
                    
                    st.success(f"✅ Connection Successful: **{title}**")
                    st.write("---")
                    
                    # Responsive grid layout for metrics
                    col1, col2 = st.columns(2)
                    col3, col4 = st.columns(2)
                    
                    with col1:
                        st.metric(label="📥 Total Downloads", value=f"{stats.get('downloads', 0):,}")
                    with col2:
                        st.metric(label="👥 Unique Downloads", value=f"{stats.get('unique_downloads', 0):,}")
                    with col3:
                        st.metric(label="👀 Total Views", value=f"{stats.get('views', 0):,}")
                    with col4:
                        st.metric(label="👤 Unique Views", value=f"{stats.get('unique_views', 0):,}")
                        
                    st.write("---")
                    
                    # Current version metrics section
                    st.markdown("### 📌 Current Version Specifics")
                    
                    v_col1, v_col2 = st.columns(2)
                    v_col3, v_col4 = st.columns(2)
                    
                    with v_col1:
                        st.write(f"🔹 **Version Downloads:** {stats.get('version_downloads', 0):,}")
                    with v_col2:
                        st.write(f"🔹 **Version Unique Downloads:** {stats.get('version_unique_downloads', 0):,}")
                    with v_col3:
                        st.write(f"🔹 **Version Views:** {stats.get('version_views', 0):,}")
                    with v_col4:
                        st.write(f"🔹 **Version Unique Views:** {stats.get('version_unique_views', 0):,}")
                    
                else:
                    st.error(f"❌ Failed to fetch data. Please check the Record ID. (Status Code: {response.status_code})")
            except Exception as e:
                st.error(f"⚠️ An error occurred while parsing data: {e}")
    else:
        st.warning("⚠️ Invalid format. Please enter a valid Zenodo URL or numerical Record ID.")
