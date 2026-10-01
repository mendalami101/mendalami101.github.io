import streamlit as st
import requests
import pandas as pd
import sqlite3
import json
from datetime import datetime, timedelta
import pytz
import time
from pathlib import Path

# ====================== KONFIGURASI ======================
DB_PATH = "fb_scheduler.db"
GRAPH_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

st.set_page_config(
    page_title="FB Bulk Scheduler + First Comment",
    page_icon="📅",
    layout="wide"
)

# ====================== DATABASE ======================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT,
            message TEXT,
            scheduled_time TEXT,
            first_comment TEXT,
            links TEXT,
            status TEXT DEFAULT 'scheduled',
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_post(post_id, message, scheduled_time, first_comment, links):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO scheduled_posts (post_id, message, scheduled_time, first_comment, links, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'scheduled', ?)
    """, (post_id, message, scheduled_time, first_comment, json.dumps(links), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_all_posts():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM scheduled_posts ORDER BY id DESC", conn)
    conn.close()
    return df

def update_status(post_id, status):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE scheduled_posts SET status = ? WHERE post_id = ?", (status, post_id))
    conn.commit()
    conn.close()

# ====================== GRAPH API ======================
def schedule_facebook_post(page_id, token, message, scheduled_unix, link=None):
    url = f"{BASE_URL}/{page_id}/feed"
    payload = {
        "message": message,
        "published": "false",
        "scheduled_publish_time": scheduled_unix,
        "access_token": token
    }
    if link:
        payload["link"] = link

    r = requests.post(url, data=payload)
    return r.json()

def post_comment(object_id, token, message):
    url = f"{BASE_URL}/{object_id}/comments"
    payload = {
        "message": message,
        "access_token": token
    }
    r = requests.post(url, data=payload)
    return r.json()

def check_post_published(post_id, token):
    url = f"{BASE_URL}/{post_id}"
    params = {
        "fields": "is_published,created_time,message",
        "access_token": token
    }
    r = requests.get(url, params=params)
    return r.json()

# ====================== UI ======================
def main():
    init_db()

    st.title("📅 Facebook Bulk Scheduler + First Comment")
    st.caption("Jadwalkan post + first comment + balasan link otomatis | Gratis via Graph API")

    # Sidebar - Credentials
    with st.sidebar:
        st.header("🔑 Credentials")
        page_id = st.text_input("Page ID", placeholder="Contoh: 123456789012345")
        access_token = st.text_input("Page Access Token", type="password", placeholder="EAAxxxx...")
        
        st.divider()
        timezone = st.selectbox("Timezone", ["Asia/Jakarta", "UTC", "Asia/Singapore", "America/New_York"])
        tz = pytz.timezone(timezone)
        
        st.divider()
        st.info("Token harus long-lived Page Access Token dengan permission: pages_manage_posts + pages_manage_engagement")

    if not page_id or not access_token:
        st.warning("Masukkan Page ID dan Access Token di sidebar terlebih dahulu.")
        st.stop()

    # Tabs
    tab1, tab2, tab3 = st.tabs(["➕ Bulk Schedule", "📋 Daftar Scheduled", "💬 Proses First Comment"])

    # ========== TAB 1: BULK SCHEDULE ==========
    with tab1:
        st.subheader("Tambah Post Baru (Bulk)")

        # Default empty rows
        if "bulk_data" not in st.session_state:
            st.session_state.bulk_data = pd.DataFrame({
                "Text": [""],
                "Tanggal (YYYY-MM-DD)": [""],
                "Jam (HH:MM)": ["12:00"],
                "First Comment": ["Links below:"],
                "Links (pisahkan dengan | )": [""]
            })

        edited_df = st.data_editor(
            st.session_state.bulk_data,
            num_rows="dynamic",
            use_container_width=True,
            key="bulk_editor"
        )

        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("🚀 Programme / Schedule Semua", type="primary", use_container_width=True):
                success_count = 0
                error_list = []

                progress = st.progress(0)
                status_text = st.empty()

                for idx, row in edited_df.iterrows():
                    text = str(row["Text"]).strip()
                    tanggal = str(row["Tanggal (YYYY-MM-DD)"]).strip()
                    jam = str(row["Jam (HH:MM)"]).strip()
                    first_comment = str(row["First Comment"]).strip()
                    links_raw = str(row["Links (pisahkan dengan | )"]).strip()

                    if not text or not tanggal:
                        continue

                    try:
                        # Parse datetime
                        dt_str = f"{tanggal} {jam}"
                        local_dt = tz.localize(datetime.strptime(dt_str, "%Y-%m-%d %H:%M"))
                        unix_ts = int(local_dt.timestamp())

                        # Validasi waktu (min 10 menit, max 30 hari)
                        now = datetime.now(tz)
                        if local_dt < now + timedelta(minutes=10):
                            error_list.append(f"Baris {idx+1}: Waktu terlalu dekat (< 10 menit)")
                            continue
                        if local_dt > now + timedelta(days=30):
                            error_list.append(f"Baris {idx+1}: Waktu lebih dari 30 hari")
                            continue

                        # Schedule
                        result = schedule_facebook_post(page_id, access_token, text, unix_ts)

                        if "id" in result:
                            post_id = result["id"]
                            links = [l.strip() for l in links_raw.split("|") if l.strip()] if links_raw else []
                            save_post(post_id, text, local_dt.isoformat(), first_comment, links)
                            success_count += 1
                            status_text.success(f"✅ Berhasil: {post_id}")
                        else:
                            error_list.append(f"Baris {idx+1}: {result.get('error', {}).get('message', str(result))}")

                    except Exception as e:
                        error_list.append(f"Baris {idx+1}: {str(e)}")

                    progress.progress((idx + 1) / len(edited_df))

                st.success(f"Selesai! {success_count} post berhasil dijadwalkan.")
                if error_list:
                    st.error("Error:")
                    for e in error_list:
                        st.write(f"- {e}")

                # Reset form
                st.session_state.bulk_data = pd.DataFrame({
                    "Text": [""],
                    "Tanggal (YYYY-MM-DD)": [""],
                    "Jam (HH:MM)": ["12:00"],
                    "First Comment": ["Links below:"],
                    "Links (pisahkan dengan | )": [""]
                })
                st.rerun()

        with col2:
            st.caption("Isi tabel di atas, lalu klik tombol Schedule. Links dipisahkan dengan tanda | (contoh: https://link1.com | https://link2.com)")

    # ========== TAB 2: DAFTAR ==========
    with tab2:
        st.subheader("Daftar Post yang Sudah Dijadwalkan")
        df = get_all_posts()
        if df.empty:
            st.info("Belum ada post yang dijadwalkan.")
        else:
            # Format tampilan
            display_df = df[["id", "post_id", "message", "scheduled_time", "first_comment", "status"]].copy()
            display_df["links"] = df["links"].apply(lambda x: " | ".join(json.loads(x)) if x else "")
            st.dataframe(display_df, use_container_width=True)

            if st.button("🔄 Refresh"):
                st.rerun()

    # ========== TAB 3: PROSES COMMENT ==========
    with tab3:
        st.subheader("Proses First Comment + Balasan Link")
        st.write("Klik tombol di bawah untuk mengecek post yang sudah live dan menambahkan first comment + balasan link.")

        if st.button("💬 Proses Semua Pending Comments", type="primary"):
            df = get_all_posts()
            pending = df[df["status"] == "scheduled"]

            if pending.empty:
                st.info("Tidak ada post pending.")
            else:
                progress = st.progress(0)
                for i, row in pending.iterrows():
                    post_id = row["post_id"]
                    first_comment = row["first_comment"]
                    links = json.loads(row["links"]) if row["links"] else []

                    # Cek apakah sudah published
                    check = check_post_published(post_id, access_token)

                    if check.get("is_published") or "created_time" in check:
                        # Post first comment
                        res1 = post_comment(post_id, access_token, first_comment)
                        if "id" in res1:
                            comment_id = res1["id"]
                            st.write(f"✅ First comment berhasil: {post_id}")

                            # Balasan link
                            for link in links:
                                time.sleep(1.5)  # delay biar aman
                                res2 = post_comment(comment_id, access_token, link)
                                if "id" in res2:
                                    st.write(f"   ↳ Link ditambahkan: {link[:50]}...")
                                else:
                                    st.warning(f"   Gagal link: {res2}")

                            update_status(post_id, "commented")
                        else:
                            st.error(f"Gagal first comment {post_id}: {res1}")
                    else:
                        st.write(f"⏳ Belum live: {post_id}")

                    progress.progress((i + 1) / len(pending))

                st.success("Proses selesai!")
                st.rerun()

        st.divider()
        st.caption("Tips: Jalankan tombol ini beberapa menit setelah waktu schedule post, atau buat cron job yang memanggil script worker terpisah.")

if __name__ == "__main__":
    main()