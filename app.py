
import io
import re
import pandas as pd
import streamlit as st

# ---------- Reference column helper ----------
def find_reference_columns_from_df(df0: pd.DataFrame):
    default_cols = [
        "Scene #", "Scene Heading", "Beat/Action", "Shot #", "Shot Type",
        "Angle", "Movement", "Lens", "Duration (s)", "Location", "Characters", "Notes"
    ]
    try:
        cols = [str(c).strip() for c in df0.columns.tolist()]
        cols = [c for c in cols if c and not c.lower().startswith("unnamed")]
        return cols if cols else default_cols
    except Exception:
        return default_cols

# ---------- Minimal Fountain Parser ----------
SCENE_HEADING_RE = re.compile(r'^\s*(INT|EXT|INT/EXT|I/E)\.?\s', re.IGNORECASE)
TRANSITION_RE = re.compile(r'^\s*[A-Z \t]+TO:\s*$')
CHARACTER_RE = re.compile(r'^\s*[A-Z0-9 \-\(\)\'\.]+(?:\(.*\))?\s*$')

def is_scene_heading(line: str) -> bool:
    return bool(SCENE_HEADING_RE.match(line.strip()))

def is_transition(line: str) -> bool:
    return bool(TRANSITION_RE.match(line.strip()))

def is_character_cue(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 60:
        return False
    if is_scene_heading(s) or is_transition(s):
        return False
    alpha = sum(1 for ch in s if ch.isalpha())
    if alpha == 0:
        return False
    upper_ratio = sum(1 for ch in s if ch.isupper()) / alpha
    return upper_ratio > 0.8 and CHARACTER_RE.match(s) is not None

def parse_fountain_text(text: str):
    lines = text.splitlines()
    scenes = []
    current_scene = None
    buffer_action = []
    current_character = None

    def flush_action_as_beat():
        nonlocal buffer_action
        if buffer_action:
            t = " ".join(l.strip() for l in buffer_action if l.strip())
            if t:
                current_scene["beats"].append(("Action", t))
            buffer_action = []

    for raw in lines:
        line = raw.rstrip("\n")
        if is_scene_heading(line):
            if current_scene:
                flush_action_as_beat()
                scenes.append(current_scene)
            current_scene = {
                "scene_number": len(scenes) + 1,
                "scene_heading": line.strip(),
                "beats": [],
                "characters": set()
            }
            buffer_action = []
            current_character = None
            continue

        if current_scene is None:
            continue

        if not line.strip():
            flush_action_as_beat()
            current_character = None
            continue

        if is_character_cue(line):
            flush_action_as_beat()
            current_character = line.strip()
            current_scene["characters"].add(current_character)
            continue

        if current_character:
            txt = line.strip()
            if txt:
                current_scene["beats"].append(("Dialogue", f"{current_character}: {txt}"))
            continue

        if is_transition(line):
            flush_action_as_beat()
            continue

        buffer_action.append(line)

    if current_scene:
        flush_action_as_beat()
        scenes.append(current_scene)

    return scenes

# ---------- Shotlist Builder ----------
def build_shotlist_from_scenes(scenes, columns):
    possible_map = {
        "Scene #": ["Scene #", "Scene", "Scene No", "Scene Number"],
        "Scene Heading": ["Scene Heading", "Heading", "Slugline"],
        "Beat/Action": ["Beat/Action", "Beat", "Action", "Description", "What Happens"],
        "Shot #": ["Shot #", "Shot Number", "Shot"],
        "Shot Type": ["Shot Type", "Type"],
        "Angle": ["Angle"],
        "Movement": ["Movement", "Move"],
        "Lens": ["Lens"],
        "Duration (s)": ["Duration (s)", "Duration", "Est. Duration (s)"],
        "Location": ["Location"],
        "Characters": ["Characters", "Cast"],
        "Notes": ["Notes"]
    }
    col_map = {}
    columns = list(columns)  # copy
    for logical, aliases in possible_map.items():
        match = None
        for alias in aliases:
            for c in columns:
                if c.strip().lower() == alias.strip().lower():
                    match = c
                    break
            if match:
                break
        if match is None:
            match = logical
            if match not in columns:
                columns.append(match)
        col_map[logical] = match

    seen = set()
    uniq_cols = []
    for c in columns:
        if c not in seen:
            uniq_cols.append(c)
            seen.add(c)

    rows = []
    for sc in scenes:
        scene_num = sc["scene_number"]
        heading = sc["scene_heading"]
        char_list = ", ".join(sorted(sc["characters"])) if sc["characters"] else ""
        shot_counter = 1
        for kind, beat_text in sc["beats"]:
            row = {c: "" for c in uniq_cols}
            row[col_map["Scene #"]] = scene_num
            row[col_map["Scene Heading"]] = heading
            row[col_map["Beat/Action"]] = f"[{kind}] {beat_text}"
            row[col_map["Shot #"]] = shot_counter
            row[col_map["Shot Type"]] = "MS"
            row[col_map["Angle"]] = "Eye-level"
            row[col_map["Movement"]] = "Static"
            row[col_map["Lens"]] = "35mm"
            loc = ""
            try:
                head_no_prefix = re.sub(r'^\s*(INT|EXT|INT/EXT|I/E)\.?\s*', '', heading, flags=re.IGNORECASE)
                loc = re.split(r'\s*-\s*', head_no_prefix)[0].strip()
            except Exception:
                pass
            row[col_map["Location"]] = loc
            row[col_map["Characters"]] = char_list
            row[col_map["Notes"]] = ""
            rows.append(row)
            shot_counter += 1

    df = pd.DataFrame(rows, columns=uniq_cols)
    return df

# ---------- UI ----------
st.set_page_config(page_title="Fountain ➜ Shotlist Converter", layout="wide")
st.title("🎬 Fountain ➜ Shotlist Converter")
st.write("Upload your screenplay in `.fountain`, optionally upload a reference Excel (to mirror columns), and download CSV/XLSX shotlists.")

ref_file = st.file_uploader("Optional: Upload reference Excel to mirror column names", type=["xlsx"], key="ref")
if ref_file is not None:
    try:
        ref_df = pd.read_excel(ref_file, sheet_name=0, header=0)
        reference_columns = find_reference_columns_from_df(ref_df)
        st.success(f"Reference columns detected: {reference_columns}")
    except Exception as e:
        st.warning(f"Could not read reference Excel: {e}")
        reference_columns = find_reference_columns_from_df(pd.DataFrame())
else:
    reference_columns = find_reference_columns_from_df(pd.DataFrame())

up_files = st.file_uploader("Upload one or more .fountain files", type=["fountain"], accept_multiple_files=True, key="fountain")

if up_files:
    for upl in up_files:
        name = upl.name
        text = upl.read().decode("utf-8", errors="ignore")
        scenes = parse_fountain_text(text)
        df = build_shotlist_from_scenes(scenes, reference_columns)
        st.subheader(f"Shotlist Preview — {name} ({len(df)} rows)")
        st.dataframe(df.head(50), use_container_width=True)

        # CSV buffer
        csv_buf = io.StringIO()
        df.to_csv(csv_buf, index=False)
        st.download_button(
            label=f"⬇️ Download CSV for {name}",
            data=csv_buf.getvalue(),
            file_name=f"{name.rsplit('.',1)[0]}_shotlist.csv",
            mime="text/csv",
        )

        # XLSX buffer
        xlsx_buf = io.BytesIO()
        with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Shotlist")
        xlsx_buf.seek(0)
        st.download_button(
            label=f"⬇️ Download Excel for {name}",
            data=xlsx_buf,
            file_name=f"{name.rsplit('.',1)[0]}_shotlist.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
else:
    st.info("Drag & drop your `.fountain` files above to begin.")
