# app.py
# Crowd Issue Reporting & Resolution — Streamlit (Pure Python Frontend)
# Features:
# - Clickable map to drop pin (streamlit-folium + folium)
# - Report issues with category, description, photo upload
# - Filter/search by category/status/text
# - Upvote and mark as resolved
# - Local JSON storage (no backend required)

import os
import json
import uuid
import time
import pathlib
from datetime import datetime

import streamlit as st
import pandas as pd
from PIL import Image

import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import urllib.request
import json as _json
import streamlit.components.v1 as components
from streamlit_javascript import st_javascript

# safe rerun helper to support multiple Streamlit versions
def safe_rerun():
    """
    Try the official API first, fall back to raising Streamlit's RerunException,
    and finally use a session-state flip + st.stop() as a last resort.
    """
    try:
        # preferred (older/newer versions may expose this)
        st.experimental_rerun()
        return
    except Exception:
        pass
    try:
        # internal API that exists on some Streamlit installs
        from streamlit.runtime.scriptrunner import RerunException
        raise RerunException()
    except Exception:
        # last-resort: flip a flag and stop execution so UI can update
        st.session_state["_needs_rerun"] = not st.session_state.get("_needs_rerun", False)
        st.stop()

DATA_DIR = pathlib.Path(__file__).parent
DB_FILE = DATA_DIR / "issues.json"
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CATEGORIES = [
    {"id": "roads", "label": "Pothole / Road Damage", "color": "#ef4444"},
    {"id": "lighting", "label": "Street Light", "color": "#f59e0b"},
    {"id": "waste", "label": "Garbage / Waste", "color": "#10b981"},
    {"id": "water", "label": "Water / Drainage", "color": "#3b82f6"},
    {"id": "safety", "label": "Public Safety", "color": "#8b5cf6"},
    {"id": "other", "label": "Other", "color": "#6b7280"},
]

STATUS = ["open", "in_progress", "resolved"]

def load_data():
    if DB_FILE.exists():
        with open(DB_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_data(items):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def category_meta(cat_id):
    for c in CATEGORIES:
        if c["id"] == cat_id:
            return c
    return CATEGORIES[-1]

# simple in-memory cache to avoid repeated geocoding calls
geocode_cache = {}
def geocode_address(address):
    if not address:
        return None
    key = address.strip()
    if key in geocode_cache:
        return geocode_cache[key]
    try:
        geolocator = Nominatim(user_agent="sih_app")
        geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
        loc = geocode(key)
        if loc:
            coords = (loc.latitude, loc.longitude)
            geocode_cache[key] = coords
            return coords
    except Exception:
        return None
    return None

def ensure_user_id():
    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())
    return st.session_state.user_id

def new_issue_payload(title, description, category, lat, lng, address, image_file):
    user_id = ensure_user_id()
    issue_id = str(uuid.uuid4())
    image_path = None
    if image_file is not None:
        # Save uploaded file
        suffix = pathlib.Path(image_file.name).suffix or ".jpg"
        filename = f"{issue_id}{suffix}"
        image_path = str(UPLOAD_DIR / filename)
        with open(image_path, "wb") as out:
            out.write(image_file.getbuffer())

    return {
        "id": issue_id,
        "title": title.strip(),
        "description": description.strip(),
        "category": category,
        "status": "open",
        "lat": lat,
        "lng": lng,
        "address": (address or "").strip(),
        "image_path": image_path,
        "votes": 0,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "created_by": user_id,
        "updates": [{"ts": int(time.time()*1000), "text": "Issue reported", "status": "open"}],
    }

def add_issue(issue):
    data = load_data()
    data.append(issue)
    save_data(data)

def update_issue(issue_id, patch):
    data = load_data()
    for i, it in enumerate(data):
        if it["id"] == issue_id:
            it.update(patch)
            data[i] = it
            break
    save_data(data)

def upvote_issue(issue_id):
    data = load_data()
    for i, it in enumerate(data):
        if it["id"] == issue_id:
            it["votes"] = int(it.get("votes", 0)) + 1
            data[i] = it
            break
    save_data(data)

def add_update(issue_id, text, status=None):
    data = load_data()
    for i, it in enumerate(data):
        if it["id"] == issue_id:
            updates = it.get("updates", [])
            updates.append({"ts": int(time.time()*1000), "text": text, "status": status or it.get("status","open")})
            it["updates"] = updates
            if status:
                it["status"] = status
            data[i] = it
            break
    save_data(data)

# ----------------- UI -----------------

st.set_page_config(page_title="Crowd Issue Reporting", layout="wide")
st.title("🗺️ Crowd Issue Reporting & Resolution (Python — Streamlit)")

ensure_user_id()

with st.sidebar:
    st.header("Filters")
    cat = st.selectbox("Category", ["all"] + [c["id"] for c in CATEGORIES], format_func=lambda x: dict([(c["id"], c["label"]) for c in CATEGORIES]).get(x, x) if x!="all" else "All")
    stt = st.selectbox("Status", ["all"] + STATUS, format_func=lambda x: x.replace("_"," ").title() if x!="all" else "All")
    q = st.text_input("Search text")
    only_mine = st.checkbox("Only my issues", value=False)
    st.markdown("---")
    st.caption("Tip: Click on the map to place a marker before submitting a report.")

# Default map center
default_center = [12.9716, 77.5946]  # Bengaluru
data = load_data()

# Filtering
def matches(it):
    if cat != "all" and it.get("category") != cat:
        return False
    if stt != "all" and it.get("status") != stt:
        return False
    if only_mine and it.get("created_by") != st.session_state.user_id:
        return False
    if q:
        blob = f"{it.get('title','')} {it.get('description','')} {it.get('address','')}".lower()
        if q.lower() not in blob:
            return False
    return True

filtered = [it for it in data if matches(it)]

# ------------- Map Block -------------
st.subheader("Map")
m = folium.Map(location=default_center, zoom_start=12, control_scale=True, tiles="OpenStreetMap")

# Add current issues as circle markers
for it in filtered:
    # If an existing issue has an address but missing coords, try geocoding once and persist
    if (it.get("lat") in (None, 0) or it.get("lng") in (None, 0)) and it.get("address"):
        geo = geocode_address(it.get("address"))
        if geo:
            it["lat"], it["lng"] = geo
            # persist the improved coords back to storage
            update_issue(it["id"], {"lat": it["lat"], "lng": it["lng"]})
    meta = category_meta(it.get("category"))
    status = it.get("status", "open")
    popup_html = f"""
    <b>{it.get('title','(no title)')}</b><br/>
    Category: {meta['label']}<br/>
    Status: {status.replace('_',' ').title()}<br/>
    Votes: {it.get('votes',0)}<br/>
    {it.get('address','')}
    """
    folium.CircleMarker(
        location=[it.get("lat"), it.get("lng")],
        radius=8,
        color=meta["color"],
        fill=True,
        fill_opacity=0.8,
        popup=popup_html,
    ).add_to(m)

# Enable click-to-add (capture last clicked lat/lng)
map_data = st_folium(m, height=480, width=None, returned_objects=["last_clicked"])

# sync clicked point into session_state so it can be reused across interactions
if map_data and map_data.get("last_clicked"):
    st.session_state.clicked_latlng = [map_data["last_clicked"]["lat"], map_data["last_clicked"]["lng"]]
    st.info(f"Selected position: {st.session_state.clicked_latlng[0]:.6f}, {st.session_state.clicked_latlng[1]:.6f}")

# Provide a "Use my current location" fallback (approx via IP geolocation)
def get_ip_location():
    try:
        with urllib.request.urlopen("http://ip-api.com/json") as resp:
            data = _json.load(resp)
            if data.get("status") == "success":
                return [float(data.get("lat")), float(data.get("lon"))]
    except Exception:
        return None
    
def get_browser_gps():
    """
    Use navigator.geolocation via streamlit-javascript.
    Returns a dict like {'lat': ..., 'lon': ..., 'accuracy': ...} or {'error': '...'}.
    """
    js = """
    async function getPos(){
      return await new Promise((resolve) => {
        if (!navigator.geolocation) { resolve({error: 'Geolocation not supported'}); return; }
        navigator.geolocation.getCurrentPosition(
          (p) => resolve({lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy}),
          (e) => resolve({error: e.message}),
          { enableHighAccuracy: true, timeout: 10000 }
        );
      });
    }
    getPos();
    """
    try:
        res = st_javascript(js, key="gps_js")
        return res or {"error": "no_response"}
    except Exception as e:
        return {"error": str(e)}

if st.button("Use my current location (approx)", key="use_current_loc"):
    loc = get_ip_location()
    if loc:
        st.session_state.clicked_latlng = loc
        st.success(f"Using approximate location: {loc[0]:.6f}, {loc[1]:.6f}")
        safe_rerun()
    else:
        st.warning("Could not determine location via IP. Please click on the map to choose a spot.")

# Browser GPS (high-accuracy, requires user permission in the browser)
if st.button("Get GPS from browser (high accuracy)", key="get_browser_gps"):
    res = get_browser_gps()
    if isinstance(res, dict) and res.get("lat") is not None:
        st.session_state.clicked_latlng = [float(res["lat"]), float(res["lon"])]
        st.success(f"Captured GPS: {res['lat']:.6f}, {res['lon']:.6f} (accuracy: {res.get('accuracy','n/a')})")
        safe_rerun()
    else:
        err = res.get("error") if isinstance(res, dict) else "Unknown error"
        st.warning(f"Could not obtain browser GPS: {err}. Try IP lookup or pick on the map.")

# ------------- Report Form -------------
st.subheader("Report a New Issue")

with st.form("report_form", clear_on_submit=True):
    cols = st.columns(2)
    with cols[0]:
        title = st.text_input("Title *")
        description = st.text_area("Description *", height=120)
        category = st.selectbox("Category *", [c["id"] for c in CATEGORIES], format_func=lambda x: dict([(c["id"], c["label"]) for c in CATEGORIES])[x])
    with cols[1]:
        address = st.text_input("Address (optional)")
        # camera input (mobile / webcam). Available in recent Streamlit versions.
        try:
            camera = st.camera_input("Take a photo (optional)")
        except Exception:
            camera = None
        # fallback file uploader
        photo = st.file_uploader("Attach a photo (optional)", type=["png","jpg","jpeg","webp"])
        # prefer camera capture when present
        image_file = camera if camera is not None else photo
        st.caption("Pick a spot on the map above or click 'Use my current location (approx)'.")
        # use session_state clicked_latlng if present, else last map click, else default center
        selected = st.session_state.get("clicked_latlng", None)
        lat_default = selected[0] if selected else default_center[0]
        lng_default = selected[1] if selected else default_center[1]
        lat = st.number_input("Latitude *", value=lat_default, format="%.6f")
        lng = st.number_input("Longitude *", value=lng_default, format="%.6f")

    submitted = st.form_submit_button("Submit Issue")
    if submitted:
        if not title.strip() or not description.strip():
            st.error("Please fill Title and Description.")
        else:
            # If an address is provided, attempt to geocode it and prefer geocoded coords
            chosen_lat = float(lat)
            chosen_lng = float(lng)
            if address and address.strip():
                geo = geocode_address(address)
                if geo:
                    chosen_lat, chosen_lng = geo
                else:
                    st.warning("Address could not be geocoded; using selected coordinates.")
            # pass captured camera image if available, otherwise uploaded file
            payload = new_issue_payload(title, description, category, chosen_lat, chosen_lng, address, image_file)
            add_issue(payload)
            st.success("Issue submitted!")
            safe_rerun()

# ------------- Issues Table -------------
st.subheader(f"Issues ({len(filtered)} shown)")

if not filtered:
    st.write("No issues match your filters yet.")
else:
    # Show as a compact list with actions
    for it in sorted(filtered, key=lambda x: (x.get("status") != "open", -x.get("votes",0), x.get("created_at","")), reverse=False):
        with st.container():
            top = st.columns([6,2,2,2])
            with top[0]:
                st.markdown(f"**{it['title']}**  \n{it['description']}")
                st.caption(f"Category: {category_meta(it['category'])['label']} • Status: {it['status'].replace('_',' ').title()} • Votes: {it.get('votes',0)}")
                if it.get("address"):
                    st.caption(f"📍 {it['address']}")
                st.caption(f"🕒 Created: {it.get('created_at','')}")
                if it.get("updates"):
                    with st.expander("Updates"):
                        for up in it["updates"]:
                            ts = up.get("ts")
                            when = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M") if ts else ""
                            st.write(f"- [{when}] {up.get('text','')} ({up.get('status','')})")
            with top[1]:
                if it.get("image_path") and os.path.exists(it["image_path"]):
                    st.image(it["image_path"], use_column_width=True, caption="Photo")
                else:
                    st.write("No photo")
            with top[2]:
                if st.button("👍 Upvote", key=f"up_{it['id']}"):
                    upvote_issue(it["id"])
                    safe_rerun()
                if st.button("📝 In Progress", key=f"prog_{it['id']}"):
                    add_update(it["id"], "Marked in progress", status="in_progress")
                    safe_rerun()
            with top[3]:
                if st.button("✅ Resolve", key=f"res_{it['id']}"):
                    add_update(it["id"], "Marked resolved", status="resolved")
                    safe_rerun()
                # Allow owner to delete
                if it.get("created_by") == st.session_state.user_id:
                    if st.button("🗑️ Delete", key=f"del_{it['id']}"):
                        data = load_data()
                        data = [x for x in data if x["id"] != it["id"]]
                        save_data(data)
                        st.success("Issue deleted.")
                        safe_rerun()

st.markdown("---")
st.write("Crowd Issue Reporting & Resolution — Built with ❤️ using Streamlit")
