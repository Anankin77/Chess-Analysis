"""Look and feel: a walnut table generated with SVG turbulence (no image files)."""

from __future__ import annotations

import base64

BOARD_COLORS = {"square light": "#e8cfa6", "square dark": "#a5713f", "margin": "#24160c", "coord": "#e8cfa6",
                "square light lastmove": "#e8cfa6", "square dark lastmove": "#a5713f"}
RED, GREEN = "#d93a3add", "#81b64cee"


def _wood_svg(base: str, grain_rgb: tuple[float, float, float], seed: int, size: int = 900,
              freq: str = "0.0035 0.13", strength: float = 1.9) -> str:
    """Tileable wood grain: stretched fractal noise darkening a base colour."""
    r, g, b = grain_rgb
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}'>
<filter id='g' x='0' y='0' width='100%' height='100%'>
<feTurbulence type='fractalNoise' baseFrequency='{freq}' numOctaves='5' seed='{seed}' stitchTiles='stitch'/>
<feColorMatrix values='0 0 0 0 {r}  0 0 0 0 {g}  0 0 0 0 {b}  {strength} 0 0 0 -{strength * 0.36:.3f}'/>
</filter>
<filter id='k' x='0' y='0' width='100%' height='100%'>
<feTurbulence type='fractalNoise' baseFrequency='0.0012 0.011' numOctaves='2' seed='{seed + 3}' stitchTiles='stitch'/>
<feColorMatrix values='0 0 0 0 1  0 0 0 0 0.78  0 0 0 0 0.5  0.9 0 0 0 -0.38'/>
</filter>
<rect width='100%' height='100%' fill='{base}'/>
<rect width='100%' height='100%' filter='url(#k)' opacity='.35'/>
<rect width='100%' height='100%' filter='url(#g)'/>
</svg>"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def css() -> str:
    table = _wood_svg("#5a3820", (0.07, 0.035, 0.012), seed=11)
    ebony = _wood_svg("#2a190e", (0.02, 0.01, 0.0), seed=4, strength=1.6)
    return f"""
<style>
:root {{ --gold: #d9a441; --cream: #f6ead4; --green: #81b64c; --green-dark: #5d8a34; }}

/* ---- the table ---- */
.stApp {{
  background-color: #3a2415;
  background-image: radial-gradient(ellipse at 50% 0%, rgba(255,214,150,.13), rgba(0,0,0,.55) 95%), url("{table}");
  background-size: cover, 900px 900px;
  background-attachment: fixed;
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stSidebar"] {{
  background-image: linear-gradient(rgba(0,0,0,.35), rgba(0,0,0,.55)), url("{ebony}");
  background-size: cover, 900px 900px;
  border-right: 1px solid rgba(217,164,65,.25); box-shadow: 6px 0 24px rgba(0,0,0,.45);
}}
[data-testid="stSidebar"] blockquote {{ border-left: 3px solid var(--gold); color: #d9c7a8; font-size: .95rem; }}
[data-testid="stSidebarNavLink"] {{ border-radius: 8px; transition: transform .12s, background .12s; }}
[data-testid="stSidebarNavLink"]:hover {{ transform: translateX(4px); background: rgba(217,164,65,.14); }}

/* ---- type ---- */
h1 {{ color: var(--cream); letter-spacing: .5px; text-shadow: 0 2px 0 rgba(0,0,0,.55), 0 0 22px rgba(0,0,0,.4); }}
h1::after {{ content: ""; display: block; width: 72px; height: 4px; margin-top: 10px; border-radius: 2px;
            background: linear-gradient(90deg, var(--gold), rgba(217,164,65,0)); }}
h2, h3 {{ color: var(--cream); text-shadow: 0 1px 2px rgba(0,0,0,.6); }}

/* ---- inlaid panels ---- */
[data-testid="stMetric"], [data-testid="stExpander"], [data-testid="stDataFrame"], [data-testid="stAlert"],
.stCode, [data-testid="stVerticalBlockBorderWrapper"] {{
  background: linear-gradient(180deg, rgba(20,12,6,.72), rgba(20,12,6,.58));
  border: 1px solid rgba(217,164,65,.22); border-radius: 10px;
  box-shadow: inset 0 1px 0 rgba(255,225,170,.07), 0 6px 18px rgba(0,0,0,.35);
}}
[data-testid="stMetric"] {{ padding: 12px 16px; transition: transform .15s, box-shadow .15s, border-color .15s; }}
[data-testid="stMetric"]:hover {{ transform: translateY(-3px); border-color: rgba(217,164,65,.6);
  box-shadow: inset 0 1px 0 rgba(255,225,170,.1), 0 12px 26px rgba(0,0,0,.5); }}
[data-testid="stMetricValue"] {{ color: var(--gold); font-weight: 800; text-shadow: 0 1px 0 rgba(0,0,0,.6); }}
[data-testid="stExpander"] details {{ border: 0; }}

/* ---- buttons: chunky, pressable ---- */
.stButton > button, [data-testid="stBaseButton-secondary"] {{
  font-weight: 800; border-radius: 8px; border: 1px solid rgba(0,0,0,.4);
  background: linear-gradient(180deg, #6b4728, #4c3019); color: var(--cream);
  box-shadow: 0 4px 0 #2a190d, 0 8px 14px rgba(0,0,0,.35); transition: transform .08s, box-shadow .08s, filter .12s;
}}
.stButton > button:hover {{ filter: brightness(1.15); transform: translateY(-1px); color: #fff; border-color: var(--gold); }}
.stButton > button:active {{ transform: translateY(3px); box-shadow: 0 1px 0 #2a190d, 0 2px 6px rgba(0,0,0,.4); }}
.stButton > button[kind="primary"] {{
  background: linear-gradient(180deg, #95c95b, #6fa53c); color: #fff; border-color: #4c7a25;
  box-shadow: 0 4px 0 #45691f, 0 8px 16px rgba(0,0,0,.4); text-shadow: 0 1px 0 rgba(0,0,0,.3);
}}
.stButton > button[kind="primary"]:active {{ box-shadow: 0 1px 0 #45691f, 0 2px 6px rgba(0,0,0,.4); }}
.stButton > button:disabled {{ filter: grayscale(.7) brightness(.7); box-shadow: none; transform: none; }}

/* ---- step bar ---- */
.steps {{ display: flex; gap: 10px; margin: 0 0 18px; flex-wrap: wrap; }}
.steps a {{
  flex: 1 1 150px; display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: 10px;
  text-decoration: none !important; color: #d9c7a8 !important; font-weight: 700;
  background: linear-gradient(180deg, rgba(20,12,6,.7), rgba(20,12,6,.5)); border: 1px solid rgba(217,164,65,.2);
  box-shadow: 0 4px 12px rgba(0,0,0,.3); transition: transform .12s, border-color .12s, color .12s;
}}
.steps a:hover {{ transform: translateY(-3px); border-color: var(--gold); color: #fff !important; }}
.steps .n {{ width: 26px; height: 26px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; flex: 0 0 26px; font-size: .85rem; background: #4c3019; color: var(--cream); border: 1px solid rgba(217,164,65,.5); }}
.steps .on {{ border-color: var(--gold); color: #fff !important;
  background: linear-gradient(180deg, rgba(217,164,65,.28), rgba(217,164,65,.1)); }}
.steps .on .n {{ background: var(--gold); color: #2a190d; }}
.steps .done .n {{ background: var(--green-dark); border-color: var(--green); }}
.steps small {{ display: block; font-weight: 400; opacity: .75; font-size: .75rem; }}

/* ---- boards on the report ---- */
[data-testid="stImage"] img {{
  border-radius: 6px; border: 6px solid #24160c; outline: 1px solid rgba(217,164,65,.35);
  box-shadow: 0 10px 24px rgba(0,0,0,.55); transition: transform .18s, box-shadow .18s;
}}
[data-testid="stImage"] img:hover {{ transform: translateY(-4px) scale(1.015); box-shadow: 0 18px 34px rgba(0,0,0,.65); }}
[data-testid="stProgress"] > div > div > div > div {{ background: linear-gradient(90deg, var(--green-dark), var(--green)); }}
iframe[title*="chesscoach_board"] {{ border-radius: 8px; }}
</style>
"""
