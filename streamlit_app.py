"""
Emerald Ranch Auction
- Multi-user Streamlit application
- Manual entry of horse stats + screenshot for display
- Stats (out of 10): Health, Stamina, Courage, Agility, Speed, Acceleration
- Radar chart, timed auctions, $25 min increment, reserve price
- Listing password protected, Discord webhooks
- Bid history per horse, de-list button, live refresh
- Custom background + western title font
"""

import streamlit as st
import sqlite3
import os
import base64
from datetime import datetime, timedelta
from PIL import Image
import plotly.graph_objects as go
import uuid
import requests
from typing import Optional, List

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

# ----------------------------
# Configuration
# ----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "auction.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
BG_PATH = os.path.join(BASE_DIR, "assets", "background.jpg")

MIN_BID_INCREMENT = 25.0
LISTING_PASSWORD = "EmeLay193"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "assets"), exist_ok=True)


def get_discord_webhook() -> Optional[str]:
    try:
        if "DISCORD_WEBHOOK_URL" in st.secrets:
            return st.secrets["DISCORD_WEBHOOK_URL"]
    except Exception:
        pass
    return os.environ.get("DISCORD_WEBHOOK_URL")


def get_bg_base64() -> str:
    if os.path.exists(BG_PATH):
        with open(BG_PATH, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""


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
            agility REAL,
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
    cols = [r[1] for r in c.execute("PRAGMA table_info(horses)").fetchall()]
    if "reserve_price" not in cols:
        c.execute("ALTER TABLE horses ADD COLUMN reserve_price REAL DEFAULT 0")
    if "agility" not in cols:
        c.execute("ALTER TABLE horses ADD COLUMN agility REAL DEFAULT 0")

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

    # Order: Health, Stamina, Courage, Agility, Speed, Acceleration
    stats_text = (
        f"**Health:** {horse.get('health', 0):.0f}/10  |  "
        f"**Stamina:** {horse.get('stamina', 0):.0f}/10  |  "
        f"**Courage:** {horse.get('courage', 0):.0f}/10\n"
        f"**Agility:** {horse.get('agility', 0):.0f}/10  |  "
        f"**Speed:** {horse.get('speed', 0):.0f}/10  |  "
        f"**Acceleration:** {horse.get('acceleration', 0):.0f}/10"
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
        "color": 0x2E7D32,
        "fields": [
            {"name": "Seller", "value": horse.get("seller", "—"), "inline": True},
            {"name": "Starting Bid", "value": f"${horse.get('current_bid', 0):.2f}", "inline": True},
            {"name": "Reserve", "value": reserve_text, "inline": True},
            {"name": "Auction Ends", "value": end_str, "inline": True},
            {"name": "Stats", "value": stats_text, "inline": False},
        ],
        "footer": {"text": "Emerald Ranch Auction"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    if public_url:
        embed["url"] = public_url
        embed["fields"].append(
            {"name": "View & Bid", "value": f"[Open Auction]({public_url})", "inline": False}
        )

    payload = {
        "content": "@here A new horse has entered Emerald Ranch Auction!",
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
        "footer": {"text": "Emerald Ranch Auction"},
        "timestamp": datetime.utcnow().isoformat(),
    }

    payload = {"embeds": [embed]}
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        return resp.status_code in (200, 204)
    except Exception:
        return False


# ----------------------------
# Radar chart – order: Health, Stamina, Courage, Agility, Speed, Acceleration
# ----------------------------
def create_radar_chart(stats: dict, title: str = "Horse Stats"):
    categories = ["Health", "Stamina", "Courage", "Agility", "Speed", "Acceleration"]
    values = [
        stats.get("health", 0),
        stats.get("stamina", 0),
        stats.get("courage", 0),
        stats.get("agility", 0),
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
                line_color="#1b5e20",
                fillcolor="rgba(46, 125, 50, 0.35)",
            )
        ]
    )
    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 10],
                tickvals=[0, 2, 4, 6, 8, 10],
            )
        ),
        showlegend=False,
        title=dict(text=title, x=0.5),
        margin=dict(l=40, r=40, t=60, b=40),
        height=420,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#f5f5f5"),
    )
    return fig


# ----------------------------
# Auction logic
# ----------------------------
def get_active_horses():
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    c = conn.cursor()

    expired = c.execute(
        "SELECT id, current_bid, reserve_price FROM horses WHERE status = 'active' AND end_time < ?",
        (now,),
    ).fetchall()
    for row in expired:
        final_status = "sold"
        if (row["reserve_price"] or 0) > 0 and (row["current_bid"] or 0) < row["reserve_price"]:
            final_status = "reserve_not_met"
        c.execute("UPDATE horses SET status = ? WHERE id = ?", (final_status, row["id"]))
    conn.commit()

    rows = c.execute(
        "SELECT * FROM horses WHERE status = 'active' ORDER BY end_time ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_horses(include_sold=True):
    conn = get_conn()
    if include_sold:
        rows = conn.execute("SELECT * FROM horses ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM horses WHERE status = 'active' ORDER BY end_time ASC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bid_history(horse_id: str) -> List[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT bidder, amount, bid_time FROM bids WHERE horse_id = ? ORDER BY amount DESC, bid_time DESC",
        (horse_id,),
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


def cancel_listing(horse_id: str) -> tuple[bool, str]:
    conn = get_conn()
    c = conn.cursor()
    horse = c.execute("SELECT * FROM horses WHERE id = ?", (horse_id,)).fetchone()
    if not horse:
        conn.close()
        return False, "Horse not found."
    if horse["status"] != "active":
        conn.close()
        return False, "This auction is no longer active."
    c.execute("UPDATE horses SET status = 'cancelled' WHERE id = ?", (horse_id,))
    conn.commit()
    conn.close()
    return True, "Horse has been de-listed / cancelled."


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
            id, name, health, stamina, courage, speed, acceleration, agility,
            image_path, description, seller, start_time, end_time,
            current_bid, highest_bidder, reserve_price, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            horse_id,
            name,
            stats["health"],
            stats["stamina"],
            stats["courage"],
            stats["speed"],
            stats["acceleration"],
            stats["agility"],
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
        "agility": stats["agility"],
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
# Streamlit UI + Custom CSS
# ----------------------------
st.set_page_config(
    page_title="Emerald Ranch Auction",
    page_icon="🐴",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Background + western font
bg_b64 = get_bg_base64()
st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Rye&family=Cinzel:wght@600;700&display=swap');

    .stApp {{
        background-image: linear-gradient(rgba(0,0,0,0.45), rgba(0,0,0,0.55)),
                          url("data:image/jpeg;base64,{bg_b64}");
        background-size: cover;
        background-position: center;
        background-attachment: fixed;
        background-repeat: no-repeat;
    }}

    [data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: rgba(20, 20, 20, 0.72) !important;
        border-radius: 12px;
        padding: 0.5rem;
    }}

    h1.emerald-title {{
        font-family: 'Rye', 'Cinzel', serif !important;
        font-size: 2.8rem !important;
        color: #f5e6c8 !important;
        text-align: center;
        text-shadow: 2px 2px 4px #000, 0 0 12px rgba(0,0,0,0.8);
        letter-spacing: 0.04em;
        margin-bottom: 0.2rem;
    }}

    .stCaption, .stMarkdown, label, p, span {{
        color: #f0f0f0 !important;
    }}

    [data-testid="stSidebar"] {{
        background-color: rgba(15, 25, 15, 0.92) !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<h1 class="emerald-title">Emerald Ranch Auction</h1>', unsafe_allow_html=True)
st.caption(
    f"Manual stats (out of 10) · Radar chart · Timed bidding  |  "
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
            "Upload screenshot / photo of the horse (for display)",
            type=["png", "jpg", "jpeg", "webp", "bmp"],
            help="This image is shown on the auction page. Stats are entered manually below.",
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
            help="If the final bid is below this amount the horse will not sell. Set to 0 for no reserve.",
        )

        st.subheader("Horse Stats (out of 10)")
        # Order: Health, Stamina, Courage, Agility, Speed, Acceleration
        health = st.number_input("Health", min_value=0.0, max_value=10.0, value=5.0, step=0.5)
        stamina = st.number_input("Stamina", min_value=0.0, max_value=10.0, value=5.0, step=0.5)
        courage = st.number_input("Courage", min_value=0.0, max_value=10.0, value=5.0, step=0.5)
        agility = st.number_input("Agility", min_value=0.0, max_value=10.0, value=5.0, step=0.5)
        speed = st.number_input("Speed", min_value=0.0, max_value=10.0, value=5.0, step=0.5)
        acceleration = st.number_input("Acceleration", min_value=0.0, max_value=10.0, value=5.0, step=0.5)

        stats = {
            "health": health,
            "stamina": stamina,
            "courage": courage,
            "agility": agility,
            "speed": speed,
            "acceleration": acceleration,
        }

    with col2:
        if uploaded_file is not None:
            preview_img = Image.open(uploaded_file)
            st.image(preview_img, caption="Uploaded image", use_container_width=True)
        else:
            st.info("Upload an image to show on the auction listing.")

        st.subheader("Radar Chart Preview")
        fig = create_radar_chart(stats, title=name or "New Horse")
        st.plotly_chart(fig, use_container_width=True)

    if st.button(
        "🚀 Create Auction Listing",
        type="primary",
        disabled=not name,
    ):
        image_path = None
        if uploaded_file is not None:
            ext = os.path.splitext(uploaded_file.name)[1].lower() or ".png"
            filename = f"{uuid.uuid4().hex}{ext}"
            image_path = os.path.join(UPLOAD_DIR, filename)
            Image.open(uploaded_file).save(image_path)

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
        msg = f"Horse **{name}** listed successfully! Starting ${starting_bid:.2f}"
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

    if HAS_AUTOREFRESH:
        st_autorefresh(interval=10_000, key="active_auctions_refresh")
        st.caption("🔄 Page auto-refreshes every 10 seconds so bids stay live.")
    else:
        st.caption("Install `streamlit-autorefresh` for automatic live updates.")

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
                        "agility": h.get("agility") or 0,
                        "speed": h["speed"] or 0,
                        "acceleration": h["acceleration"] or 0,
                    }
                    fig = create_radar_chart(stats, title="")
                    st.plotly_chart(fig, use_container_width=True, key=f"radar_{h['id']}")

                    # Bid History
                    history = get_bid_history(h["id"])
                    with st.expander(f"📜 Bid History ({len(history)} bids)"):
                        if not history:
                            st.write("No bids yet.")
                        else:
                            for b in history:
                                try:
                                    t = datetime.fromisoformat(b["bid_time"]).strftime("%Y-%m-%d %H:%M")
                                except Exception:
                                    t = b["bid_time"]
                                st.write(f"**${b['amount']:.2f}** by {b['bidder']} · {t}")

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

                    can_delist = (
                        st.session_state.get("listing_authenticated", False)
                        or (
                            st.session_state.username
                            and st.session_state.username == h.get("seller")
                        )
                    )
                    if can_delist:
                        st.divider()
                        if st.button(
                            "🗑️ De-list / Cancel this horse",
                            key=f"cancel_{h['id']}",
                            type="secondary",
                        ):
                            ok, msg = cancel_listing(h["id"])
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)


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
            elif status == "cancelled":
                status_emoji = "🚫 Cancelled"
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
                        "agility": h.get("agility") or 0,
                        "speed": h["speed"] or 0,
                        "acceleration": h["acceleration"] or 0,
                    }
                    fig = create_radar_chart(stats, title=h["name"])
                    st.plotly_chart(fig, use_container_width=True, key=f"hist_radar_{h['id']}")

                    history = get_bid_history(h["id"])
                    st.write(f"**Bid History ({len(history)} bids)**")
                    if not history:
                        st.write("No bids recorded.")
                    else:
                        for b in history:
                            try:
                                t = datetime.fromisoformat(b["bid_time"]).strftime("%Y-%m-%d %H:%M")
                            except Exception:
                                t = b["bid_time"]
                            st.write(f"${b['amount']:.2f} by {b['bidder']} · {t}")


st.divider()
st.caption(
    "Emerald Ranch Auction · Stats out of 10 · Min bid $25 · "
    "Reserve prices · Bid history · Live refresh · Discord notifications"
)
