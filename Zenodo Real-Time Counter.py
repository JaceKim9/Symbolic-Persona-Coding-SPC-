import streamlit as st
import requests
import re

# Set web app configuration
st.set_config(page_title="Zenodo Real-Time Counter", page_icon="📊", layout="centered")

st.title("📊 Zenodo Real-Time Counter")
st.markdown("Fetch the **actual primary metrics** directly from Zenodo's internal API, bypassing web interface lag or display issues.")

# User input field
url_input = st.text_input(
    "Enter Zenodo Record URL or Record ID:",
    placeholder="e.g., https://zenodo.org/records/18813202 or 18813202"
)

if url_input:
    # [수정] Zenodo URL 구조(/records/숫자) 또는 완전히 독립된 숫자만 매칭하도록 정규식 강화
    record_id = None
    
    # 1. URL 형태인 경우 '/records/숫자' 패턴에서 숫자만 추출
    url_match = re.search(r'records/(\d+)', url_input)
    if url_match:
        record_id = url_match.group(1)
    else:
        # 2. 주소가 아니고 그냥 숫자만 입력한 경우 (6~9자리 레코드 번호)
        pure_digits = re.sub(r'\D', '', url_input) # 숫자가 아닌 모든 문자 제거
        if pure_digits:
            record_id = pure_digits

    if record_id:
        api_url = f"https://zenodo.org/api/records/{record_id}"
        
        with st.spinner("Fetching live tracking data from Zenodo server..."):
            try:
                response = requests.get(api_url)
                
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
