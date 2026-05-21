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
    # [보완] 입력값 앞뒤의 무의미한 공백이나 줄바꿈 제거
    clean_input = url_input.strip()
    record_id = None
    
    # 1. Check if the input is a full Zenodo URL containing '/records/ID'
    url_match = re.search(r'records/(\d+)', clean_input)
    if url_match:
        record_id = url_match.group(1)
    else:
        # 2. Extract only digits if the user just typed the raw Record ID
        pure_digits = re.sub(r'\D', '', clean_input)
        if pure_digits:
            record_id = pure_digits

    if record_id:
        api_url = f"https://zenodo.org/api/records/{record_id}"
        
        with st.spinner("Fetching live tracking data from Zenodo server..."):
            try:
                # [보완] 요청 헤더에 일반 브라우저처럼 보이도록 User-Agent 추가 (차단 방지)
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                response = requests.get(api_url, headers=headers)
                
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
                    
                # [보완] 404 에러 시 조금 더 구체적인 원인 안내
                elif response.status_code == 404:
                    st.error(f"❌ Record Not Found (404). The ID `{record_id}` does not exist on Zenodo yet, or the API sync is lagging. Please double-check your link.")
                else:
                    st.error(f"❌ Failed to fetch data. (Status Code: {response.status_code})")
            except Exception as e:
                st.error(f"⚠️ An error occurred while parsing data: {e}")
    else:
        st.warning("⚠️ Invalid format. Please enter a valid Zenodo URL or numerical Record ID.")
