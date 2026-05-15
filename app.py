import streamlit as st
from datetime import datetime
import io
import requests

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

# ── Rating constants ──────────────────────────────────────────────────────────
RATING_LABELS = {
    0: "N/A",
    1: "Not achieved",
    2: "Needs work",
    3: "Adequate",
    4: "Good",
    5: "Excellent",
}
RATING_COLORS = {
    0: "#888888",
    1: "#e03030",
    2: "#d4890a",
    3: "#c4a700",
    4: "#7cb342",
    5: "#16a877",
}
RATING_FMT = ["N/A", "1", "2", "3", "4", "5"]

# ── Session state defaults ────────────────────────────────────────────────────
_DEFAULTS: dict = {
    "form_generated":  False,
    "timer_running":   False,
    "elapsed_seconds": 0,
    "start_time":      None,
    "timer_events":    [],
    "pdf_bytes":       None,
    "pdf_ready":       False,
    "header_data":     {},
    "scores":          {},   # {f"{sec_id}_{i}": int 0-5}
    "notes":           {},   # {sec_id: str}
    "proctor_obs":     "",
    "ai_recs":         "",
    "recs_ready":      False,
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

# ── Core helpers ──────────────────────────────────────────────────────────────

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
    """Score = round(rating / 5 * item_pts) — fully proportional to 0-5 rating."""
    total  = 0
    result = {}
    for sec in SECTIONS:
        sc        = 0
        items_out = []
        for i, it in enumerate(sec["items"]):
            rating     = int(st.session_state.scores.get(f"{sec['id']}_{i}", 0))
            pts_earned = round(rating / 5 * it["pts"]) if not sec.get("qualitative") else 0
            sc        += pts_earned
            items_out.append({**it, "rating": rating, "pts_earned": pts_earned})
        result[sec["id"]] = {
            "scored":      sc,
            "total":       sec["points"],
            "qualitative": sec.get("qualitative", False),
            "items":       items_out,
            "note":        st.session_state.notes.get(sec["id"], ""),
        }
        total += sc
    return total, result


def verdict(pct: int):
    if pct >= 85:
        return "APPROVED",          "#16a877", "#16a877", "white"
    if pct >= 65:
        return "CONDITIONAL",       "#d4890a", "#d4890a", "white"
    return     "NEEDS IMPROVEMENT", "#e03030", "#e03030", "white"


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
        st.session_state.start_time  = datetime.now()
        st.session_state.timer_running = True
        st.session_state.timer_events.append(
            {"action": "start", "ts": datetime.now().strftime("%H:%M:%S")}
        )

# ── AI Recommendations ────────────────────────────────────────────────────────

def build_ai_recommendations() -> str:
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return (
            "**⚠️ API key not configured.**\n\n"
            "Add `ANTHROPIC_API_KEY` to your Streamlit secrets to enable AI recommendations.\n"
            "Go to app dashboard → Settings → Secrets."
        )

    scored, sec_data = calc_scores()
    pct     = round(scored / MAX_PTS * 100)
    hd      = st.session_state.header_data
    obs     = st.session_state.proctor_obs.strip()
    elapsed = get_elapsed()

    lines = []
    for sec in SECTIONS:
        sd   = sec_data[sec["id"]]
        is_q = sd["qualitative"]
        pts_str = "Qualitative" if is_q else f"{sd['scored']}/{sec['points']} pts"
        lines.append(f"\n## {sec['title']} ({pts_str})")
        for it in sd["items"]:
            r     = it["rating"]
            label = RATING_LABELS.get(r, "?")
            crit  = " [CRITICAL ITEM]" if it["crit"] else ""
            if is_q:
                lines.append(f"  - Observed ({label}): {it['text']}{crit}")
            else:
                lines.append(
                    f"  - Rating {r}/5 ({label}) → {it['pts_earned']}/{it['pts']} pts: "
                    f"{it['text']}{crit}"
                )
        if sd["note"]:
            lines.append(f"  → Evaluator notes: {sd['note']}")

    exam_summary = "\n".join(lines)

    prompt = f"""You are a senior CI-QA Lead at Ruvixx, providing post-exam coaching to a SketchUp License Compliance Case Investigator in the LATAM region.

EXAM CONTEXT
────────────
Investigator : {hd.get('investigator', 'N/A')}
Region       : {hd.get('region', 'N/A')}
Exam #       : {hd.get('exam_number', 'N/A')} — {hd.get('date', 'N/A')}
Proctor      : {hd.get('proctor', 'N/A')}
Score        : {scored}/{MAX_PTS} pts ({pct}%)
Time used    : {fmt_time(elapsed)}

RATING SCALE: 0=N/A · 1=Not achieved · 2=Needs work · 3=Adequate · 4=Good · 5=Excellent

DETAILED EXAM RESULTS
─────────────────────
{exam_summary}

PROCTOR OBSERVATIONS
────────────────────
{obs if obs else "(None provided)"}

───────────────────────────────────────────────────────────
Based on the above, write a structured investigator coaching report using Markdown.

### Overall Assessment
2–3 sentences summarizing performance, the score, and the most significant pattern.

### Strengths
Items or sections rated 4–5. Brief and specific. Skip if none.

### Priority Areas for Improvement
For each item or section rated 0–2 (prioritize critical items):
- What was missed or done incorrectly
- Why it matters to investigation quality
- A concrete corrective action or example

### Recommended Strategies & Methods
3–5 specific, actionable techniques to practice:
- Tools (LinkedIn, WHOIS, IP lookup, Pleteo filters, etc.)
- Workflow habits and order of operations
- Decision frameworks for common edge cases

### Learning Path
A short prioritized sequence: what to tackle first, second, third before the next exam.

### Proctor Follow-up
Additional coaching notes based on the proctor observations above.

Be direct, constructive, and specific. Avoid generic advice. Write in English."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model":    "claude-sonnet-4-20250514",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        if resp.status_code == 200:
            return resp.json()["content"][0]["text"]
        return f"⚠️ API error {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return f"⚠️ Request failed: {e}"

# ── PDF generation ────────────────────────────────────────────────────────────

def build_pdf() -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2.5*cm, bottomMargin=2*cm,
    )
    scored, sec_data = calc_scores()
    pct     = round(scored / MAX_PTS * 100) if MAX_PTS else 0
    vtext, _, _, _ = verdict(pct)
    elapsed = get_elapsed()
    hd      = st.session_state.header_data
    recs    = st.session_state.ai_recs.strip()

    def S(name, **kw):
        return ParagraphStyle(name, parent=getSampleStyleSheet()["Normal"], **kw)

    C      = lambda h: colors.HexColor(h)
    PURPLE = C("#534AB7")
    WHITE  = colors.white
    DARK   = C("#1a1a2e")
    MUTED  = C("#666666")

    def rclr(r):
        return [C("#888888"), C("#c0392b"), C("#d68910"),
                C("#9a7d0a"), C("#5d8a2e"), C("#117a65")][max(0, min(5, r))]

    story = []

    # Title
    story.append(Paragraph("OSINT CI LATAM",
        S("t1", fontSize=20, fontName="Helvetica-Bold", textColor=DARK)))
    story.append(Paragraph("Case Investigation Exam · Evaluation Report",
        S("t2", fontSize=11, fontName="Helvetica", textColor=MUTED, spaceAfter=8)))
    story.append(HRFlowable(width="100%", thickness=2.5, color=PURPLE, spaceAfter=12))

    # Info table
    info_rows = [
        ["Region",      hd.get("region", ""),       "Exam #",    str(hd.get("exam_number", ""))],
        ["Manager",     hd.get("manager", ""),      "Date",      str(hd.get("date", ""))],
        ["Proctor",     hd.get("proctor", ""),      "Time used", fmt_time(elapsed)],
        ["Investigator", hd.get("investigator", ""), "",          ""],
    ]
    t_info = Table(info_rows, colWidths=[2.8*cm, 6.5*cm, 2.8*cm, 4.5*cm])
    t_info.setStyle(TableStyle([
        ("FONTNAME",      (0,0), (-1,-1), "Helvetica"),
        ("FONTNAME",      (0,0), (0,-1),  "Helvetica-Bold"),
        ("FONTNAME",      (2,0), (2,-1),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("TEXTCOLOR",     (0,0), (0,-1),  PURPLE),
        ("TEXTCOLOR",     (2,0), (2,-1),  PURPLE),
        ("ROWBACKGROUNDS",(0,0), (-1,-1), [C("#f5f3ff"), WHITE]),
        ("GRID",          (0,0), (-1,-1), 0.4, C("#e0e0e0")),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
    ]))
    story.append(t_info)
    story.append(Spacer(1, 14))

    # Score banner
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
        ("BACKGROUND",    (0,0), (-1,-1), PURPLE),
        ("TOPPADDING",    (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("LEFTPADDING",   (0,0), (-1,-1), 12),
        ("RIGHTPADDING",  (0,0), (-1,-1), 12),
    ]))
    story.append(t_banner)
    story.append(Spacer(1, 18))

    # Timer log
    if st.session_state.timer_events:
        story.append(Paragraph("Timer Log",
            S("tlog", fontSize=9, fontName="Helvetica-Bold", textColor=MUTED, spaceAfter=4)))
        ev_rows = [["Action", "Timestamp"]] + [
            ["Started" if e["action"] == "start" else "Paused", e["ts"]]
            for e in st.session_state.timer_events
        ]
        t_ev = Table(ev_rows, colWidths=[4*cm, 4*cm])
        t_ev.setStyle(TableStyle([
            ("FONTNAME",      (0,0), (-1,-1), "Helvetica"),
            ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("BACKGROUND",    (0,0), (-1,0),  C("#f0f0f0")),
            ("GRID",          (0,0), (-1,-1), 0.3, C("#cccccc")),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ]))
        story.append(t_ev)
        story.append(Spacer(1, 14))

    # Rating legend
    story.append(Paragraph("Rating Scale",
        S("rl", fontSize=9, fontName="Helvetica-Bold", textColor=MUTED, spaceAfter=4)))
    leg_pairs = [(0,1),(2,3),(4,5)]
    for r1, r2 in leg_pairs:
        story.append(Paragraph(
            f"<b>{r1}</b> = {RATING_LABELS[r1]}  &nbsp;&nbsp;&nbsp;  <b>{r2}</b> = {RATING_LABELS[r2]}",
            S(f"lg{r1}", fontSize=8, fontName="Helvetica", textColor=MUTED, spaceAfter=2),
        ))
    story.append(Spacer(1, 14))

    # Section results
    for sec in SECTIONS:
        sd   = sec_data[sec["id"]]
        sc   = colors.HexColor(sec["color"])
        is_q = sd["qualitative"]
        pts_lbl = "Qualitative" if is_q else f"{sd['scored']} / {sec['points']} pts"

        hdr_d = [[
            Paragraph(f"<b>{sec['title']}</b>",
                      S("sh", fontSize=10, fontName="Helvetica-Bold", textColor=WHITE)),
            Paragraph(f"<b>{pts_lbl}</b>",
                      S("sp", fontSize=10, fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_RIGHT)),
        ]]
        t_hdr = Table(hdr_d, colWidths=[13*cm, 3.6*cm])
        t_hdr.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), sc),
            ("TOPPADDING",    (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
            ("LEFTPADDING",   (0,0), (-1,-1), 10),
            ("RIGHTPADDING",  (0,0), (-1,-1), 10),
        ]))

        rows = []
        for it in sd["items"]:
            r      = it["rating"]
            flag   = "  ⚑" if it["crit"] else ""
            rl     = RATING_LABELS.get(r, "?")
            pts_str = f"[{r}/5] {it['pts_earned']}/{it['pts']}pt" if not is_q else f"[{r}] {rl}"
            txt_col = DARK if r >= 3 else (MUTED if r > 0 else C("#bbbbbb"))
            rows.append([
                Paragraph(f"{it['text']}{flag}",
                          S("ri", fontSize=8.5, fontName="Helvetica",
                            textColor=txt_col, leftIndent=4)),
                Paragraph(pts_str,
                          S("rp", fontSize=8, fontName="Helvetica",
                            textColor=rclr(r), alignment=TA_RIGHT)),
            ])

        t_rows = Table(rows, colWidths=[13*cm, 3.6*cm])
        t_rows.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [WHITE, C("#fafafa")]),
            ("GRID",           (0,0), (-1,-1), 0.3, C("#e8e8e8")),
            ("TOPPADDING",     (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",  (0,0), (-1,-1), 5),
            ("LEFTPADDING",    (0,0), (-1,-1), 8),
            ("RIGHTPADDING",   (0,0), (-1,-1), 8),
        ]))

        block = [t_hdr, t_rows]
        note  = sd["note"].strip()
        if note:
            block.append(Paragraph(
                f"<i>Evaluator notes: {note}</i>",
                S("nt", fontSize=8.5, fontName="Helvetica-Oblique",
                  textColor=C("#555555"), leftIndent=8,
                  backColor=C("#fffbf0"), borderPadding=5, spaceAfter=2),
            ))
        block.append(Spacer(1, 8))
        story.append(KeepTogether(block))

    # Proctor observations
    obs = st.session_state.proctor_obs.strip()
    if obs:
        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=1,
                                 color=C("#dddddd"), spaceBefore=4, spaceAfter=8))
        story.append(Paragraph("Proctor Observations",
            S("poh", fontSize=11, fontName="Helvetica-Bold", textColor=DARK, spaceAfter=6)))
        story.append(Paragraph(obs.replace("\n", "<br/>"),
            S("pot", fontSize=9, fontName="Helvetica",
              textColor=C("#333333"), leading=14, leftIndent=6)))
        story.append(Spacer(1, 14))

    # AI Recommendations
    if recs:
        story.append(HRFlowable(width="100%", thickness=1.5,
                                 color=PURPLE, spaceBefore=8, spaceAfter=10))
        story.append(Paragraph("AI-Powered Coaching Report",
            S("arh", fontSize=13, fontName="Helvetica-Bold", textColor=PURPLE, spaceAfter=8)))
        for line in recs.split("\n"):
            s = line.strip()
            if not s:
                story.append(Spacer(1, 4))
                continue
            if s.startswith("### "):
                story.append(Spacer(1, 6))
                story.append(Paragraph(s[4:],
                    S("ah", fontSize=10, fontName="Helvetica-Bold",
                      textColor=DARK, spaceAfter=3)))
            elif s.startswith("- ") or s.startswith("• "):
                story.append(Paragraph("•  " + s[2:],
                    S("ai", fontSize=9, fontName="Helvetica",
                      textColor=C("#333333"), leftIndent=14, leading=13)))
            else:
                clean = s.replace("**", "")
                story.append(Paragraph(clean,
                    S("ap", fontSize=9, fontName="Helvetica",
                      textColor=C("#333333"), leading=13, spaceAfter=2)))
        story.append(Spacer(1, 10))

    # Footer
    story.append(HRFlowable(width="100%", thickness=1,
                             color=C("#dddddd"), spaceBefore=8, spaceAfter=8))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%B %d, %Y at %H:%M')} "
        f"· Ruvixx LATAM CI-QA · Proctor: {hd.get('proctor', '')}",
        S("ft", fontSize=8, fontName="Helvetica",
          textColor=C("#aaaaaa"), alignment=TA_CENTER),
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

.block-container {
    padding-top:    1.5rem   !important;
    padding-bottom: 160px    !important;
    max-width:      860px    !important;
}

[data-testid="stSidebar"] {
    border-right: 1px solid rgba(0,0,0,0.08);
}
[data-theme="dark"] [data-testid="stSidebar"] {
    border-right: 1px solid rgba(255,255,255,0.08);
}

.osint-header-bar {
    background:    #EEEDFE;
    border-left:   4px solid #534AB7;
    border-radius: 0 10px 10px 0;
    padding:       10px 16px;
    font-size:     13px;
    color:         #2a2060;
}
[data-theme="dark"] .osint-header-bar {
    background: #1e1a3e;
    color:      #c8c3ff;
}

.osint-card-foot {
    border:        1px solid rgba(0,0,0,0.08);
    border-top:    none;
    border-radius: 0 0 14px 14px;
    height:        10px;
    background:    rgba(255,255,255,0.5);
}
[data-theme="dark"] .osint-card-foot {
    border-color: rgba(255,255,255,0.08);
    background:   rgba(0,0,0,0.15);
}

.osint-sec-header {
    border-radius: 14px 14px 0 0;
    padding:       12px 20px;
    display:       flex;
    justify-content: space-between;
    align-items:   center;
    margin-top:    1.8rem;
}

/* Rating legend pills */
.rating-legend { display:flex; gap:8px; flex-wrap:wrap; margin:0.6rem 0 0.8rem; }
.rl-pill {
    font-size:11px; font-weight:600; padding:3px 10px;
    border-radius:20px; color:white; white-space:nowrap;
}

/* Item text in evaluation rows */
.item-text { font-size:13px; line-height:1.45; padding-top:14px; }
.item-crit { font-size:10px; font-weight:600; color:#e03030; margin-left:6px; }

/* AI Recs box */
.ai-recs-box {
    background:    #f8f6ff;
    border:        1.5px solid #534AB7;
    border-radius: 14px;
    padding:       20px 24px;
    margin-top:    0.5rem;
    font-size:     14px;
    line-height:   1.65;
    color:         #1a1a2e;
}
[data-theme="dark"] .ai-recs-box {
    background: #14112a;
    color:      #e0dcff;
}

.obs-header {
    font-size:16px; font-weight:600;
    margin-bottom:6px; color:#534AB7;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# HIDDEN TIMER TOGGLE
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.form_generated:
    _toggle = st.button("TIMER_TOGGLE_HIDDEN__", key="fab_trigger_btn")
    if _toggle:
        _do_timer_toggle()
        st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# FLOATING FAB
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.form_generated:
    _e   = int(st.session_state.elapsed_seconds)
    _r   = "true" if st.session_state.timer_running else "false"
    _ts  = (
        int(st.session_state.start_time.timestamp())
        if st.session_state.timer_running and st.session_state.start_time else 0
    )
    _status       = "RUNNING" if st.session_state.timer_running else ("PAUSED" if _e > 0 else "READY")
    _status_color = "#4CAF50" if st.session_state.timer_running else ("#FF9800" if _e > 0 else "#888888")
    _btn_label    = "⏸  Pause Exam" if st.session_state.timer_running else (
                    "▶  Resume"     if _e > 0 else "▶  Start Exam")
    _btn_bg       = "#c0392b" if st.session_state.timer_running else "#534AB7"
    _pulse        = "animation:fabPulseRed 1.8s infinite;" if st.session_state.timer_running else \
                    "animation:fabPulseBlue 3s infinite;"

    st.markdown(f"""
<style>
#osint-fab{{position:fixed;bottom:24px;right:24px;z-index:99999;
  display:flex;flex-direction:column;align-items:flex-end;gap:10px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}}
#osint-fab-timer{{background:#1a1a2e;color:white;border-radius:18px;
  padding:14px 22px;text-align:center;
  box-shadow:0 8px 32px rgba(0,0,0,.45);min-width:138px;user-select:none;}}
#osint-fab-timer .fab-lbl{{font-size:10px;color:#888;letter-spacing:2px;margin-bottom:6px;}}
#osint-fab-timer .fab-time{{font-size:2.3rem;font-weight:700;font-family:monospace;letter-spacing:4px;}}
#osint-fab-timer .fab-st{{font-size:10px;font-weight:600;margin-top:6px;
  letter-spacing:1px;color:{_status_color};}}
#osint-fab-action{{background:{_btn_bg};color:white;border:none;border-radius:50px;
  padding:13px 28px;font-size:14px;font-weight:600;cursor:pointer;
  {_pulse} transition:transform .15s ease,background .2s ease;white-space:nowrap;}}
#osint-fab-action:hover{{transform:scale(1.06);}}
#osint-fab-action:active{{transform:scale(0.96);}}
@keyframes fabPulseRed{{
  0%,100%{{box-shadow:0 6px 24px rgba(192,57,43,.55);}}
  50%{{box-shadow:0 6px 42px rgba(192,57,43,.95);}}
}}
@keyframes fabPulseBlue{{
  0%,100%{{box-shadow:0 6px 24px rgba(83,74,183,.50);}}
  50%{{box-shadow:0 6px 38px rgba(83,74,183,.85);}}
}}
</style>
<div id="osint-fab">
  <div id="osint-fab-timer">
    <div class="fab-lbl">EXAM TIMER</div>
    <div class="fab-time" id="osint-time-el">00:00</div>
    <div class="fab-st">● {_status}</div>
  </div>
  <button id="osint-fab-action" onclick="fabToggle()">{_btn_label}</button>
</div>
<script>
(function(){{
  var base={_e},running={_r},since={_ts};
  function fmt(s){{s=Math.max(0,s);var m=Math.floor(s/60),sec=s%60;
    return String(m).padStart(2,'0')+':'+String(sec).padStart(2,'0');}}
  function tick(){{var t=base;if(running&&since>0)t+=Math.floor(Date.now()/1000-since);
    var el=document.getElementById('osint-time-el');if(el)el.textContent=fmt(t);}}
  tick();setInterval(tick,500);
}})();
function fabToggle(){{
  var all=document.querySelectorAll('button');
  for(var i=0;i<all.length;i++){{
    if(all[i].innerText&&all[i].innerText.trim()==='TIMER_TOGGLE_HIDDEN__'){{all[i].click();return;}}
  }}
}}
(function hideToggle(){{
  function tryHide(){{
    var all=document.querySelectorAll('button');
    for(var i=0;i<all.length;i++){{
      if(all[i].innerText&&all[i].innerText.trim()==='TIMER_TOGGLE_HIDDEN__'){{
        var el=all[i];
        for(var j=0;j<8;j++){{
          if(!el.parentElement)break;el=el.parentElement;
          if(el.getAttribute&&el.getAttribute('data-testid')==='stButton'){{
            el.style.cssText+='position:fixed!important;left:-9999px!important;'+
              'height:0!important;overflow:hidden!important;opacity:0!important;';
            return true;
          }}
        }}
      }}
    }}
    return false;
  }}
  if(!tryHide()){{
    var obs=new MutationObserver(function(){{if(tryHide())obs.disconnect();}});
    obs.observe(document.body,{{childList:true,subtree:true}});
    setTimeout(function(){{tryHide();obs.disconnect();}},2000);
  }}
}})();
</script>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 OSINT CI LATAM")
    if st.session_state.form_generated:
        scored, _ = calc_scores()
        pct = round(scored / MAX_PTS * 100)
        vt, vc, _, _ = verdict(pct)
        st.metric("Score", f"{scored} / {MAX_PTS}", f"{pct}%")
        st.progress(pct / 100)
        st.markdown(
            f'<span style="color:{vc};font-weight:600;font-size:14px;">{vt}</span>',
            unsafe_allow_html=True,
        )
        if st.session_state.recs_ready:
            st.caption("🤖 AI report ready")
        if st.session_state.pdf_ready:
            st.caption("📄 PDF ready")
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
        st.selectbox("Region *", ["MCC — México Central Caribe", "CS — Cono Sur"], key="inp_region")
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
        if st.button("Generate Form  ▶", type="primary",
                     use_container_width=True, disabled=not _ready):
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
    hd = st.session_state.header_data
    c_info, c_edit = st.columns([5, 1])
    with c_info:
        st.markdown(
            f'<div class="osint-header-bar">'
            f'<b>{hd.get("investigator","")}</b> &nbsp;·&nbsp;'
            f'{hd.get("region","")} &nbsp;·&nbsp;'
            f'Exam #{hd.get("exam_number","")} &nbsp;·&nbsp;'
            f'{hd.get("date","")} &nbsp;·&nbsp;'
            f'Proctor: {hd.get("proctor","")}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c_edit:
        if st.button("✏️ Edit", use_container_width=True):
            for _k in ["form_generated","timer_running","elapsed_seconds",
                       "start_time","timer_events","pdf_bytes","pdf_ready",
                       "scores","notes","proctor_obs","ai_recs","recs_ready"]:
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

    # Rating legend
    pills = "".join(
        f'<span class="rl-pill" style="background:{RATING_COLORS[r]};">'
        f'{r} — {RATING_LABELS[r]}</span>'
        for r in range(6)
    )
    st.markdown(f'<div class="rating-legend">{pills}</div>', unsafe_allow_html=True)
    st.caption("Rate each item 0–5. Score is proportional to rating (5 = full points). 🔴 = critical item.")

    for sec in SECTIONS:
        is_q = sec.get("qualitative", False)

        # Live section score
        sec_scored = 0
        if not is_q:
            for i in range(len(sec["items"])):
                r = int(st.session_state.scores.get(f"{sec['id']}_{i}", 0))
                sec_scored += round(r / 5 * sec["items"][i]["pts"])
        pts_display = "Qualitative" if is_q else f"{sec_scored} / {sec['points']} pts"

        st.markdown(
            f'<div class="osint-sec-header" style="background:{sec["color"]};">'
            f'<span style="color:white;font-weight:600;font-size:15px;">{sec["title"]}</span>'
            f'<span style="color:rgba(255,255,255,.9);font-size:13px;">{pts_display}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        with st.container():
            for i, it in enumerate(sec["items"]):
                key     = f"{sec['id']}_{i}"
                cur_val = int(st.session_state.scores.get(key, 0))
                crit_html = ' <span class="item-crit">🔴 critical</span>' if it["crit"] else ""

                col_t, col_s, col_b = st.columns([5, 5, 1])

                with col_t:
                    st.markdown(
                        f'<div class="item-text">{it["text"]}{crit_html}</div>',
                        unsafe_allow_html=True,
                    )

                with col_s:
                    rating = st.select_slider(
                        label="rating",
                        options=[0, 1, 2, 3, 4, 5],
                        value=cur_val,
                        format_func=lambda x: RATING_FMT[x],
                        key=f"sl_{key}",
                        label_visibility="collapsed",
                    )
                    st.session_state.scores[key] = rating
                    lbl_color = RATING_COLORS[rating]
                    lbl_text  = RATING_LABELS[rating]
                    st.markdown(
                        f'<div style="text-align:center;font-size:11px;font-weight:600;'
                        f'color:{lbl_color};margin-top:-8px;">{lbl_text}</div>',
                        unsafe_allow_html=True,
                    )

                with col_b:
                    if not is_q and it["pts"] > 0:
                        pts_e = round(rating / 5 * it["pts"])
                        clr   = RATING_COLORS[rating]
                        st.markdown(
                            f'<div style="text-align:right;font-size:11px;font-weight:700;'
                            f'color:{clr};padding-top:14px;line-height:1.3;">'
                            f'{pts_e}'
                            f'<span style="opacity:.45;font-weight:400;font-size:10px;">/{it["pts"]}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

            st.markdown("")
            note = st.text_area(
                "Evaluator notes:",
                key=f"note_{sec['id']}",
                value=st.session_state.notes.get(sec["id"], ""),
                height=68,
                placeholder="Observations, tools used, case-specific details...",
            )
            st.session_state.notes[sec["id"]] = note

        st.markdown('<div class="osint-card-foot"></div>', unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PROCTOR OBSERVATIONS
    # ─────────────────────────────────────────────────────────────────────────
    st.divider()
    st.markdown('<div class="obs-header">🗒️ Proctor Observations</div>', unsafe_allow_html=True)
    st.caption("Workflow, pace, tools used, attitude, and anything not captured in the rubric.")
    obs_val = st.text_area(
        label="obs",
        label_visibility="collapsed",
        key="obs_input",
        value=st.session_state.proctor_obs,
        height=120,
        placeholder=(
            "e.g. Investigator started by reading all machines before entity. "
            "Used LinkedIn effectively but skipped WHOIS. "
            "Struggled with IP filtering in Pleteo. Finished in 38 min..."
        ),
    )
    st.session_state.proctor_obs = obs_val

    # ─────────────────────────────────────────────────────────────────────────
    # FINAL SCORE CARD
    # ─────────────────────────────────────────────────────────────────────────
    st.divider()
    scored, _ = calc_scores()
    pct       = round(scored / MAX_PTS * 100)
    vt, vc, vbg, vtc = verdict(pct)
    elapsed   = get_elapsed()

    st.markdown(
        f"""
        <div style="
            background:linear-gradient(135deg,#534AB7 0%,#7F77DD 100%);
            border-radius:18px;padding:28px 32px;
            color:white;text-align:center;margin:1rem 0 0.5rem;
        ">
          <div style="font-size:12px;opacity:.7;letter-spacing:1.5px;margin-bottom:6px;">FINAL SCORE</div>
          <div style="font-size:3.5rem;font-weight:700;line-height:1.1;">
            {scored}
            <span style="font-size:1.6rem;opacity:.55;margin-left:6px;">/ {MAX_PTS}</span>
          </div>
          <div style="font-size:1.25rem;opacity:.85;margin-top:4px;">{pct}%</div>
          <div style="
              margin-top:14px;display:inline-block;
              background:{vbg};color:{vtc};
              padding:6px 26px;border-radius:20px;
              font-weight:600;font-size:14px;letter-spacing:.5px;
          ">{vt}</div>
          <div style="margin-top:10px;opacity:.65;font-size:13px;">⏱ Time used: {fmt_time(elapsed)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # ACTION BUTTONS — 3-column row
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("")
    col_ai, col_gen, col_dl = st.columns(3)

    with col_ai:
        if st.button("🤖 AI Recommendations", use_container_width=True, type="secondary"):
            with st.spinner("Analyzing results and generating coaching report…"):
                st.session_state.ai_recs    = build_ai_recommendations()
                st.session_state.recs_ready = True
                st.session_state.pdf_ready  = False  # invalidate PDF; needs regen
            st.rerun()

    with col_gen:
        _gen_label = "📊 Generate PDF Result" if not st.session_state.recs_ready \
                     else "📊 Generate PDF + Report"
        if st.button(_gen_label, type="primary", use_container_width=True):
            if st.session_state.timer_running:
                st.session_state.elapsed_seconds += (
                    datetime.now() - st.session_state.start_time
                ).total_seconds()
                st.session_state.timer_running = False
                st.session_state.timer_events.append(
                    {"action": "stop", "ts": datetime.now().strftime("%H:%M:%S")}
                )
            with st.spinner("Building PDF…"):
                st.session_state.pdf_bytes = build_pdf()
                st.session_state.pdf_ready = True
            st.rerun()

    with col_dl:
        if st.session_state.pdf_ready and st.session_state.pdf_bytes:
            hd    = st.session_state.header_data
            fname = (
                f"OSINT_Exam_"
                f"{hd.get('investigator','').replace(' ','_')}_"
                f"Exam{hd.get('exam_number','')}_"
                f"{datetime.now().strftime('%Y%m%d')}.pdf"
            )
            st.download_button(
                "⬇️ Download PDF",
                data=st.session_state.pdf_bytes,
                file_name=fname,
                mime="application/pdf",
                use_container_width=True,
            )
            if st.session_state.recs_ready:
                st.success("✅ PDF includes AI report!")
            else:
                st.success("✅ PDF ready!")

    # ── AI Recommendations display ────────────────────────────────────────────
    if st.session_state.recs_ready and st.session_state.ai_recs:
        st.markdown("")
        st.markdown(
            '<div style="font-size:16px;font-weight:600;color:#534AB7;margin-bottom:8px;">'
            '🤖 AI Coaching Report</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="ai-recs-box">{st.session_state.ai_recs}</div>',
            unsafe_allow_html=True,
        )
        if not st.session_state.pdf_ready:
            st.caption("💡 Click **Generate PDF + Report** to include this in the PDF.")

    st.markdown("<div style='height:140px;'></div>", unsafe_allow_html=True)
