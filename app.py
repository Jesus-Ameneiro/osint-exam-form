import streamlit as st
from datetime import datetime
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, HRFlowable, KeepTogether,
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="OSINT CI LATAM — Exam Evaluator",
    page_icon="🔍",
    layout="centered",
)

# ── Session state defaults ────────────────────────────────────────────────────
_DEFAULTS: dict = {
    "form_generated":  False,
    "timer_running":   False,
    "elapsed_seconds": 0,
    "start_time":      None,
    "timer_events":    [],       # [{"action": "start"|"stop", "ts": "HH:MM:SS"}]
    "pdf_bytes":       None,
    "pdf_ready":       False,
    "header_data":     {},
    "scores":          {},       # {"<section_id>_<i>": bool}
    "notes":           {},       # {"<section_id>": str}
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Rubric data ───────────────────────────────────────────────────────────────
SECTIONS = [
    {
        "id": "mach_date", "title": "Machine Viability — Dates",
        "points": 15, "color": "#534AB7",
        "items": [
            {"text": "Identifies ≥ 1 machine with Last Event ≥ 2023 linked to the company", "pts": 5, "crit": False},
            {"text": "Handles Last Event ≤ 2022 correctly: approves only if no prior engagement AND company association is confirmed", "pts": 5, "crit": False},
            {"text": "Does not approve out-of-date machines without documented justification", "pts": 5, "crit": False},
        ],
    },
    {
        "id": "mach_tech", "title": "Machine Viability — Technical",
        "points": 25, "color": "#0F6E56",
        "items": [
            {"text": "Checks computer domain or client email for company association", "pts": 4, "crit": False},
            {"text": "Checks hostname and username for company association", "pts": 4, "crit": False},
            {"text": "When domain/email absent: investigates hostname/username during infringement period and documents sources with links", "pts": 5, "crit": True},
            {"text": "Checks IP Organization or Public IP (requires ≥ 5 events)", "pts": 3, "crit": False},
            {"text": "Checks Wi-Fi access points for location association (requires ≥ 5 events)", "pts": 3, "crit": False},
            {"text": "Applies SketchUp Pro rule: Event Types must be Unlicensed or Undefined with no license info", "pts": 3, "crit": False},
            {"text": "Handles conflicting machine IDs: investigates name change / acquisition or multiple machines (uses Active MACs and System Models)", "pts": 3, "crit": True},
        ],
    },
    {
        "id": "mach_rej", "title": "Machine Rejection — Non-Viable Cases",
        "points": 10, "color": "#993C1D",
        "items": [
            {"text": "Rejects machines with only Commercial, Evaluation, or Educational events", "pts": 4, "crit": False},
            {"text": "Rejects machines with no company association found", "pts": 3, "crit": False},
            {"text": "Rejects machines with hashed / absent hostname and username when no other clear association exists", "pts": 3, "crit": False},
        ],
    },
    {
        "id": "latam", "title": "LATAM Campaign Qualification",
        "points": 10, "color": "#185FA5",
        "items": [
            {"text": "Confirms company has office only in LATAM — approves directly", "pts": 3, "crit": False},
            {"text": "Multi-country company: verifies ≥ 1 machine linked to LATAM via IP, Wi-Fi, or domain prefix/suffix", "pts": 4, "crit": False},
            {"text": "Identifies when case belongs to another Ruvixx campaign and moves / unqualifies it correctly", "pts": 3, "crit": False},
        ],
    },
    {
        "id": "entity", "title": "Entity Qualification",
        "points": 15, "color": "#854F0B",
        "items": [
            {"text": "Locates the correct entity using the email domain found in the data", "pts": 4, "crit": False},
            {"text": "When no website exists, accepts social media — verifies posts within the past 2 years", "pts": 3, "crit": False},
            {"text": "Confirms entity has an office in the country where irregular events occurred", "pts": 4, "crit": False},
            {"text": "When multiple subsidiaries exist: qualifies the specific offices where infringements occurred", "pts": 4, "crit": True},
        ],
    },
    {
        "id": "contacts", "title": "Contact Identification",
        "points": 15, "color": "#3B6D11",
        "items": [
            {"text": "Finds ≥ 2 corporate individual contacts (not generic emails unless unavoidable)", "pts": 5, "crit": False},
            {"text": "Avoids generic emails (contact@, info@, sales@…) unless fewer than 2 individuals found", "pts": 3, "crit": False},
            {"text": "Verifies email domain when a contact found online has a different domain", "pts": 3, "crit": False},
            {"text": "Associates ≥ 1 phone number; does not repeat the same number across contacts", "pts": 4, "crit": False},
        ],
    },
    {
        "id": "notes_sec", "title": "Notes & Justification",
        "points": 10, "color": "#993556",
        "items": [
            {"text": "Documents reasoning for approved machines (sources and links provided)", "pts": 4, "crit": False},
            {"text": "Documents reasoning for rejected machines with clear explanation", "pts": 3, "crit": False},
            {"text": "Notes reflect investigation logic, not just conclusions", "pts": 3, "crit": False},
        ],
    },
    {
        "id": "tools", "title": "Tools & Investigation Strategy",
        "points": 0, "color": "#5F5E5A", "qualitative": True,
        "items": [
            {"text": "Uses LinkedIn, company website, or registry for entity / contact verification", "pts": 0, "crit": False},
            {"text": "Uses WHOIS, DNS, or IP lookup tools to corroborate technical data", "pts": 0, "crit": False},
            {"text": "Uses Google Maps, Street View, or Wi-Fi geolocation tools", "pts": 0, "crit": False},
            {"text": "Uses reverse email / phone lookup tools", "pts": 0, "crit": False},
            {"text": "Uses Pleteo efficiently (filters, event types, machine comparison)", "pts": 0, "crit": False},
            {"text": "Shows systematic workflow: reads data → assesses machines → qualifies entity → finds contacts → documents", "pts": 0, "crit": False},
        ],
    },
]

MAX_PTS: int = sum(s["points"] for s in SECTIONS if not s.get("qualitative"))

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_elapsed() -> int:
    total = st.session_state.elapsed_seconds
    if st.session_state.timer_running and st.session_state.start_time:
        total += (datetime.now() - st.session_state.start_time).total_seconds()
    return int(total)


def fmt_time(secs: int) -> str:
    secs = max(0, int(secs))
    m, s = divmod(secs, 60)
    return f"{m:02d}:{s:02d}"


def calc_scores():
    total = 0
    result = {}
    for sec in SECTIONS:
        sc = 0
        items_out = []
        for i, it in enumerate(sec["items"]):
            checked = st.session_state.scores.get(f"{sec['id']}_{i}", False)
            if not sec.get("qualitative") and checked:
                sc += it["pts"]
            items_out.append({**it, "checked": checked})
        result[sec["id"]] = {
            "scored": sc,
            "total": sec["points"],
            "qualitative": sec.get("qualitative", False),
            "items": items_out,
            "note": st.session_state.notes.get(sec["id"], ""),
        }
        total += sc
    return total, result


def verdict(pct: int):
    if pct >= 85:
        return "APPROVED",           "#0F6E56", "#E1F5EE"
    if pct >= 65:
        return "CONDITIONAL",        "#854F0B", "#FAEEDA"
    return     "NEEDS IMPROVEMENT",  "#A32D2D", "#FCEBEB"


def _do_timer_toggle():
    if st.session_state.timer_running:
        st.session_state.elapsed_seconds += (
            datetime.now() - st.session_state.start_time
        ).total_seconds()
        st.session_state.timer_running = False
        st.session_state.timer_events.append(
            {"action": "stop", "ts": datetime.now().strftime("%H:%M:%S")}
        )
    else:
        st.session_state.start_time = datetime.now()
        st.session_state.timer_running = True
        st.session_state.timer_events.append(
            {"action": "start", "ts": datetime.now().strftime("%H:%M:%S")}
        )


# ── PDF generation ────────────────────────────────────────────────────────────

def build_pdf() -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2.5 * cm, bottomMargin=2 * cm,
    )
    scored, sec_data = calc_scores()
    pct = round(scored / MAX_PTS * 100) if MAX_PTS else 0
    vtext, vcol, _ = verdict(pct)
    elapsed = get_elapsed()
    hd = st.session_state.header_data

    def S(name, **kw):
        return ParagraphStyle(name, parent=getSampleStyleSheet()["Normal"], **kw)

    C = lambda h: colors.HexColor(h)
    PURPLE = C("#534AB7")
    WHITE  = colors.white
    DARK   = C("#1a1a2e")
    MUTED  = C("#666666")

    story = []

    # ── Title ──
    story.append(Paragraph(
        "OSINT CI LATAM",
        S("t1", fontSize=20, fontName="Helvetica-Bold", textColor=DARK),
    ))
    story.append(Paragraph(
        "Case Investigation Exam · Evaluation Report",
        S("t2", fontSize=11, fontName="Helvetica", textColor=MUTED, spaceAfter=8),
    ))
    story.append(HRFlowable(width="100%", thickness=2.5, color=PURPLE, spaceAfter=12))

    # ── Info table ──
    info_rows = [
        ["Region",      hd.get("region", ""),      "Exam #",     str(hd.get("exam_number", ""))],
        ["Manager",     hd.get("manager", ""),     "Date",       str(hd.get("date", ""))],
        ["Proctor",     hd.get("proctor", ""),     "Time used",  fmt_time(elapsed)],
        ["Investigator", hd.get("investigator", ""), "",          ""],
    ]
    t_info = Table(info_rows, colWidths=[2.8*cm, 6.5*cm, 2.8*cm, 4.5*cm])
    t_info.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), PURPLE),
        ("TEXTCOLOR", (2, 0), (2, -1), PURPLE),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [C("#f5f3ff"), WHITE]),
        ("GRID", (0, 0), (-1, -1), 0.4, C("#e0e0e0")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t_info)
    story.append(Spacer(1, 14))

    # ── Score banner ──
    banner = [[
        Paragraph("<b>TOTAL SCORE</b>",
                  S("bs1", fontSize=9, fontName="Helvetica-Bold", textColor=WHITE)),
        Paragraph(f"<b>{scored} / {MAX_PTS} pts — {pct}%</b>",
                  S("bs2", fontSize=14, fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_CENTER)),
        Paragraph(f"<b>{vtext}</b>",
                  S("bs3", fontSize=10, fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_RIGHT)),
    ]]
    t_banner = Table(banner, colWidths=[3.5*cm, 8.5*cm, 4.6*cm])
    t_banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PURPLE),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(t_banner)
    story.append(Spacer(1, 18))

    # ── Timer log ──
    if st.session_state.timer_events:
        story.append(Paragraph(
            "Timer Log",
            S("tlog", fontSize=9, fontName="Helvetica-Bold", textColor=MUTED, spaceAfter=4),
        ))
        ev_rows = [["Action", "Timestamp"]] + [
            ["Started" if e["action"] == "start" else "Paused", e["ts"]]
            for e in st.session_state.timer_events
        ]
        t_ev = Table(ev_rows, colWidths=[4*cm, 4*cm])
        t_ev.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), C("#f0f0f0")),
            ("GRID", (0, 0), (-1, -1), 0.3, C("#cccccc")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(t_ev)
        story.append(Spacer(1, 14))

    # ── Sections ──
    for sec in SECTIONS:
        sd = sec_data[sec["id"]]
        sc = C(sec["color"])
        pts_lbl = "Qualitative" if sd["qualitative"] else f"{sd['scored']} / {sec['points']} pts"

        hdr_data = [[
            Paragraph(f"<b>{sec['title']}</b>",
                      S("sh", fontSize=10, fontName="Helvetica-Bold", textColor=WHITE)),
            Paragraph(f"<b>{pts_lbl}</b>",
                      S("sp", fontSize=10, fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_RIGHT)),
        ]]
        t_hdr = Table(hdr_data, colWidths=[13*cm, 3.6*cm])
        t_hdr.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), sc),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))

        rows = []
        for it in sd["items"]:
            mark    = "☑" if it["checked"] else "☐"
            flag    = "  ⚑ critical" if it["crit"] else ""
            pts_str = f"{it['pts']} pt" if not sd["qualitative"] and it["pts"] > 0 else "—"
            txt_col = C("#111111") if it["checked"] else C("#999999")
            pt_col  = sc if it["checked"] else C("#cccccc")
            rows.append([
                Paragraph(f"{mark}  {it['text']}{flag}",
                          S("ri", fontSize=8.5, fontName="Helvetica", textColor=txt_col, leftIndent=4)),
                Paragraph(pts_str,
                          S("rp", fontSize=8, fontName="Helvetica", textColor=pt_col, alignment=TA_RIGHT)),
            ])

        t_rows = Table(rows, colWidths=[14.5*cm, 2.1*cm])
        t_rows.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, C("#fafafa")]),
            ("GRID", (0, 0), (-1, -1), 0.3, C("#e8e8e8")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))

        block = [t_hdr, t_rows]
        note = sd["note"].strip()
        if note:
            block.append(Paragraph(
                f"<i>Evaluator notes: {note}</i>",
                S("nt", fontSize=8.5, fontName="Helvetica-Oblique",
                  textColor=C("#555555"), leftIndent=8,
                  backColor=C("#fffbf0"), borderPadding=5, spaceAfter=2),
            ))
        block.append(Spacer(1, 8))
        story.append(KeepTogether(block))

    # ── Footer ──
    story.append(HRFlowable(width="100%", thickness=1, color=C("#dddddd"),
                             spaceBefore=8, spaceAfter=8))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%B %d, %Y at %H:%M')} "
        f"· Ruvixx LATAM CI-QA · Proctor: {hd.get('proctor', '')}",
        S("ft", fontSize=8, fontName="Helvetica", textColor=C("#aaaaaa"), alignment=TA_CENTER),
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
.stApp { background: #f5f5fb; }
.block-container { padding-top: 1.5rem !important; max-width: 840px !important; }
[data-testid="stSidebar"] {
    background: #ffffff;
    border-right: 1px solid #e8e8f0;
}
/* Give the page room at the bottom so the FAB never covers content */
.block-container { padding-bottom: 160px !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# HIDDEN TIMER TOGGLE BUTTON
# Rendered off-screen; the floating FAB clicks it programmatically via JS.
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.form_generated:
    _toggle = st.button("TIMER_TOGGLE_HIDDEN__", key="fab_trigger_btn")
    if _toggle:
        _do_timer_toggle()
        st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# FLOATING FAB  (only shown after Generate Form)
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.form_generated:
    _e   = int(st.session_state.elapsed_seconds)
    _r   = "true" if st.session_state.timer_running else "false"
    _ts  = (
        int(st.session_state.start_time.timestamp())
        if st.session_state.timer_running and st.session_state.start_time
        else 0
    )
    _status       = "RUNNING" if st.session_state.timer_running else ("PAUSED" if _e > 0 else "READY")
    _status_color = "#4CAF50" if st.session_state.timer_running else ("#FF9800" if _e > 0 else "#888888")
    _btn_label    = "⏸  Pause Exam" if st.session_state.timer_running else (
                    "▶  Resume"     if _e > 0 else "▶  Start Exam")
    _btn_bg       = "#c0392b" if st.session_state.timer_running else "#534AB7"
    _pulse_anim   = "animation: fabPulseRed 1.8s infinite;" if st.session_state.timer_running else \
                    "animation: fabPulseBlue 3s infinite;"

    st.markdown(f"""
<style>
/* ── FAB layout ── */
#osint-fab {{
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 99999;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 10px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
/* ── Timer pill ── */
#osint-fab-timer {{
    background: #1a1a2e;
    color: white;
    border-radius: 18px;
    padding: 14px 22px;
    text-align: center;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45);
    min-width: 138px;
    user-select: none;
}}
#osint-fab-timer .fab-lbl  {{ font-size: 10px; color: #888; letter-spacing: 2px; margin-bottom: 6px; }}
#osint-fab-timer .fab-time {{ font-size: 2.3rem; font-weight: 700; font-family: monospace; letter-spacing: 4px; }}
#osint-fab-timer .fab-st   {{ font-size: 10px; font-weight: 600; margin-top: 6px;
                               letter-spacing: 1px; color: {_status_color}; }}
/* ── Action button ── */
#osint-fab-action {{
    background: {_btn_bg};
    color: white;
    border: none;
    border-radius: 50px;
    padding: 13px 28px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    {_pulse_anim}
    transition: transform .15s ease, background .2s ease;
    white-space: nowrap;
}}
#osint-fab-action:hover  {{ transform: scale(1.06); }}
#osint-fab-action:active {{ transform: scale(0.96); }}
/* ── Pulse keyframes ── */
@keyframes fabPulseRed  {{
    0%, 100% {{ box-shadow: 0 6px 24px rgba(192, 57, 43, 0.55); }}
    50%       {{ box-shadow: 0 6px 42px rgba(192, 57, 43, 0.95); }}
}}
@keyframes fabPulseBlue {{
    0%, 100% {{ box-shadow: 0 6px 24px rgba(83, 74, 183, 0.50); }}
    50%       {{ box-shadow: 0 6px 38px rgba(83, 74, 183, 0.85); }}
}}
</style>

<div id="osint-fab">
  <div id="osint-fab-timer">
    <div class="fab-lbl">EXAM TIMER</div>
    <div class="fab-time" id="osint-time-el">00:00</div>
    <div class="fab-st" id="osint-status-el">● {_status}</div>
  </div>
  <button id="osint-fab-action" onclick="fabToggle()">{_btn_label}</button>
</div>

<script>
/* ── Live timer ── */
(function () {{
  var base    = {_e};
  var running = {_r};
  var since   = {_ts};

  function fmt(s) {{
    s = Math.max(0, s);
    var m = Math.floor(s / 60), sec = s % 60;
    return String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
  }}

  function tick() {{
    var total = base;
    if (running && since > 0) total += Math.floor(Date.now() / 1000 - since);
    var el = document.getElementById('osint-time-el');
    if (el) el.textContent = fmt(total);
  }}

  tick();
  setInterval(tick, 500);
}})();

/* ── FAB click → triggers hidden Streamlit button ── */
function fabToggle() {{
  /* Search all buttons in the page for our sentinel label */
  var all = document.querySelectorAll('button');
  for (var i = 0; i < all.length; i++) {{
    var b = all[i];
    if (b.innerText && b.innerText.trim() === 'TIMER_TOGGLE_HIDDEN__') {{
      b.click();
      return;
    }}
  }}
}}

/* ── Hide the sentinel button from view ── */
(function hideToggle() {{
  function tryHide() {{
    var all = document.querySelectorAll('button');
    for (var i = 0; i < all.length; i++) {{
      var b = all[i];
      if (b.innerText && b.innerText.trim() === 'TIMER_TOGGLE_HIDDEN__') {{
        /* Walk up to the stButton wrapper */
        var el = b;
        for (var j = 0; j < 8; j++) {{
          if (!el.parentElement) break;
          el = el.parentElement;
          if (el.getAttribute && el.getAttribute('data-testid') === 'stButton') {{
            el.style.cssText +=
              'position:fixed!important;left:-9999px!important;' +
              'height:0!important;overflow:hidden!important;opacity:0!important;';
            return true;
          }}
        }}
      }}
    }}
    return false;
  }}

  if (!tryHide()) {{
    /* Retry via MutationObserver until found */
    var obs = new MutationObserver(function () {{
      if (tryHide()) obs.disconnect();
    }});
    obs.observe(document.body, {{ childList: true, subtree: true }});
    /* Hard fallback after 2 s */
    setTimeout(function () {{ tryHide(); obs.disconnect(); }}, 2000);
  }}
}})();
</script>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — score overview + timer log
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 OSINT CI LATAM")
    if st.session_state.form_generated:
        scored, _ = calc_scores()
        pct = round(scored / MAX_PTS * 100)
        vt, vc, _ = verdict(pct)
        st.metric("Score", f"{scored} / {MAX_PTS}", f"{pct}%")
        st.progress(pct / 100)
        st.markdown(
            f'<span style="color:{vc};font-weight:600;font-size:14px;">{vt}</span>',
            unsafe_allow_html=True,
        )
        if st.session_state.timer_events:
            st.divider()
            st.caption("Timer log")
            for ev in st.session_state.timer_events:
                icon = "▶" if ev["action"] == "start" else "⏸"
                st.caption(f"{icon} {ev['action'].title()} — {ev['ts']}")
    else:
        st.info("Complete the header form and click **Generate Form** to begin.")

# ─────────────────────────────────────────────────────────────────────────────
# PAGE TITLE
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("## OSINT CI LATAM — Exam Evaluation")
st.caption("Ruvixx SketchUp License Compliance · Case Investigation")
st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# HEADER FORM
# ─────────────────────────────────────────────────────────────────────────────
if not st.session_state.form_generated:
    st.markdown("### 📋 Exam Information")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.selectbox(
            "Region *",
            ["MCC — México Central Caribe", "CS — Cono Sur"],
            key="inp_region",
        )
    with c2:
        st.text_input("Manager *", key="inp_manager", placeholder="e.g. Tatiana Romero")
    with c3:
        st.text_input("Exam Proctor *", key="inp_proctor", placeholder="e.g. Jesús")

    c4, c5, c6 = st.columns(3)
    with c4:
        st.text_input("Investigator *", key="inp_investigator", placeholder="Full name")
    with c5:
        st.number_input("Exam #", min_value=1, max_value=99, value=1, key="inp_exam_number")
    with c6:
        st.date_input("Date *", value=datetime.today(), key="inp_date")

    st.markdown("")
    _, btn_c, _ = st.columns([2, 1.8, 2])
    with btn_c:
        _ready = bool(
            st.session_state.get("inp_manager", "").strip()
            and st.session_state.get("inp_proctor", "").strip()
            and st.session_state.get("inp_investigator", "").strip()
        )
        if st.button(
            "Generate Form  ▶",
            type="primary",
            use_container_width=True,
            disabled=not _ready,
        ):
            st.session_state.form_generated = True
            st.session_state.header_data = {
                "region":       st.session_state.inp_region,
                "manager":      st.session_state.inp_manager,
                "proctor":      st.session_state.inp_proctor,
                "investigator": st.session_state.inp_investigator,
                "exam_number":  st.session_state.inp_exam_number,
                "date":         st.session_state.inp_date.strftime("%B %d, %Y"),
            }
            st.rerun()

else:
    # Header summary bar
    hd = st.session_state.header_data
    c_info, c_edit = st.columns([5, 1])
    with c_info:
        st.markdown(
            f"""
            <div style="background:#EEEDFE;border-left:4px solid #534AB7;border-radius:0 10px 10px 0;
                        padding:10px 16px;font-size:13px;color:#1a1a2e;">
              <b>{hd.get('investigator','')}</b> &nbsp;·&nbsp;
              {hd.get('region','')} &nbsp;·&nbsp;
              Exam #{hd.get('exam_number','')} &nbsp;·&nbsp;
              {hd.get('date','')} &nbsp;·&nbsp;
              Proctor: {hd.get('proctor','')}
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c_edit:
        if st.button("✏️ Edit", use_container_width=True):
            for _k in [
                "form_generated", "timer_running", "elapsed_seconds",
                "start_time", "timer_events", "pdf_bytes", "pdf_ready",
                "scores", "notes",
            ]:
                st.session_state[_k] = (
                    [] if isinstance(_DEFAULTS[_k], list)
                    else {} if isinstance(_DEFAULTS[_k], dict)
                    else _DEFAULTS[_k]
                )
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION SECTIONS
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.form_generated:
    st.markdown("### 📝 Evaluation Criteria")
    st.caption("Mark each item the investigator correctly identifies or performs during the session.")
    st.markdown("")

    for sec in SECTIONS:
        is_q = sec.get("qualitative", False)

        # Live section score
        sec_scored = sum(
            sec["items"][i]["pts"]
            for i in range(len(sec["items"]))
            if st.session_state.scores.get(f"{sec['id']}_{i}", False) and not is_q
        )
        pts_display = "Qualitative" if is_q else f"{sec_scored} / {sec['points']} pts"

        # Colored section header (standalone div — does NOT wrap Streamlit widgets)
        st.markdown(
            f"""
            <div style="
                background: {sec['color']};
                border-radius: 14px 14px 0 0;
                padding: 12px 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-top: 1.8rem;
            ">
              <span style="color:white;font-weight:600;font-size:15px;">{sec['title']}</span>
              <span style="color:rgba(255,255,255,.85);font-size:13px;">{pts_display}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Items inside a visually bordered body
        with st.container():
            for i, it in enumerate(sec["items"]):
                key = f"{sec['id']}_{i}"
                label = it["text"] + ("  🔴" if it["crit"] else "")
                cc, cp = st.columns([11, 1])
                with cc:
                    val = st.checkbox(
                        label,
                        key=f"cb_{key}",
                        value=st.session_state.scores.get(key, False),
                    )
                    st.session_state.scores[key] = val
                with cp:
                    if not is_q and it["pts"] > 0:
                        clr = sec["color"] if val else "#cccccc"
                        st.markdown(
                            f'<div style="text-align:right;font-size:11px;font-weight:600;'
                            f'color:{clr};padding-top:6px;">{it["pts"]}pt</div>',
                            unsafe_allow_html=True,
                        )

            note = st.text_area(
                "Evaluator notes:",
                key=f"note_{sec['id']}",
                value=st.session_state.notes.get(sec["id"], ""),
                height=68,
                placeholder="Observations, tools used, case-specific details...",
            )
            st.session_state.notes[sec["id"]] = note

        # Closing card border
        st.markdown(
            """<div style="border:1px solid #e8e8f0;border-top:none;
                           border-radius:0 0 14px 14px;height:10px;
                           background:white;"></div>""",
            unsafe_allow_html=True,
        )

    # ── Final score card ──────────────────────────────────────────────────────
    st.divider()
    scored, _ = calc_scores()
    pct = round(scored / MAX_PTS * 100)
    vt, vc, vbg = verdict(pct)
    elapsed = get_elapsed()

    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #534AB7 0%, #7F77DD 100%);
            border-radius: 18px; padding: 28px 32px;
            color: white; text-align: center; margin: 1rem 0 1.5rem;
        ">
          <div style="font-size:12px;opacity:.7;letter-spacing:1.5px;margin-bottom:6px;">FINAL SCORE</div>
          <div style="font-size:3.5rem;font-weight:700;line-height:1.1;">
            {scored}
            <span style="font-size:1.6rem;opacity:.55;margin-left:6px;">/ {MAX_PTS}</span>
          </div>
          <div style="font-size:1.25rem;opacity:.85;margin-top:4px;">{pct}%</div>
          <div style="
              margin-top: 14px; display: inline-block;
              background: {vbg}; color: {vc};
              padding: 6px 26px; border-radius: 20px;
              font-weight: 600; font-size: 14px;
          ">{vt}</div>
          <div style="margin-top:10px;opacity:.65;font-size:13px;">⏱ Time used: {fmt_time(elapsed)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c_gen, c_dl = st.columns(2)
    with c_gen:
        if st.button("📊 Generate Result", type="primary", use_container_width=True):
            # Auto-stop timer if still running
            if st.session_state.timer_running:
                st.session_state.elapsed_seconds += (
                    datetime.now() - st.session_state.start_time
                ).total_seconds()
                st.session_state.timer_running = False
                st.session_state.timer_events.append(
                    {"action": "stop", "ts": datetime.now().strftime("%H:%M:%S")}
                )
            with st.spinner("Building PDF report…"):
                st.session_state.pdf_bytes = build_pdf()
                st.session_state.pdf_ready = True
            st.rerun()

    with c_dl:
        if st.session_state.pdf_ready and st.session_state.pdf_bytes:
            hd = st.session_state.header_data
            fname = (
                f"OSINT_Exam_"
                f"{hd.get('investigator', '').replace(' ', '_')}_"
                f"Exam{hd.get('exam_number', '')}_"
                f"{datetime.now().strftime('%Y%m%d')}.pdf"
            )
            st.download_button(
                "⬇️ Download PDF",
                data=st.session_state.pdf_bytes,
                file_name=fname,
                mime="application/pdf",
                use_container_width=True,
            )
            st.success("✅ PDF ready for download!")

    # Space so FAB never covers the download button
    st.markdown("<div style='height:140px;'></div>", unsafe_allow_html=True)
