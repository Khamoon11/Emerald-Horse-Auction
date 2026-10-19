"""
Virtual Horse Auction App
- Multi-user Streamlit application (global via public hosting)
- Upload horse screenshots, extract/edit stats (Health, Stamina, Courage, Speed, Acceleration)
- Display radar charts
- Timed auctions with bidding
- Minimum bid increment: $25
- Reserve price support
- Listing tab protected by password
- Discord webhook notifications for new listings and new high bids
"""

import streamlit as st
import sqlite3
import os
from datetime import datetime, timedelta
from PIL import Image
import pytesseract
import plotly.graph_objects as go
import re
import uuid
import requests
from typing import Optional

# ----------------------------
# Configuration
# ----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "auction.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

MIN_BID_INCREMENT = 25.0  # $25 minimum raise
LISTING_PASSWORD = "EmeLay193"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_discord_webhook() -> Optional[str]:
    try:
        if "DISCORD_WEBHOOK_URL" in st.secrets:
            return st.secrets["DISCORD_WEBHOOK_URL"]
    except Exception:
        pass
    return os.environ.get("DISCORD_WEBHOOK_URL")


# ----------------------------
# Database helpers
# ----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS horses (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            health REAL,
            stamina REAL,
            courage REAL,
            speed REAL,
            acceleration REAL,
            image_path TEXT,
            description TEXT,
            seller TEXT,
            start_time TEXT,
            end_time TEXT,
            current_bid REAL DEFAULT 0,
            highest_bidder TEXT,
            reserve_price REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT
        )
        """
    )
    # Ensure reserve_price exists on older databases
    cols = [r[1] for r in c.execute("PRAGMA table_info(horses)").fetchall()]
    if "reserve_price" not in cols:
        c.execute("ALTER TABLE horses ADD COLUMN reserve_price REAL DEFAULT 0")

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            horse_id TEXT,
            bidder TEXT,
            amount REAL,
            bid_time TEXT,
            FOREIGN KEY (horse_id) REFERENCES horses(id)
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


# ----------------------------
# Discord notifications
# ----------------------------
def send_discord_new_listing(horse: dict, public_url: str = ""):
    webhook = get_discord_webhook()
    if not webhook:
        return False

    stats_text = (
        f"**Health:** {horse.get('health', 0):.0f}  |  "
        f"**Stamina:** {horse.get('stamina', 0):.0f}  |  "
        f"**Courage:** {horse.get('courage', 0):.0f}\n"
        f"**Speed:** {horse.get('speed', 0):.0f}  |  "
        f"**Acceleration:** {horse.get('acceleration', 0):.0f}"
    )

    end_time = horse.get("end_time", "")
    try:
        end_dt = datetime.fromisoformat(end_time)
        end_str = end_dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        end_str = end_time

    reserve = horse.get("reserve_price", 0) or 0
    reserve_text = f"${reserve:.2f}" if reserve > 0 else "None"

    embed = {
        "title": f"🐴 New Horse Listed: {horse.get('name', 'Unknown')}",
        "description": horse.get("description") or "No description provided.",
        "color": 0x1F77B4,
        "fields": [
            {"name": "Seller", "value": horse.get("seller", "—"), "inline": True},
            {
                "name": "Starting Bid",
                "value": f"${horse.get('current_bid', 0):.2f}",
                "inline": True,
            },
            {"name": "Reserve", "value": reserve_text, "inline": True},
            {"name": "Auction Ends", "value": end_str, "inline": True},
            {"name": "Stats", "value": stats_text, "inline": False},
        ],
        "footer": {"text": "Virtual Horse Auction"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    if public_url:
        embed["url"] = public_url
        embed["fields"].append(
            {
                "name": "View & Bid",
                "value": f"[Open Auction]({public_url})",
                "inline": False,
            }
        )

    payload = {
        "content": "@here A new horse has entered the auction house!",
        "embeds": [embed],
    }

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        return resp.status_code in (200, 204)
    except Exception:
        return False


def send_discord_new_bid(horse_name: str, bidder: str, amount: float, previous_bid: float):
    webhook = get_discord_webhook()
    if not webhook:
        return False

    embed = {
        "title": f"💰 New Bid on {horse_name}",
        "color": 0x2ECC71,
        "fields": [
            {"name": "Bidder", "value": bidder, "inline": True},
            {"name": "Amount", "value": f"${amount:.2f}", "inline": True},
            {"name": "Previous Bid", "value": f"${previous_bid:.2f}", "inline": True},
        ],
        "footer": {"text": "Virtual Horse Auction"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    payload = {"embeds": [embed]}
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        return resp.status_code in (200, 204)
    except Exception:
        return False


# ----------------------------
# OCR / Stats extraction
# ----------------------------
def extract_stats_from_image(image: Image.Image) -> tuple[dict, str]:
    if image.mode != "RGB":
        image = image.convert("RGB")

    text = pytesseract.image_to_string(image)
    text_lower = text.lower()

    stats = {
        "health": 0.0,
        "stamina": 0.0,
        "courage": 0.0,
        "speed": 0.0,
        "acceleration": 0.0,
    }

    patterns = {
        "health": r"(?:health|hp|hit\s*points?)[:\s]*(\d+(?:\.\d+)?)",
        "stamina": r"(?:stamina|stam|endurance)[:\s]*(\d+(?:\.\d+)?)",
        "courage": r"(?:courage|brave|bravery)[:\s]*(\d+(?:\.\d+)?)",
        "speed": r"(?:speed|spd|velocity)[:\s]*(\d+(?:\.\d+)?)",
        "acceleration": r"(?:acceleration|accel|acc)[:\s]*(\d+(?:\.\d+)?)",
    }

    for key, pat in patterns.items():
        match = re.search(pat, text_lower, re.IGNORECASE)
        if match:
            try:
                stats[key] = float(match.group(1))
            except ValueError:
                pass

    return stats, text


# ----------------------------
# Radar chart
# ----------------------------
def create_radar_chart(stats: dict, title: str = "Horse Stats"):
    categories = ["Health", "Stamina", "Courage", "Speed", "Acceleration"]
    values = [
        stats.get("health", 0),
        stats.get("stamina", 0),
        stats.get("courage", 0),
        stats.get("speed", 0),
        stats.get("acceleration", 0),
    ]
    values += values[:1]
    categories += categories[:1]

    fig = go.Figure(
        data=[
            go.Scatterpolar(
                r=values,
                theta=categories,
                fill="toself",
                name="Stats",
                line_color="#1f77b4",
                fillcolor="rgba(31, 119, 180, 0.3)",
            )
        ]
    )
    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, max(100, max(values) * 1.1 if any(values) else 100)],
            )
        ),
        showlegend=False,
        title=dict(text=title, x=0.5),
        margin=dict(l=40, r=40, t=60, b=40),
        height=400,
    )
    return fig


# ----------------------------
# Auction logic
# ----------------------------
def get_active_horses():
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    c = conn.cursor()

    # Close expired auctions and set final status based on reserve
    expired = c.execute(
        "SELECT id, current_bid, reserve_price FROM horses WHERE status = 'active' AND end_time < ?",
        (now,),
    ).fetchall()
    for row in expired:
        final_status = "sold"
        if (row["reserve_price"] or 0) > 0 and (row["current_bid"] or 0) < row["reserve_price"]:
            final_status = "reserve_not_met"
        c.execute(
            "UPDATE horses SET status = ? WHERE id = ?",
            (final_status, row["id"]),
        )
    conn.commit()

    rows = c.execute(
        """
        SELECT * FROM horses
        WHERE status = 'active'
        ORDER BY end_time ASC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_horses(include_sold=True):
    conn = get_conn()
    if include_sold:
        rows = conn.execute(
            "SELECT * FROM horses ORDER BY created_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM horses WHERE status = 'active' ORDER BY end_time ASC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def place_bid(horse_id: str, bidder: str, amount: float) -> tuple[bool, str]:
    conn = get_conn()
    c = conn.cursor()
    horse = c.execute("SELECT * FROM horses WHERE id = ?", (horse_id,)).fetchone()
    if not horse:
        conn.close()
        return False, "Horse not found."
    horse = dict(horse)

    now = datetime.utcnow()
    end_time = datetime.fromisoformat(horse["end_time"])
    if horse["status"] != "active" or now > end_time:
        conn.close()
        return False, "Auction has ended."

    min_required = horse["current_bid"] + MIN_BID_INCREMENT
    if amount < min_required:
        conn.close()
        return (
            False,
            f"Bid must be at least ${min_required:.2f} "
            f"(current ${horse['current_bid']:.2f} + ${MIN_BID_INCREMENT:.0f} minimum increment).",
        )

    previous_bid = horse["current_bid"]

    c.execute(
        "INSERT INTO bids (horse_id, bidder, amount, bid_time) VALUES (?, ?, ?, ?)",
        (horse_id, bidder, amount, now.isoformat()),
    )
    c.execute(
        "UPDATE horses SET current_bid = ?, highest_bidder = ? WHERE id = ?",
        (amount, bidder, horse_id),
    )
    conn.commit()
    conn.close()

    try:
        send_discord_new_bid(horse["name"], bidder, amount, previous_bid)
    except Exception:
        pass

    return True, f"Bid of ${amount:.2f} placed successfully!"


def create_listing(
    name,
    stats,
    image_path,
    description,
    seller,
    duration_hours,
    starting_bid=0.0,
    reserve_price=0.0,
):
    horse_id = str(uuid.uuid4())
    now = datetime.utcnow()
    end_time = now + timedelta(hours=duration_hours)

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO horses (
            id, name, health, stamina, courage, speed, acceleration,
            image_path, description, seller, start_time, end_time,
            current_bid, highest_bidder, reserve_price, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            horse_id,
            name,
            stats["health"],
            stats["stamina"],
            stats["courage"],
            stats["speed"],
            stats["acceleration"],
            image_path,
            description,
            seller,
            now.isoformat(),
            end_time.isoformat(),
            starting_bid,
            None,
            reserve_price,
            "active",
            now.isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    horse_dict = {
        "id": horse_id,
        "name": name,
        "health": stats["health"],
        "stamina": stats["stamina"],
        "courage": stats["courage"],
        "speed": stats["speed"],
        "acceleration": stats["acceleration"],
        "description": description,
        "seller": seller,
        "end_time": end_time.isoformat(),
        "current_bid": starting_bid,
        "reserve_price": reserve_price,
    }

    try:
        public_url = ""
        try:
            public_url = st.get_option("server.baseUrlPath") or ""
        except Exception:
            pass
        send_discord_new_listing(horse_dict, public_url=public_url)
    except Exception:
        pass

    return horse_id


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(
    page_title="Virtual Horse Auction",
    page_icon="🐴",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🐴 Virtual Horse Auction")
st.caption(
    f"Upload screenshots → extract stats → radar chart → timed bidding  |  "
    f"Minimum bid increment: **${MIN_BID_INCREMENT:.0f}**"
)

# Sidebar
with st.sidebar:
    st.header("Your Identity")
    if "username" not in st.session_state:
        st.session_state.username = ""
    username = st.text_input(
        "Enter your display name (required for bidding)",
        value=st.session_state.username,
        key="username_input",
    )
    if username.strip():
        st.session_state.username = username.strip()
        st.success(f"Bidding as: **{st.session_state.username}**")
    else:
        st.warning("Set a name to place bids.")

    st.divider()
    st.header("Navigation")
    page = st.radio(
        "Go to",
        ["🏟️ Active Auctions", "➕ List a Horse (password)", "📜 All Listings / History"],
        label_visibility="collapsed",
    )

    st.divider()
    st.header("Discord")
    webhook = get_discord_webhook()
    if webhook:
        st.success("Discord webhook connected ✅")
        st.caption("New listings and high bids are posted to your channel.")
    else:
        st.info(
            "No Discord webhook set.\n\n"
            "Add `DISCORD_WEBHOOK_URL` to Streamlit secrets "
            "or as an environment variable."
        )


# ----------------------------
# Page: List a Horse (password protected)
# ----------------------------
if page == "➕ List a Horse (password)":
    st.header("List a New Horse for Auction")

    # Password gate
    if "listing_authenticated" not in st.session_state:
        st.session_state.listing_authenticated = False

    if not st.session_state.listing_authenticated:
        st.warning("This section is password protected.")
        pw = st.text_input("Enter listing password", type="password", key="listing_pw")
        if st.button("Unlock Listing"):
            if pw == LISTING_PASSWORD:
                st.session_state.listing_authenticated = True
                st.success("Access granted.")
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()

    # Authenticated – show listing form
    st.success("Listing access unlocked for this session.")
    if st.button("Lock listing page again"):
        st.session_state.listing_authenticated = False
        st.rerun()

    if not st.session_state.username:
        st.warning("Please set your display name in the sidebar first.")
        st.stop()

    col1, col2 = st.columns([1, 1])

    with col1:
        uploaded_file = st.file_uploader(
            "Upload in-game screenshot of the horse",
            type=["png", "jpg", "jpeg", "webp", "bmp"],
            help="OCR will try to read the five stats. You can correct them.",
        )

        name = st.text_input("Horse Name *", placeholder="e.g. Thunderbolt")
        description = st.text_area("Description (optional)", height=80)
        duration = st.number_input(
            "Auction duration (hours)",
            min_value=0.25,
            max_value=168.0,
            value=24.0,
            step=0.25,
        )
        starting_bid = st.number_input(
            "Starting price ($)",
            min_value=0.0,
            value=100.0,
            step=25.0,
            help="First bid must be at least this amount. Later bids raise by at least $25.",
        )
        reserve_price = st.number_input(
            "Reserve price ($)",
            min_value=0.0,
            value=0.0,
            step=25.0,
            help="If the final bid is below this amount the horse will not sell (status: reserve not met). Set to 0 for no reserve.",
        )

    stats = {
        "health": 0.0,
        "stamina": 0.0,
        "courage": 0.0,
        "speed": 0.0,
        "acceleration": 0.0,
    }
    raw_ocr = ""
    preview_img = None

    with col2:
        if uploaded_file is not None:
            preview_img = Image.open(uploaded_file)
            st.image(preview_img, caption="Uploaded screenshot", use_container_width=True)

            with st.spinner("Running OCR to extract stats..."):
                stats, raw_ocr = extract_stats_from_image(preview_img)

            st.subheader("Extracted Stats (edit if needed)")
            stats["health"] = st.number_input(
                "Health", value=float(stats["health"]), min_value=0.0, step=0.1
            )
            stats["stamina"] = st.number_input(
                "Stamina", value=float(stats["stamina"]), min_value=0.0, step=0.1
            )
            stats["courage"] = st.number_input(
                "Courage", value=float(stats["courage"]), min_value=0.0, step=0.1
            )
            stats["speed"] = st.number_input(
                "Speed", value=float(stats["speed"]), min_value=0.0, step=0.1
            )
            stats["acceleration"] = st.number_input(
                "Acceleration",
                value=float(stats["acceleration"]),
                min_value=0.0,
                step=0.1,
            )

            with st.expander("Raw OCR text (for debugging)"):
                st.code(raw_ocr or "(no text detected)")

            st.subheader("Radar Chart Preview")
            fig = create_radar_chart(stats, title=name or "New Horse")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Upload a screenshot to auto-extract stats and see the radar chart.")

    if st.button(
        "🚀 Create Auction Listing",
        type="primary",
        disabled=not (name and uploaded_file),
    ):
        ext = os.path.splitext(uploaded_file.name)[1].lower() or ".png"
        filename = f"{uuid.uuid4().hex}{ext}"
        image_path = os.path.join(UPLOAD_DIR, filename)
        preview_img.save(image_path)

        horse_id = create_listing(
            name=name,
            stats=stats,
            image_path=image_path,
            description=description,
            seller=st.session_state.username,
            duration_hours=duration,
            starting_bid=starting_bid,
            reserve_price=reserve_price,
        )
        msg = (
            f"Horse **{name}** listed successfully! "
            f"Starting ${starting_bid:.2f}"
        )
        if reserve_price > 0:
            msg += f" · Reserve ${reserve_price:.2f}"
        msg += f" · Ends in {duration} hours."
        st.success(msg)
        if get_discord_webhook():
            st.info("Discord notification sent.")
        st.balloons()
        st.rerun()


# ----------------------------
# Page: Active Auctions
# ----------------------------
elif page == "🏟️ Active Auctions":
    st.header("Active Auctions")
    horses = get_active_horses()

    if not horses:
        st.info("No active auctions right now. Be the first to list a horse!")
    else:
        for h in horses:
            end_dt = datetime.fromisoformat(h["end_time"])
            remaining = end_dt - datetime.utcnow()
            if remaining.total_seconds() < 0:
                remaining_str = "Ended"
            else:
                hours, rem = divmod(int(remaining.total_seconds()), 3600)
                mins, secs = divmod(rem, 60)
                remaining_str = f"{hours}h {mins}m {secs}s"

            reserve = h.get("reserve_price") or 0
            reserve_met = (h["current_bid"] or 0) >= reserve if reserve > 0 else True

            with st.container(border=True):
                c1, c2, c3 = st.columns([1.2, 1.5, 1])

                with c1:
                    if h["image_path"] and os.path.exists(h["image_path"]):
                        st.image(h["image_path"], use_container_width=True)
                    else:
                        st.write("(no image)")

                with c2:
                    st.subheader(h["name"])
                    st.caption(
                        f"Seller: {h['seller']} · Ends: {end_dt.strftime('%Y-%m-%d %H:%M UTC')} "
                        f"({remaining_str} left)"
                    )
                    if h.get("description"):
                        st.write(h["description"])

                    if reserve > 0:
                        if reserve_met:
                            st.success("Reserve met ✓")
                        else:
                            st.warning("Reserve not yet met")

                    stats = {
                        "health": h["health"] or 0,
                        "stamina": h["stamina"] or 0,
                        "courage": h["courage"] or 0,
                        "speed": h["speed"] or 0,
                        "acceleration": h["acceleration"] or 0,
                    }
                    fig = create_radar_chart(stats, title="")
                    st.plotly_chart(fig, use_container_width=True, key=f"radar_{h['id']}")

                with c3:
                    st.metric("Current Bid", f"${h['current_bid']:.2f}")
                    if h["highest_bidder"]:
                        st.caption(f"Highest bidder: {h['highest_bidder']}")
                    else:
                        st.caption("No bids yet")

                    min_next = h["current_bid"] + MIN_BID_INCREMENT
                    st.caption(f"Minimum next bid: **${min_next:.2f}**")

                    if st.session_state.username:
                        bid_amount = st.number_input(
                            "Your bid ($)",
                            min_value=min_next,
                            value=min_next,
                            step=MIN_BID_INCREMENT,
                            key=f"bid_{h['id']}",
                        )
                        if st.button("Place Bid", key=f"btn_{h['id']}", type="primary"):
                            ok, msg = place_bid(
                                h["id"], st.session_state.username, bid_amount
                            )
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
                    else:
                        st.info("Set your name in the sidebar to bid.")


# ----------------------------
# Page: All Listings / History
# ----------------------------
else:
    st.header("All Listings & History")
    show_sold = st.checkbox("Include finished auctions", value=True)
    horses = get_all_horses(include_sold=show_sold)

    if not horses:
        st.info("No listings yet.")
    else:
        for h in horses:
            status = h["status"]
            if status == "active":
                status_emoji = "🟢"
            elif status == "sold":
                status_emoji = "🔴 Sold"
            elif status == "reserve_not_met":
                status_emoji = "⚪ Reserve not met"
            else:
                status_emoji = "⚫"

            end_dt = datetime.fromisoformat(h["end_time"])
            reserve = h.get("reserve_price") or 0

            with st.expander(
                f"{status_emoji} {h['name']} — ${h['current_bid']:.2f} — "
                f"Ends {end_dt.strftime('%Y-%m-%d %H:%M')}"
            ):
                cols = st.columns([1, 2])
                with cols[0]:
                    if h["image_path"] and os.path.exists(h["image_path"]):
                        st.image(h["image_path"], use_container_width=True)
                with cols[1]:
                    st.write(f"**Seller:** {h['seller']}")
                    st.write(f"**Status:** {status}")
                    st.write(f"**Highest bidder:** {h['highest_bidder'] or '—'}")
                    st.write(f"**Starting / Current:** ${h['current_bid']:.2f}")
                    if reserve > 0:
                        st.write(f"**Reserve:** ${reserve:.2f}")
                    st.write(f"**Description:** {h.get('description') or '—'}")
                    stats = {
                        "health": h["health"] or 0,
                        "stamina": h["stamina"] or 0,
                        "courage": h["courage"] or 0,
                        "speed": h["speed"] or 0,
                        "acceleration": h["acceleration"] or 0,
                    }
                    fig = create_radar_chart(stats, title=h["name"])
                    st.plotly_chart(
                        fig, use_container_width=True, key=f"hist_radar_{h['id']}"
                    )


st.divider()
st.caption(
    "Global multi-user auction · Min bid increment $25 · Reserve prices · "
    "Listing protected by password · Discord notifications supported."
)
