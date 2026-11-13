import io
import base64
import zipfile
import html
import os
import re
import socket
import sqlite3
import unicodedata
from datetime import datetime

import pandas as pd
import qrcode
import streamlit as st

st.set_page_config(page_title="Геологиялық экспедиция", page_icon="🛢️", layout="wide")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.db")
TEACHER_PASSWORD = os.environ.get("TEACHER_PASSWORD", "12345")
ORAL_CRITERIA = [("Сұраққа нақты жауап беру", 4), ("Мұнай мен газдың түзілуін ғылыми түсіндіру", 5), ("Қалыптасу кезеңдерін ретімен айту", 5), ("Геологиялық терминдерді қолдану", 3), ("Жауапты жүйелі жеткізу", 3)]

# Change these tasks to suit the homework topic.
CHOICE = [
    {"q": "Петрофизика нені зерттейді?", "options": ["Тау жыныстарының физикалық қасиеттерін", "Ауа райын", "Өсімдіктердің өсуін", "Аспан денелерін"], "correct": 0},
    {"q": "Кеуектілік дегеніміз не?", "options": ["Жыныстың түсі", "Жыныстағы бос кеңістіктің үлесі", "Жыныстың жасы", "Жыныстың қаттылығы"], "correct": 1},
    {"q": "Өткізгіштік нені сипаттайды?", "options": ["Жыныстың жылу сыйымдылығын", "Жыныстың магниттілігін", "Флюидтердің жыныс арқылы өту қабілетін", "Жыныстың түсін"], "correct": 2},
]

# Each row is a matching pair. Every correct pair earns one point.
MATCH = [
    ("Кеуектілік", "Бос кеңістіктің көлемдік үлесі"),
    ("Өткізгіштік", "Флюидті өткізу қабілеті"),
    ("Коллектор", "Флюидтерді жинап, өткізе алатын жыныс"),
    ("Жапқыш жыныс", "Көмірсутектердің қозғалуын тежейтін қабат"),
]

# Short answers are checked by exact normalized keywords/phrases.
# These are NOT AI semantic checks: synonyms must be added explicitly.
SHORT = [
    {"q": "Жыныс кеуектерін толтыратын мұнай, газ және су жалпы қалай аталады?", "accepted": ["флюид", "флюидтер", "қабат флюидтері", "қабаттық флюидтер"]},
    {"q": "Мұнай мен газ жиналатын кеуекті әрі өткізгіш жыныс қалай аталады?", "accepted": ["коллектор", "коллектор жыныс", "коллектор жынысы", "коллекторлық жыныс"]},
]

TOTAL = len(CHOICE) + len(MATCH) + len(SHORT)


def normalize(value):
    value = unicodedata.normalize("NFKC", value).lower().strip()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def initialize():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                student_key TEXT NOT NULL UNIQUE,
                choice_score INTEGER NOT NULL,
                match_score INTEGER NOT NULL,
                short_score INTEGER NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                percentage REAL NOT NULL,
                grade INTEGER NOT NULL,
                submitted_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oral_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                question TEXT NOT NULL,
                criterion_1 INTEGER NOT NULL,
                criterion_2 INTEGER NOT NULL,
                criterion_3 INTEGER NOT NULL,
                criterion_4 INTEGER NOT NULL,
                criterion_5 INTEGER NOT NULL,
                score INTEGER NOT NULL,
                percentage REAL NOT NULL,
                letter_grade TEXT NOT NULL,
                grade INTEGER NOT NULL,
                submitted_at TEXT NOT NULL
            )
        """)


def letter_for(pct):
    for minimum, letter in [(95, "A"), (90, "A−"), (85, "B+"), (80, "B"), (75, "B−"), (70, "C+"), (65, "C"), (60, "C−"), (55, "D+"), (50, "D")]:
        if pct >= minimum:
            return letter
    return "F"


def oral_grade_for(pct):
    if pct >= 90:
        return 5
    if pct >= 70:
        return 4
    if pct >= 50:
        return 3
    return 2


def get_oral_results():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM oral_results ORDER BY submitted_at DESC, id DESC", conn)


def init_presentations():
    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS presentations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            presenter TEXT NOT NULL,
            filename TEXT NOT NULL,
            filetype TEXT NOT NULL,
            content BLOB NOT NULL,
            uploaded_at TEXT NOT NULL
        )""")


def get_presentations():
    with get_conn() as conn:
        return conn.execute("SELECT id, group_name, presenter, filename, filetype, uploaded_at FROM presentations ORDER BY id DESC").fetchall()


def get_presentation_file(presentation_id):
    with get_conn() as conn:
        return conn.execute("SELECT filename, filetype, content FROM presentations WHERE id=?", (presentation_id,)).fetchone()


def validate_presentation(filename, content):
    suffix = os.path.splitext(filename.lower())[1]
    if suffix == '.pdf':
        return content.startswith(b'%PDF-')
    if suffix == '.pptx':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                return '[Content_Types].xml' in z.namelist() and 'ppt/presentation.xml' in z.namelist()
        except (zipfile.BadZipFile, OSError):
            return False
    return False


def grade_for(pct):
    if pct >= 90:
        return 5
    if pct >= 70:
        return 4
    if pct >= 50:
        return 3
    return 2


def get_results():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM results ORDER BY submitted_at DESC, id DESC", conn)


def qr_png(url):
    image = qrcode.make(url)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def guess_lan_ip():
    # No packets are sent: UDP connect only selects a local interface.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


initialize()
init_presentations()

# Сабақтың төрт кезеңі: 20 + 30 + 30 + 20 = 100.
OIL_STEPS = [
    "Геологиялық деректерді жинау және талдау",
    "Геофизикалық (сейсмикалық) барлау жүргізу",
    "Перспективалы тұтқыш құрылымды анықтау",
    "Барлау ұңғымасын бұрғылау",
    "Ұңғыма нәтижелерін зерттеп, қорды бағалау",
]
ORAL_SIX = [
    "Нейтрондардың тау жыныстарымен әрекеттесуін түсіндіріңіз.",
    "Гамма-кванттардың тау жыныстарымен әрекеттесуін түсіндіріңіз.",
    "Ядролық магниттік прецессия дегеніміз не?",
    "Кеуектілік дегеніміз не және оның түрлері қандай?",
    "Өткізгіштік дегеніміз не және оның түрлері қандай?",
    "Поляризация құбылысы дегеніміз не?",
]
TEAM_NAMES = ["1-топ — Геофизиктер", "2-топ — Петрофизиктер", "3-топ — Зерттеушілер"]
GROUP_CRITERIA = [("Ғылыми дұрыстық", 10), ("Презентация мазмұны мен көрнекілігі", 8), ("Топ мүшелерінің қатысуы", 6), ("Қорғау сапасы", 6)]


# Student names are loaded from private Streamlit Secrets (not public GitHub).
def roster_names():
    raw = st.secrets.get("STUDENT_NAMES", "")
    if isinstance(raw, str):
        return list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip()))
    return list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))

def class_name():
    return str(st.secrets.get("CLASS_GROUP", "ФК-41А")).strip() or "ФК-41А"

def roster_select(label, key):
    names = roster_names()
    if names:
        return st.selectbox(label, names, index=None, placeholder="Тізімнен таңдаңыз", key=key)
    st.warning("Студенттер тізімі әлі қосылмаған. Streamlit Cloud Secrets бөліміне STUDENT_NAMES енгізіңіз.")
    return st.text_input(label, key=key)

def chosen_name(value):
    return (value or "").strip()

def student_key_for(name, group):
    return normalize(group) + "|" + normalize(name)

def init_lesson():
    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS oil_discovery (
            student_key TEXT PRIMARY KEY, student_name TEXT NOT NULL, group_name TEXT NOT NULL,
            team_name TEXT NOT NULL, score INTEGER NOT NULL, submitted_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS six_answers (
            student_key TEXT PRIMARY KEY, student_name TEXT NOT NULL, group_name TEXT NOT NULL,
            team_name TEXT NOT NULL, q1 INTEGER NOT NULL, q2 INTEGER NOT NULL,
            q3 INTEGER NOT NULL, q4 INTEGER NOT NULL, q5 INTEGER NOT NULL,
            q6 INTEGER NOT NULL, score INTEGER NOT NULL, submitted_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS student_teams (student_key TEXT PRIMARY KEY, team_name TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS group_assessment (
            group_name TEXT NOT NULL, team_name TEXT NOT NULL,
            k1 INTEGER NOT NULL, k2 INTEGER NOT NULL, k3 INTEGER NOT NULL,
            k4 INTEGER NOT NULL, score INTEGER NOT NULL, submitted_at TEXT NOT NULL,
            PRIMARY KEY (group_name, team_name))""")

def read_lesson_table(table):
    allowed = {"oil_discovery", "six_answers", "group_assessment"}
    if table not in allowed:
        raise ValueError("Unknown table")
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM " + table, conn)

def final_scores():
    oil = read_lesson_table("oil_discovery")
    oral = read_lesson_table("six_answers")
    quiz = get_results()
    teams = read_lesson_table("group_assessment")
    students = {student_key_for(n, class_name()): {"Аты-жөні": n, "Сынып/топ": class_name(), "Команда": ""} for n in roster_names()}
    with get_conn() as conn:
        assigned = conn.execute("SELECT student_key, team_name FROM student_teams").fetchall()
    assignments = dict(assigned)
    for df in (oil, oral, quiz):
        for _, r in df.iterrows():
            key = r["student_key"]
            students.setdefault(key, {"Аты-жөні": r["student_name"], "Сынып/топ": r["group_name"], "Команда": ""})
            if "team_name" in r and pd.notna(r["team_name"]):
                students[key]["Команда"] = r["team_name"]
    for k, info in students.items():
        if k in assignments:
            info["Команда"] = assignments[k]
    rows = []
    oil_map = {r["student_key"]: int(r["score"]) for _, r in oil.iterrows()}
    oral_map = {r["student_key"]: int(r["score"]) for _, r in oral.iterrows()}
    quiz_map = {r["student_key"]: round(float(r["score"]) / float(r["total"]) * 20, 1) for _, r in quiz.iterrows() if r["total"]}
    team_map = {(normalize(r["group_name"]), r["team_name"]): int(r["score"]) for _, r in teams.iterrows()}
    for key, info in students.items():
        team = info["Команда"]
        team_key = (normalize(info["Сынып/топ"]), team)
        group_points = team_map.get(team_key) if team else None
        parts = [oil_map.get(key), oral_map.get(key), group_points, quiz_map.get(key)]
        total = round(sum(x for x in parts if x is not None), 1)
        completed = sum(x is not None for x in parts)
        rows.append({**info, "Мұнай іздеу /20": parts[0], "6 сұрақ /30": parts[1],
                     "Топтық /30": parts[2], "Бекіту /20": parts[3],
                     "Жиыны /100": total, "Кезең": f"{completed}/4",
                     "Әріптік баға": letter_for(total) if completed == 4 else "Аяқталмаған",
                     "Баға": grade_for(total) if completed == 4 else "—"})
    return pd.DataFrame(rows)

init_lesson()

st.markdown("""
<style>
.block-container {max-width: 1050px; padding-top: 2rem;}
[data-testid="stMetric"] {background: #edf4f8; border-radius: 12px; padding: 14px;}
</style>
""", unsafe_allow_html=True)
st.title("🛢️ Геологиялық экспедиция")
st.caption("4 кезең: мұнайды іздеу /20 • 6 сұрақ /30 • топтық жұмыс /30 • бекіту /20")

student_tab, oil_tab, upload_tab, teacher_tab = st.tabs(["📱 Бекіту тесті", "🛢️ Мұнайды қалай табамыз?", "📤 Презентация жіберу", "👩‍🏫 Мұғалім"])

with student_tab:
    if st.session_state.get("completed"):
        result = st.session_state["result"]
        st.success("Жауабыңыз қабылданды!")
        st.subheader(f"{result['name']}, нәтижеңіз")
        c1, c2, c3 = st.columns(3)
        c1.metric("Ұпай", f"{result['score']}/{TOTAL}")
        c2.metric("Нәтиже", f"{result['pct']:.1f}%")
        c3.metric("Баға", str(result["grade"]))
        st.info("Бұл құрылғыда тапсырма аяқталды. Нәтиже мұғалім кестесіне сақталды.")
    else:
        st.write("Барлық тапсырманы орындап, төмендегі **Жіберу** батырмасын басыңыз.")
        with st.form("homework_form"):
            name = roster_select("Аты-жөніңіз", "quiz_roster_name")
            group = class_name()
            st.caption(f"Оқу тобы: {group}")
            st.divider()
            st.subheader("1-бөлім. Дұрыс жауапты таңдаңыз")
            selected = []
            for i, item in enumerate(CHOICE):
                selected.append(st.radio(
                    f"{i+1}. {item['q']}",
                    range(len(item["options"])),
                    format_func=lambda j, opts=item["options"]: opts[j],
                    index=None,
                    key=f"choice_{i}",
                ))
            st.divider()
            st.subheader("2-бөлім. Сәйкестендіріңіз")
            st.caption("Әр терминге сәйкес анықтаманы таңдаңыз. Әр дұрыс жұп — 1 ұпай.")
            definitions = [definition for _, definition in MATCH]
            matched = []
            for i, (term, _) in enumerate(MATCH):
                matched.append(st.selectbox(
                    f"{i+1}. {term}",
                    options=definitions,
                    index=None,
                    placeholder="Анықтаманы таңдаңыз",
                    key=f"match_{i}",
                ))
            st.divider()
            st.subheader("3-бөлім. Қысқа жауап жазыңыз")
            st.caption("Жауапты бір сөзбен немесе қысқа сөз тіркесімен жазыңыз.")
            short_answers = []
            for i, item in enumerate(SHORT):
                short_answers.append(st.text_input(f"{i+1}. {item['q']}", key=f"short_{i}"))
            submitted = st.form_submit_button("Жауаптарды жіберу", type="primary", use_container_width=True)

        if submitted:
            if not chosen_name(name) or not group.strip():
                st.error("Аты-жөніңізді және тобыңызды толтырыңыз.")
            elif any(x is None for x in selected) or any(x is None for x in matched) or any(not x.strip() for x in short_answers):
                st.error("Барлық сұраққа жауап беріңіз.")
            else:
                choice_score = sum(a == item["correct"] for a, item in zip(selected, CHOICE))
                match_score = sum(a == definition for a, (_, definition) in zip(matched, MATCH))
                short_score = sum(normalize(a) in {normalize(v) for v in item["accepted"]} for a, item in zip(short_answers, SHORT))
                score = choice_score + match_score + short_score
                pct = round(100 * score / TOTAL, 1)
                grade = grade_for(pct)
                key = student_key_for(name, group)
                try:
                    with get_conn() as conn:
                        conn.execute("""
                            INSERT INTO results
                            (student_name, group_name, student_key, choice_score, match_score, short_score,
                             score, total, percentage, grade, submitted_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (chosen_name(name), group.strip(), key, choice_score, match_score,
                              short_score, score, TOTAL, pct, grade,
                              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                except sqlite3.IntegrityError:
                    st.error("Бұл аты-жөн мен топ бойынша жауап бұрын тапсырылған. Мұғалімге хабарласыңыз.")
                else:
                    st.session_state["result"] = {"name": name.strip(), "score": score, "pct": pct, "grade": grade}
                    st.session_state["completed"] = True
                    st.rerun()


with oil_tab:
    st.subheader("🛢️ Мұнайды қалай табамыз? — 20 балл")
    st.write("Мұнайды іздеудің 5 кезеңін дұрыс ретімен таңдаңыз. Әр дұрыс орын — 4 балл.")
    with st.form("oil_steps_form"):
        oil_name = roster_select("Аты-жөніңіз", "oil_student_name")
        oil_class = class_name()
        st.caption(f"Оқу тобы: {oil_class}")
        oil_team = st.selectbox("Командаңыз", TEAM_NAMES, key="oil_team")
        oil_picks = []
        for i in range(5):
            oil_picks.append(st.selectbox(f"{i+1}-кезең", OIL_STEPS, index=None, placeholder="Кезеңді таңдаңыз", key=f"oil_step_{i}"))
        oil_submit = st.form_submit_button("Жауапты жіберу", type="primary")
    if oil_submit:
        if not chosen_name(oil_name) or not oil_class.strip() or any(v is None for v in oil_picks):
            st.error("Аты-жөніңізді, сынып/топты және барлық кезеңді толтырыңыз.")
        elif len(set(oil_picks)) != 5:
            st.error("Бір кезеңді екі рет таңдауға болмайды.")
        else:
            oil_points = sum(4 for actual, expected in zip(oil_picks, OIL_STEPS) if actual == expected)
            key = student_key_for(oil_name, oil_class)
            with get_conn() as conn:
                existing = conn.execute("SELECT 1 FROM oil_discovery WHERE student_key=?", (key,)).fetchone()
                if existing:
                    st.error("Бұл студент осы тапсырманы бұрын тапсырған. Мұғалімге хабарласыңыз.")
                else:
                    conn.execute("INSERT INTO oil_discovery VALUES (?, ?, ?, ?, ?, ?)",
                                 (key, chosen_name(oil_name), oil_class.strip(), oil_team, oil_points, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Нәтиже сақталды: {oil_points}/20 балл")

with upload_tab:
    st.subheader("📤 Топтық презентацияны жіберу")
    st.info("QR-код арқылы осы қолданбаға кіріп, топ атауын енгізіңіз және PDF немесе PowerPoint (.pptx) файлын жүктеңіз. Мұғалім оны өз экранынан ашады.")
    with st.form("presentation_upload", clear_on_submit=True):
        upload_group = st.selectbox("Топ", ["1-топ — Геофизиктер", "2-топ — Петрофизиктер", "3-топ — Зерттеушілер"])
        upload_name = st.text_input("Студенттің аты-жөні")
        upload_file = st.file_uploader("Презентацияны таңдаңыз (PDF немесе PPTX, 15 МБ-қа дейін)", type=["pdf", "pptx"])
        upload_submit = st.form_submit_button("📤 Мұғалімге жіберу", type="primary")
    if upload_submit:
        if not upload_name.strip() or upload_file is None:
            st.error("Аты-жөніңізді жазып, файлды таңдаңыз.")
        else:
            payload = upload_file.getvalue()
            if not payload or len(payload) > 15 * 1024 * 1024:
                st.error("Файл бос немесе 15 МБ-тан үлкен.")
            elif not validate_presentation(upload_file.name, payload):
                st.error("Тек дұрыс PDF немесе PPTX файлын жіберіңіз.")
            else:
                with get_conn() as conn:
                    conn.execute("INSERT INTO presentations (group_name, presenter, filename, filetype, content, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
                                 (upload_group, upload_name.strip()[:120], os.path.basename(upload_file.name)[:200],
                                  os.path.splitext(upload_file.name.lower())[1], payload,
                                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                st.success("Презентация мұғалімге жіберілді! Мұғалім панелінен ашуға болады.")

with teacher_tab:
    st.subheader("Мұғалім панелі")
    if not st.session_state.get("teacher_ok"):
        password = st.text_input("Мұғалім құпиясөзі", type="password")
        if st.button("Кіру"):
            if password == TEACHER_PASSWORD:
                st.session_state["teacher_ok"] = True
                st.rerun()
            else:
                st.error("Құпиясөз дұрыс емес.")
    else:
        st.success("Мұғалім режимі")
        st.divider()
        st.subheader("📽️ Студенттердің презентациялары")
        st.caption("Осы тізімді жаңарту үшін «Нәтижелерді жаңарту» батырмасын басыңыз. PDF браузерде көрсетіледі, PPTX файлды ашу үшін жүктеу қажет.")
        presentation_rows = get_presentations()
        if not presentation_rows:
            st.info("Әзірге презентация жіберілген жоқ.")
        else:
            choices = {f"#{r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[5]}": r[0] for r in presentation_rows}
            picked = st.selectbox("Көрсетілетін презентация", list(choices.keys()))
            chosen = get_presentation_file(choices[picked])
            if chosen:
                file_name, file_type, file_data = chosen
                st.download_button("⬇️ Презентацияны ашу / жүктеу", data=file_data, file_name=file_name,
                                   mime="application/pdf" if file_type == '.pdf' else "application/vnd.openxmlformats-officedocument.presentationml.presentation")
                if file_type == '.pdf':
                    pdf64 = base64.b64encode(file_data).decode('ascii')
                    st.components.v1.html(f'<iframe src="data:application/pdf;base64,{pdf64}" width="100%" height="650" style="border:0"></iframe>', height=670)
                else:
                    st.info("PowerPoint (.pptx) файлын браузер ішінде тікелей көрсету қолдау таппауы мүмкін. Тікелей экраннан көрсету үшін студенттерге PDF ретінде жіберуді ұсыныңыз.")

        if st.button("🔄 Нәтижелерді жаңарту"):
            st.rerun()
        assessment_tab, oral_tab = st.tabs(["1️⃣ Интерактивті тапсырмалар", "2️⃣ Ауызша үй тапсырмасын бағалау"])
        with assessment_tab:
            df = get_results()
            if df.empty:
                st.info("Әзірге интерактивті тапсырманы ешкім тапсырған жоқ.")
            else:
                a, b, c = st.columns(3)
                a.metric("Студенттер", len(df))
                b.metric("Орташа баға", f"{df['grade'].mean():.2f}")
                c.metric("Орташа нәтиже", f"{df['percentage'].mean():.1f}%")
                shown = df[["student_name", "group_name", "choice_score", "match_score", "short_score", "score", "total", "percentage", "grade", "submitted_at"]].rename(columns={
                    "student_name": "Аты-жөні", "group_name": "Тобы", "choice_score": "Таңдау", "match_score": "Сәйкестендіру",
                    "short_score": "Қысқа жауап", "score": "Ұпай", "total": "Барлығы", "percentage": "Пайыз",
                    "grade": "Баға", "submitted_at": "Тапсырған уақыты"
                })
                st.markdown(shown.to_html(index=False, escape=True), unsafe_allow_html=True)
                st.download_button("📥 Интерактивті нәтижелерді CSV жүктеу", shown.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="geologia_results.csv", mime="text/csv")

        with oral_tab:
            st.subheader("Үй тапсырмасын ауызша бағалау — 20 балл")
            st.write("Сұрақ: **Мұнай-газ қабаты қалай түзіледі?**")
            st.caption("Әр критерийге 0-ден көрсетілген максималды баллға дейін қойыңыз. Баға автоматты есептеледі.")
            with st.form("oral_assessment_form", clear_on_submit=True):
                oral_name = roster_select("Студенттің аты-жөні", "oral_name")
                oral_group = class_name()
                st.caption(f"Оқу тобы: {oral_group}")
                oral_question = st.text_input("Ауызша сұрақ", value="Мұнай-газ қабаты қалай түзіледі?")
                points = []
                for i, (criterion, maximum) in enumerate(ORAL_CRITERIA):
                    points.append(st.slider(f"{i+1}. {criterion} (0–{maximum} балл)", 0, maximum, 0, key=f"oral_criterion_{i}"))
                st.write(f"**Жалпы балл: {sum(points)} / 20**")
                oral_submitted = st.form_submit_button("💾 Бағаны сақтау", type="primary")
            if oral_submitted:
                if not chosen_name(oral_name) or not oral_group.strip() or not oral_question.strip():
                    st.error("Студенттің аты-жөнін, тобын және сұрақты толтырыңыз.")
                else:
                    oral_total = sum(points)
                    oral_pct = round(oral_total * 100 / 20, 1)
                    oral_letter = letter_for(oral_pct)
                    oral_grade = oral_grade_for(oral_pct)
                    with get_conn() as conn:
                        conn.execute("""
                            INSERT INTO oral_results
                            (student_name, group_name, question, criterion_1, criterion_2, criterion_3, criterion_4, criterion_5,
                             score, percentage, letter_grade, grade, submitted_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (chosen_name(oral_name), oral_group.strip(), oral_question.strip(), *points,
                              oral_total, oral_pct, oral_letter, oral_grade, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Сақталды: {oral_name} — {oral_total}/20 балл, {oral_pct:.0f}%, {oral_letter}, баға {oral_grade}.")
            oral_df = get_oral_results()
            st.subheader("Ауызша бағалау нәтижелері")
            if oral_df.empty:
                st.info("Әзірге ауызша бағалау нәтижелері жоқ.")
            else:
                oral_shown = oral_df[["student_name", "group_name", "question", "criterion_1", "criterion_2", "criterion_3", "criterion_4", "criterion_5", "score", "percentage", "letter_grade", "grade", "submitted_at"]].rename(columns={
                    "student_name": "Аты-жөні", "group_name": "Тобы", "question": "Сұрақ",
                    "criterion_1": "К1 /4", "criterion_2": "К2 /5", "criterion_3": "К3 /5", "criterion_4": "К4 /3", "criterion_5": "К5 /3",
                    "score": "Балл /20", "percentage": "Пайыз", "letter_grade": "Әріптік баға", "grade": "Баға (2–5)", "submitted_at": "Уақыты"
                })
                st.markdown(oral_shown.to_html(index=False, escape=True), unsafe_allow_html=True)
                st.download_button("📥 Ауызша бағалауды CSV жүктеу", oral_shown.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="geologia_oral_results.csv", mime="text/csv")
        st.divider()
        st.subheader("👥 Студенттер тізімі және командалар")
        names = roster_names()
        st.caption(f"Тізімде {len(names)} студент бар. Аты-жөндер тек мұғалімнің құпия баптауларынан оқылады.")
        if names:
            with st.form("assign_student_team"):
                assigned_student = st.selectbox("Студент", names)
                assigned_team = st.selectbox("Команда", TEAM_NAMES)
                save_assignment = st.form_submit_button("Командаға тіркеу")
            if save_assignment:
                with get_conn() as conn:
                    conn.execute("INSERT INTO student_teams(student_key, team_name) VALUES (?, ?) ON CONFLICT(student_key) DO UPDATE SET team_name=excluded.team_name", (student_key_for(assigned_student, class_name()), assigned_team))
                st.success("Студент командаға тіркелді")
            with get_conn() as conn:
                team_rows = conn.execute("SELECT student_key, team_name FROM student_teams").fetchall()
            teams_by_student = dict(team_rows)
            roster_df = pd.DataFrame([{"Аты-жөні": n, "Команда": teams_by_student.get(student_key_for(n, class_name()), "Тағайындалмаған")} for n in names])
            st.markdown(roster_df.to_html(index=False, escape=True), unsafe_allow_html=True)
        st.divider()
        st.header("📊 Сабақтың төрт кезеңін бағалау")
        six_tab, team_tab, summary_tab = st.tabs(["🗣️ 6 сұрақ /30", "👥 Топтық жұмыс /30", "🏆 Қорытынды /100"])
        with six_tab:
            st.caption("Әр сұрақ: 0 — қате/жауап жоқ, 1 — жартылай, 3 — негізінен дұрыс, 5 — толық жауап. Ұпай автоматты қосылады.")
            with st.form("six_questions_form", clear_on_submit=True):
                six_name = roster_select("Студенттің аты-жөні", "six_name")
                six_class = class_name()
                st.caption(f"Оқу тобы: {six_class}")
                six_team = st.selectbox("Студенттің командасы", TEAM_NAMES, key="six_team")
                six_points = []
                for i, question in enumerate(ORAL_SIX):
                    six_points.append(st.select_slider(f"{i+1}. {question}", options=[0, 1, 3, 5], value=0, key=f"six_q_{i}"))
                st.write(f"**Ұпай: {sum(six_points)} / 30**")
                six_submit = st.form_submit_button("💾 6 сұрақ бағасын сақтау", type="primary")
            if six_submit:
                if not chosen_name(six_name) or not six_class.strip():
                    st.error("Аты-жөні мен сынып/топты енгізіңіз.")
                else:
                    key = student_key_for(six_name, six_class)
                    with get_conn() as conn:
                        conn.execute("""INSERT INTO six_answers
                            (student_key, student_name, group_name, team_name, q1, q2, q3, q4, q5, q6, score, submitted_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(student_key) DO UPDATE SET
                            team_name=excluded.team_name, q1=excluded.q1, q2=excluded.q2,
                            q3=excluded.q3, q4=excluded.q4, q5=excluded.q5, q6=excluded.q6,
                            score=excluded.score, submitted_at=excluded.submitted_at""",
                            (key, chosen_name(six_name), six_class.strip(), six_team, *six_points, sum(six_points), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Сақталды: {six_name} — {sum(six_points)}/30")
            six_df = read_lesson_table("six_answers")
            if not six_df.empty:
                st.dataframe(six_df[["student_name", "group_name", "team_name", "score"]].rename(columns={"student_name":"Аты-жөні", "group_name":"Сынып/топ", "team_name":"Команда", "score":"Ұпай /30"}), hide_index=True)
        with team_tab:
            st.caption("Команданың ұпайы осы командаға тіркелген барлық студентке беріледі. Сынып/топ атауын студенттер енгізгендей жазыңыз.")
            with st.form("team_form"):
                team_class = class_name()
                st.caption(f"Оқу тобы: {team_class}")
                team_name = st.selectbox("Бағаланатын команда", TEAM_NAMES, key="team_name")
                team_points = [st.slider(f"{label} /{maximum}", 0, maximum, 0, key=f"team_criterion_{i}") for i, (label, maximum) in enumerate(GROUP_CRITERIA)]
                st.write(f"**Ұпай: {sum(team_points)} / 30**")
                team_submit = st.form_submit_button("💾 Топ бағасын сақтау", type="primary")
            if team_submit:
                if not team_class.strip():
                    st.error("Сынып/топ атауын енгізіңіз.")
                else:
                    with get_conn() as conn:
                        conn.execute("""INSERT INTO group_assessment VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(group_name, team_name) DO UPDATE SET
                            k1=excluded.k1, k2=excluded.k2, k3=excluded.k3, k4=excluded.k4,
                            score=excluded.score, submitted_at=excluded.submitted_at""",
                            (team_class.strip(), team_name, *team_points, sum(team_points), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Сақталды: {team_name} — {sum(team_points)}/30")
            team_df = read_lesson_table("group_assessment")
            if not team_df.empty:
                st.dataframe(team_df[["group_name", "team_name", "score"]], hide_index=True)
        with summary_tab:
            st.caption("Бекіту тестінің бастапқы 9 ұпайы 20 балға пропорционал есептеледі. Аяқталмаған кезеңдер бос көрсетіледі, қорытынды баға қойылмайды.")
            summary = final_scores()
            if summary.empty:
                st.info("Әзірге студенттердің нәтижелері жоқ.")
            else:
                st.dataframe(summary, hide_index=True, use_container_width=True)
                st.download_button("📥 Қорытынды бағаларды CSV жүктеу", summary.to_csv(index=False).encode("utf-8-sig"), file_name="geologia_final_100.csv", mime="text/csv")
                st.metric("Барлық 4 кезеңді аяқтағандар", int((summary["Кезең"] == "4/4").sum()))
        st.divider()
        st.subheader("📱 Студенттерге арналған QR-код")
        lan = guess_lan_ip()
        current_port = st.context.url.split(":")[-1].split("/")[0] if ":" in st.context.url.split("//")[-1] else "8501"
        suggested = st.context.url if ".streamlit.app" in st.context.url else (f"http://{lan}:{current_port}" if lan else "")
        url = st.text_input("Студенттер кіретін сілтеме", value=suggested,
                            help="Бір Wi-Fi желісінде: http://КОМПЬЮТЕР_IP:8501. Интернетке жарияласаңыз, https://... сілтемесін енгізіңіз.")
        if url.startswith(("http://", "https://")):
            st.image(qr_png(url), width=230, caption="Телефон камерасымен сканерлеңіз")
            st.code(url)
            st.download_button("QR-кодты PNG жүктеу", qr_png(url), file_name="geologia_qr.png", mime="image/png")
        st.warning("Компьютер мен телефон бір Wi-Fi желісінде болуы керек. Windows Firewall сұраса, тек сенімді Private network үшін рұқсат беріңіз. QR-код желіні өздігінен қоспайды.")
        st.caption("Жергілікті results.db файлы осы компьютерде сақталады. Қолданбаны тегін бұлттық хостингке өзгеріссіз орналастырсаңыз, файл тұрақты сақталмауы мүмкін.")
        if st.button("Мұғалім режимінен шығу"):
            st.session_state["teacher_ok"] = False
            st.rerun()
import io
import base64
import zipfile
import html
import os
import re
import socket
import sqlite3
import unicodedata
from datetime import datetime

import pandas as pd
import qrcode
import streamlit as st

st.set_page_config(page_title="Геологиялық экспедиция", page_icon="🛢️", layout="wide")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.db")
TEACHER_PASSWORD = os.environ.get("TEACHER_PASSWORD", "12345")
ORAL_CRITERIA = [("Сұраққа нақты жауап беру", 4), ("Мұнай мен газдың түзілуін ғылыми түсіндіру", 5), ("Қалыптасу кезеңдерін ретімен айту", 5), ("Геологиялық терминдерді қолдану", 3), ("Жауапты жүйелі жеткізу", 3)]

# Change these tasks to suit the homework topic.
CHOICE = [
    {"q": "Петрофизика нені зерттейді?", "options": ["Тау жыныстарының физикалық қасиеттерін", "Ауа райын", "Өсімдіктердің өсуін", "Аспан денелерін"], "correct": 0},
    {"q": "Кеуектілік дегеніміз не?", "options": ["Жыныстың түсі", "Жыныстағы бос кеңістіктің үлесі", "Жыныстың жасы", "Жыныстың қаттылығы"], "correct": 1},
    {"q": "Өткізгіштік нені сипаттайды?", "options": ["Жыныстың жылу сыйымдылығын", "Жыныстың магниттілігін", "Флюидтердің жыныс арқылы өту қабілетін", "Жыныстың түсін"], "correct": 2},
]

# Each row is a matching pair. Every correct pair earns one point.
MATCH = [
    ("Кеуектілік", "Бос кеңістіктің көлемдік үлесі"),
    ("Өткізгіштік", "Флюидті өткізу қабілеті"),
    ("Коллектор", "Флюидтерді жинап, өткізе алатын жыныс"),
    ("Жапқыш жыныс", "Көмірсутектердің қозғалуын тежейтін қабат"),
]

# Short answers are checked by exact normalized keywords/phrases.
# These are NOT AI semantic checks: synonyms must be added explicitly.
SHORT = [
    {"q": "Жыныс кеуектерін толтыратын мұнай, газ және су жалпы қалай аталады?", "accepted": ["флюид", "флюидтер", "қабат флюидтері", "қабаттық флюидтер"]},
    {"q": "Мұнай мен газ жиналатын кеуекті әрі өткізгіш жыныс қалай аталады?", "accepted": ["коллектор", "коллектор жыныс", "коллектор жынысы", "коллекторлық жыныс"]},
]

TOTAL = len(CHOICE) + len(MATCH) + len(SHORT)


def normalize(value):
    value = unicodedata.normalize("NFKC", value).lower().strip()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def initialize():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                student_key TEXT NOT NULL UNIQUE,
                choice_score INTEGER NOT NULL,
                match_score INTEGER NOT NULL,
                short_score INTEGER NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                percentage REAL NOT NULL,
                grade INTEGER NOT NULL,
                submitted_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oral_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                question TEXT NOT NULL,
                criterion_1 INTEGER NOT NULL,
                criterion_2 INTEGER NOT NULL,
                criterion_3 INTEGER NOT NULL,
                criterion_4 INTEGER NOT NULL,
                criterion_5 INTEGER NOT NULL,
                score INTEGER NOT NULL,
                percentage REAL NOT NULL,
                letter_grade TEXT NOT NULL,
                grade INTEGER NOT NULL,
                submitted_at TEXT NOT NULL
            )
        """)


def letter_for(pct):
    for minimum, letter in [(95, "A"), (90, "A−"), (85, "B+"), (80, "B"), (75, "B−"), (70, "C+"), (65, "C"), (60, "C−"), (55, "D+"), (50, "D")]:
        if pct >= minimum:
            return letter
    return "F"


def oral_grade_for(pct):
    if pct >= 90:
        return 5
    if pct >= 70:
        return 4
    if pct >= 50:
        return 3
    return 2


def get_oral_results():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM oral_results ORDER BY submitted_at DESC, id DESC", conn)


def init_presentations():
    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS presentations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            presenter TEXT NOT NULL,
            filename TEXT NOT NULL,
            filetype TEXT NOT NULL,
            content BLOB NOT NULL,
            uploaded_at TEXT NOT NULL
        )""")


def get_presentations():
    with get_conn() as conn:
        return conn.execute("SELECT id, group_name, presenter, filename, filetype, uploaded_at FROM presentations ORDER BY id DESC").fetchall()


def get_presentation_file(presentation_id):
    with get_conn() as conn:
        return conn.execute("SELECT filename, filetype, content FROM presentations WHERE id=?", (presentation_id,)).fetchone()


def validate_presentation(filename, content):
    suffix = os.path.splitext(filename.lower())[1]
    if suffix == '.pdf':
        return content.startswith(b'%PDF-')
    if suffix == '.pptx':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                return '[Content_Types].xml' in z.namelist() and 'ppt/presentation.xml' in z.namelist()
        except (zipfile.BadZipFile, OSError):
            return False
    return False


def grade_for(pct):
    if pct >= 90:
        return 5
    if pct >= 70:
        return 4
    if pct >= 50:
        return 3
    return 2


def get_results():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM results ORDER BY submitted_at DESC, id DESC", conn)


def qr_png(url):
    image = qrcode.make(url)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def guess_lan_ip():
    # No packets are sent: UDP connect only selects a local interface.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


initialize()
init_presentations()

# Сабақтың төрт кезеңі: 20 + 30 + 30 + 20 = 100.
OIL_STEPS = [
    "Геологиялық деректерді жинау және талдау",
    "Геофизикалық (сейсмикалық) барлау жүргізу",
    "Перспективалы тұтқыш құрылымды анықтау",
    "Барлау ұңғымасын бұрғылау",
    "Ұңғыма нәтижелерін зерттеп, қорды бағалау",
]
ORAL_SIX = [
    "Нейтрондардың тау жыныстарымен әрекеттесуін түсіндіріңіз.",
    "Гамма-кванттардың тау жыныстарымен әрекеттесуін түсіндіріңіз.",
    "Ядролық магниттік прецессия дегеніміз не?",
    "Кеуектілік дегеніміз не және оның түрлері қандай?",
    "Өткізгіштік дегеніміз не және оның түрлері қандай?",
    "Поляризация құбылысы дегеніміз не?",
]
TEAM_NAMES = ["1-топ — Геофизиктер", "2-топ — Петрофизиктер", "3-топ — Зерттеушілер"]
GROUP_CRITERIA = [("Ғылыми дұрыстық", 10), ("Презентация мазмұны мен көрнекілігі", 8), ("Топ мүшелерінің қатысуы", 6), ("Қорғау сапасы", 6)]

def student_key_for(name, group):
    return normalize(group) + "|" + normalize(name)

def init_lesson():
    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS oil_discovery (
            student_key TEXT PRIMARY KEY, student_name TEXT NOT NULL, group_name TEXT NOT NULL,
            team_name TEXT NOT NULL, score INTEGER NOT NULL, submitted_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS six_answers (
            student_key TEXT PRIMARY KEY, student_name TEXT NOT NULL, group_name TEXT NOT NULL,
            team_name TEXT NOT NULL, q1 INTEGER NOT NULL, q2 INTEGER NOT NULL,
            q3 INTEGER NOT NULL, q4 INTEGER NOT NULL, q5 INTEGER NOT NULL,
            q6 INTEGER NOT NULL, score INTEGER NOT NULL, submitted_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS group_assessment (
            group_name TEXT NOT NULL, team_name TEXT NOT NULL,
            k1 INTEGER NOT NULL, k2 INTEGER NOT NULL, k3 INTEGER NOT NULL,
            k4 INTEGER NOT NULL, score INTEGER NOT NULL, submitted_at TEXT NOT NULL,
            PRIMARY KEY (group_name, team_name))""")

def read_lesson_table(table):
    allowed = {"oil_discovery", "six_answers", "group_assessment"}
    if table not in allowed:
        raise ValueError("Unknown table")
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM " + table, conn)

def final_scores():
    oil = read_lesson_table("oil_discovery")
    oral = read_lesson_table("six_answers")
    quiz = get_results()
    teams = read_lesson_table("group_assessment")
    students = {}
    for df in (oil, oral, quiz):
        for _, r in df.iterrows():
            key = r["student_key"]
            students.setdefault(key, {"Аты-жөні": r["student_name"], "Сынып/топ": r["group_name"], "Команда": ""})
            if "team_name" in r and pd.notna(r["team_name"]):
                students[key]["Команда"] = r["team_name"]
    rows = []
    oil_map = {r["student_key"]: int(r["score"]) for _, r in oil.iterrows()}
    oral_map = {r["student_key"]: int(r["score"]) for _, r in oral.iterrows()}
    quiz_map = {r["student_key"]: round(float(r["score"]) / float(r["total"]) * 20, 1) for _, r in quiz.iterrows() if r["total"]}
    team_map = {(normalize(r["group_name"]), r["team_name"]): int(r["score"]) for _, r in teams.iterrows()}
    for key, info in students.items():
        team = info["Команда"]
        team_key = (normalize(info["Сынып/топ"]), team)
        group_points = team_map.get(team_key) if team else None
        parts = [oil_map.get(key), oral_map.get(key), group_points, quiz_map.get(key)]
        total = round(sum(x for x in parts if x is not None), 1)
        completed = sum(x is not None for x in parts)
        rows.append({**info, "Мұнай іздеу /20": parts[0], "6 сұрақ /30": parts[1],
                     "Топтық /30": parts[2], "Бекіту /20": parts[3],
                     "Жиыны /100": total, "Кезең": f"{completed}/4",
                     "Әріптік баға": letter_for(total) if completed == 4 else "Аяқталмаған",
                     "Баға": grade_for(total) if completed == 4 else "—"})
    return pd.DataFrame(rows)

init_lesson()

st.markdown("""
<style>
.block-container {max-width: 1050px; padding-top: 2rem;}
[data-testid="stMetric"] {background: #edf4f8; border-radius: 12px; padding: 14px;}
</style>
""", unsafe_allow_html=True)
st.title("🛢️ Геологиялық экспедиция")
st.caption("4 кезең: мұнайды іздеу /20 • 6 сұрақ /30 • топтық жұмыс /30 • бекіту /20")

student_tab, oil_tab, upload_tab, teacher_tab = st.tabs(["📱 Бекіту тесті", "🛢️ Мұнайды қалай табамыз?", "📤 Презентация жіберу", "👩‍🏫 Мұғалім"])

with student_tab:
    if st.session_state.get("completed"):
        result = st.session_state["result"]
        st.success("Жауабыңыз қабылданды!")
        st.subheader("1-бөлім. Дұрыс жауапты таңдаңыз")
            selected = []
            for i, item in enumerate(CHOICE):
                selected.append(
                    st.radio(
                        f"{i+1}. {item['q']}",
                        range(len(item["options"])),
                        format_func=lambda j, opts=item["options"]: opts[j],
                        index=None,
                        key=f"choice_{i}",
            st.subheader("2-бөлім. Сәйкестендіріңіз")
            st.caption("Әр терминге сәйкес анықтаманы таңдаңыз. Әр дұрыс жұп — 1 ұпай.")
            definitions = [definition for _, definition in MATCH]
            matched = []
            for i, (term, _) in enumerate(MATCH):
                matched.append(st.selectbox(
                    f"{i+1}. {term}",
                    options=definitions,
                    index=None,
                    placeholder="Анықтаманы таңдаңыз",
                    key=f"match_{i}",
                ))
            st.divider()
            st.subheader("3-бөлім. Қысқа жауап жазыңыз")
            st.caption("Жауапты бір сөзбен немесе қысқа сөз тіркесімен жазыңыз.")
            short_answers = []
            for i, item in enumerate(SHORT):
                short_answers.append(st.text_input(f"{i+1}. {item['q']}", key=f"short_{i}"))
            submitted = st.form_submit_button("Жауаптарды жіберу", type="primary", use_container_width=True)

        if submitted:
            if not name.strip() or not group.strip():
                st.error("Аты-жөніңізді және тобыңызды толтырыңыз.")
            elif any(x is None for x in selected) or any(x is None for x in matched) or any(not x.strip() for x in short_answers):
                st.error("Барлық сұраққа жауап беріңіз.")
            else:
                choice_score = sum(a == item["correct"] for a, item in zip(selected, CHOICE))
                match_score = sum(a == definition for a, (_, definition) in zip(matched, MATCH))
                short_score = sum(normalize(a) in {normalize(v) for v in item["accepted"]} for a, item in zip(short_answers, SHORT))
                score = choice_score + match_score + short_score
                pct = round(100 * score / TOTAL, 1)
                grade = grade_for(pct)
                key = normalize(group) + "|" + normalize(name)
                try:
                    with get_conn() as conn:
                        conn.execute("""
                            INSERT INTO results
                            (student_name, group_name, student_key, choice_score, match_score, short_score,
                             score, total, percentage, grade, submitted_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (name.strip(), group.strip(), key, choice_score, match_score,
                              short_score, score, TOTAL, pct, grade,
                              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                except sqlite3.IntegrityError:
                    st.error("Бұл аты-жөн мен топ бойынша жауап бұрын тапсырылған. Мұғалімге хабарласыңыз.")
                else:
                    st.session_state["result"] = {"name": name.strip(), "score": score, "pct": pct, "grade": grade}
                    st.session_state["completed"] = True
                    st.rerun()


with oil_tab:
    st.subheader("🛢️ Мұнайды қалай табамыз? — 20 балл")
    st.write("Мұнайды іздеудің 5 кезеңін дұрыс ретімен таңдаңыз. Әр дұрыс орын — 4 балл.")
    with st.form("oil_steps_form"):
        oil_name = st.text_input("Аты-жөніңіз", key="oil_student_name")
        oil_class = st.text_input("Сынып/топ атауы (мысалы, ГР-31)", key="oil_class")
        oil_team = st.selectbox("Командаңыз", TEAM_NAMES, key="oil_team")
        oil_picks = []
        for i in range(5):
            oil_picks.append(st.selectbox(f"{i+1}-кезең", OIL_STEPS, index=None, placeholder="Кезеңді таңдаңыз", key=f"oil_step_{i}"))
        oil_submit = st.form_submit_button("Жауапты жіберу", type="primary")
    if oil_submit:
        if not oil_name.strip() or not oil_class.strip() or any(v is None for v in oil_picks):
            st.error("Аты-жөніңізді, сынып/топты және барлық кезеңді толтырыңыз.")
        elif len(set(oil_picks)) != 5:
            st.error("Бір кезеңді екі рет таңдауға болмайды.")
        else:
            oil_points = sum(4 for actual, expected in zip(oil_picks, OIL_STEPS) if actual == expected)
            key = student_key_for(oil_name, oil_class)
            with get_conn() as conn:
                existing = conn.execute("SELECT 1 FROM oil_discovery WHERE student_key=?", (key,)).fetchone()
                if existing:
                    st.error("Бұл студент осы тапсырманы бұрын тапсырған. Мұғалімге хабарласыңыз.")
                else:
                    conn.execute("INSERT INTO oil_discovery VALUES (?, ?, ?, ?, ?, ?)",
                                 (key, oil_name.strip(), oil_class.strip(), oil_team, oil_points, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Нәтиже сақталды: {oil_points}/20 балл")

with upload_tab:
    st.subheader("📤 Топтық презентацияны жіберу")
    st.info("QR-код арқылы осы қолданбаға кіріп, топ атауын енгізіңіз және PDF немесе PowerPoint (.pptx) файлын жүктеңіз. Мұғалім оны өз экранынан ашады.")
    with st.form("presentation_upload", clear_on_submit=True):
        upload_group = st.selectbox("Топ", ["1-топ — Геофизиктер", "2-топ — Петрофизиктер", "3-топ — Зерттеушілер"])
        upload_name = st.text_input("Студенттің аты-жөні")
        upload_file = st.file_uploader("Презентацияны таңдаңыз (PDF немесе PPTX, 15 МБ-қа дейін)", type=["pdf", "pptx"])
        upload_submit = st.form_submit_button("📤 Мұғалімге жіберу", type="primary")
    if upload_submit:
        if not upload_name.strip() or upload_file is None:
            st.error("Аты-жөніңізді жазып, файлды таңдаңыз.")
        else:
            payload = upload_file.getvalue()
            if not payload or len(payload) > 15 * 1024 * 1024:
                st.error("Файл бос немесе 15 МБ-тан үлкен.")
            elif not validate_presentation(upload_file.name, payload):
                st.error("Тек дұрыс PDF немесе PPTX файлын жіберіңіз.")
            else:
                with get_conn() as conn:
                    conn.execute("INSERT INTO presentations (group_name, presenter, filename, filetype, content, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
                                 (upload_group, upload_name.strip()[:120], os.path.basename(upload_file.name)[:200],
                                  os.path.splitext(upload_file.name.lower())[1], payload,
                                  datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                st.success("Презентация мұғалімге жіберілді! Мұғалім панелінен ашуға болады.")

with teacher_tab:
    st.subheader("Мұғалім панелі")
    if not st.session_state.get("teacher_ok"):
        password = st.text_input("Мұғалім құпиясөзі", type="password")
        if st.button("Кіру"):
            if password == TEACHER_PASSWORD:
                st.session_state["teacher_ok"] = True
                st.rerun()
            else:
                st.error("Құпиясөз дұрыс емес.")
    else:
        st.success("Мұғалім режимі")
        st.divider()
        st.subheader("📽️ Студенттердің презентациялары")
        st.caption("Осы тізімді жаңарту үшін «Нәтижелерді жаңарту» батырмасын басыңыз. PDF браузерде көрсетіледі, PPTX файлды ашу үшін жүктеу қажет.")
        presentation_rows = get_presentations()
        if not presentation_rows:
            st.info("Әзірге презентация жіберілген жоқ.")
        else:
            choices = {f"#{r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[5]}": r[0] for r in presentation_rows}
            picked = st.selectbox("Көрсетілетін презентация", list(choices.keys()))
            chosen = get_presentation_file(choices[picked])
            if chosen:
                file_name, file_type, file_data = chosen
                st.download_button("⬇️ Презентацияны ашу / жүктеу", data=file_data, file_name=file_name,
                                   mime="application/pdf" if file_type == '.pdf' else "application/vnd.openxmlformats-officedocument.presentationml.presentation")
                if file_type == '.pdf':
                    pdf64 = base64.b64encode(file_data).decode('ascii')
                    st.components.v1.html(f'<iframe src="data:application/pdf;base64,{pdf64}" width="100%" height="650" style="border:0"></iframe>', height=670)
                else:
                    st.info("PowerPoint (.pptx) файлын браузер ішінде тікелей көрсету қолдау таппауы мүмкін. Тікелей экраннан көрсету үшін студенттерге PDF ретінде жіберуді ұсыныңыз.")

        if st.button("🔄 Нәтижелерді жаңарту"):
            st.rerun()
        assessment_tab, oral_tab = st.tabs(["1️⃣ Интерактивті тапсырмалар", "2️⃣ Ауызша үй тапсырмасын бағалау"])
        with assessment_tab:
            df = get_results()
            if df.empty:
                st.info("Әзірге интерактивті тапсырманы ешкім тапсырған жоқ.")
            else:
                a, b, c = st.columns(3)
                a.metric("Студенттер", len(df))
                b.metric("Орташа баға", f"{df['grade'].mean():.2f}")
                c.metric("Орташа нәтиже", f"{df['percentage'].mean():.1f}%")
                shown = df[["student_name", "group_name", "choice_score", "match_score", "short_score", "score", "total", "percentage", "grade", "submitted_at"]].rename(columns={
                    "student_name": "Аты-жөні", "group_name": "Тобы", "choice_score": "Таңдау", "match_score": "Сәйкестендіру",
                    "short_score": "Қысқа жауап", "score": "Ұпай", "total": "Барлығы", "percentage": "Пайыз",
                    "grade": "Баға", "submitted_at": "Тапсырған уақыты"
                })
                st.markdown(shown.to_html(index=False, escape=True), unsafe_allow_html=True)
                st.download_button("📥 Интерактивті нәтижелерді CSV жүктеу", shown.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="geologia_results.csv", mime="text/csv")

        with oral_tab:
            st.subheader("Үй тапсырмасын ауызша бағалау — 20 балл")
            st.write("Сұрақ: **Мұнай-газ қабаты қалай түзіледі?**")
            st.caption("Әр критерийге 0-ден көрсетілген максималды баллға дейін қойыңыз. Баға автоматты есептеледі.")
            with st.form("oral_assessment_form", clear_on_submit=True):
                oral_name = st.text_input("Студенттің аты-жөні", key="oral_name")
                oral_group = st.text_input("Тобы", key="oral_group")
                oral_question = st.text_input("Ауызша сұрақ", value="Мұнай-газ қабаты қалай түзіледі?")
                points = []
                for i, (criterion, maximum) in enumerate(ORAL_CRITERIA):
                    points.append(st.slider(f"{i+1}. {criterion} (0–{maximum} балл)", 0, maximum, 0, key=f"oral_criterion_{i}"))
                st.write(f"**Жалпы балл: {sum(points)} / 20**")
                oral_submitted = st.form_submit_button("💾 Бағаны сақтау", type="primary")
            if oral_submitted:
                if not oral_name.strip() or not oral_group.strip() or not oral_question.strip():
                    st.error("Студенттің аты-жөнін, тобын және сұрақты толтырыңыз.")
                else:
                    oral_total = sum(points)
                    oral_pct = round(oral_total * 100 / 20, 1)
                    oral_letter = letter_for(oral_pct)
                    oral_grade = oral_grade_for(oral_pct)
                    with get_conn() as conn:
                        conn.execute("""
                            INSERT INTO oral_results
                            (student_name, group_name, question, criterion_1, criterion_2, criterion_3, criterion_4, criterion_5,
                             score, percentage, letter_grade, grade, submitted_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (oral_name.strip(), oral_group.strip(), oral_question.strip(), *points,
                              oral_total, oral_pct, oral_letter, oral_grade, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Сақталды: {oral_name} — {oral_total}/20 балл, {oral_pct:.0f}%, {oral_letter}, баға {oral_grade}.")
            oral_df = get_oral_results()
            st.subheader("Ауызша бағалау нәтижелері")
            if oral_df.empty:
                st.info("Әзірге ауызша бағалау нәтижелері жоқ.")
            else:
                oral_shown = oral_df[["student_name", "group_name", "question", "criterion_1", "criterion_2", "criterion_3", "criterion_4", "criterion_5", "score", "percentage", "letter_grade", "grade", "submitted_at"]].rename(columns={
                    "student_name": "Аты-жөні", "group_name": "Тобы", "question": "Сұрақ",
                    "criterion_1": "К1 /4", "criterion_2": "К2 /5", "criterion_3": "К3 /5", "criterion_4": "К4 /3", "criterion_5": "К5 /3",
                    "score": "Балл /20", "percentage": "Пайыз", "letter_grade": "Әріптік баға", "grade": "Баға (2–5)", "submitted_at": "Уақыты"
                })
                st.markdown(oral_shown.to_html(index=False, escape=True), unsafe_allow_html=True)
                st.download_button("📥 Ауызша бағалауды CSV жүктеу", oral_shown.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="geologia_oral_results.csv", mime="text/csv")
        st.divider()
        st.header("📊 Сабақтың төрт кезеңін бағалау")
        six_tab, team_tab, summary_tab = st.tabs(["🗣️ 6 сұрақ /30", "👥 Топтық жұмыс /30", "🏆 Қорытынды /100"])
        with six_tab:
            st.caption("Әр сұрақ: 0 — қате/жауап жоқ, 1 — жартылай, 3 — негізінен дұрыс, 5 — толық жауап. Ұпай автоматты қосылады.")
            with st.form("six_questions_form", clear_on_submit=True):
                six_name = st.text_input("Студенттің аты-жөні", key="six_name")
                six_class = st.text_input("Сынып/топ атауы (мысалы, ГР-31)", key="six_class")
                six_team = st.selectbox("Студенттің командасы", TEAM_NAMES, key="six_team")
                six_points = []
                for i, question in enumerate(ORAL_SIX):
                    six_points.append(st.select_slider(f"{i+1}. {question}", options=[0, 1, 3, 5], value=0, key=f"six_q_{i}"))
                st.write(f"**Ұпай: {sum(six_points)} / 30**")
                six_submit = st.form_submit_button("💾 6 сұрақ бағасын сақтау", type="primary")
            if six_submit:
                if not six_name.strip() or not six_class.strip():
                    st.error("Аты-жөні мен сынып/топты енгізіңіз.")
                else:
                    key = student_key_for(six_name, six_class)
                    with get_conn() as conn:
                        conn.execute("""INSERT INTO six_answers
                            (student_key, student_name, group_name, team_name, q1, q2, q3, q4, q5, q6, score, submitted_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(student_key) DO UPDATE SET
                            team_name=excluded.team_name, q1=excluded.q1, q2=excluded.q2,
                            q3=excluded.q3, q4=excluded.q4, q5=excluded.q5, q6=excluded.q6,
                            score=excluded.score, submitted_at=excluded.submitted_at""",
                            (key, six_name.strip(), six_class.strip(), six_team, *six_points, sum(six_points), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Сақталды: {six_name} — {sum(six_points)}/30")
            six_df = read_lesson_table("six_answers")
            if not six_df.empty:
                st.dataframe(six_df[["student_name", "group_name", "team_name", "score"]].rename(columns={"student_name":"Аты-жөні", "group_name":"Сынып/топ", "team_name":"Команда", "score":"Ұпай /30"}), hide_index=True)
        with team_tab:
            st.caption("Команданың ұпайы осы командаға тіркелген барлық студентке беріледі. Сынып/топ атауын студенттер енгізгендей жазыңыз.")
            with st.form("team_form"):
                team_class = st.text_input("Сынып/топ атауы (мысалы, ГР-31)", key="team_class")
                team_name = st.selectbox("Бағаланатын команда", TEAM_NAMES, key="team_name")
                team_points = [st.slider(f"{label} /{maximum}", 0, maximum, 0, key=f"team_criterion_{i}") for i, (label, maximum) in enumerate(GROUP_CRITERIA)]
                st.write(f"**Ұпай: {sum(team_points)} / 30**")
                team_submit = st.form_submit_button("💾 Топ бағасын сақтау", type="primary")
            if team_submit:
                if not team_class.strip():
                    st.error("Сынып/топ атауын енгізіңіз.")
                else:
                    with get_conn() as conn:
                        conn.execute("""INSERT INTO group_assessment VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(group_name, team_name) DO UPDATE SET
                            k1=excluded.k1, k2=excluded.k2, k3=excluded.k3, k4=excluded.k4,
                            score=excluded.score, submitted_at=excluded.submitted_at""",
                            (team_class.strip(), team_name, *team_points, sum(team_points), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success(f"Сақталды: {team_name} — {sum(team_points)}/30")
            team_df = read_lesson_table("group_assessment")
            if not team_df.empty:
                st.dataframe(team_df[["group_name", "team_name", "score"]], hide_index=True)
        with summary_tab:
            st.caption("Бекіту тестінің бастапқы 9 ұпайы 20 балға пропорционал есептеледі. Аяқталмаған кезеңдер бос көрсетіледі, қорытынды баға қойылмайды.")
            summary = final_scores()
            if summary.empty:
                st.info("Әзірге студенттердің нәтижелері жоқ.")
            else:
                st.dataframe(summary, hide_index=True, use_container_width=True)
                st.download_button("📥 Қорытынды бағаларды CSV жүктеу", summary.to_csv(index=False).encode("utf-8-sig"), file_name="geologia_final_100.csv", mime="text/csv")
                st.metric("Барлық 4 кезеңді аяқтағандар", int((summary["Кезең"] == "4/4").sum()))
        st.divider()
        st.subheader("📱 Студенттерге арналған QR-код")
        lan = guess_lan_ip()
        current_port = st.context.url.split(":")[-1].split("/")[0] if ":" in st.context.url.split("//")[-1] else "8501"
        suggested = st.context.url if ".streamlit.app" in st.context.url else (f"http://{lan}:{current_port}" if lan else "")
        url = st.text_input("Студенттер кіретін сілтеме", value=suggested,
                            help="Бір Wi-Fi желісінде: http://КОМПЬЮТЕР_IP:8501. Интернетке жарияласаңыз, https://... сілтемесін енгізіңіз.")
        if url.startswith(("http://", "https://")):
            st.image(qr_png(url), width=230, caption="Телефон камерасымен сканерлеңіз")
            st.code(url)
            st.download_button("QR-кодты PNG жүктеу", qr_png(url), file_name="geologia_qr.png", mime="image/png")
        st.warning("Компьютер мен телефон бір Wi-Fi желісінде болуы керек. Windows Firewall сұраса, тек сенімді Private network үшін рұқсат беріңіз. QR-код желіні өздігінен қоспайды.")
        st.caption("Жергілікті results.db файлы осы компьютерде сақталады. Қолданбаны тегін бұлттық хостингке өзгеріссіз орналастырсаңыз, файл тұрақты сақталмауы мүмкін.")
        if st.button("Мұғалім режимінен шығу"):
            st.session_state["teacher_ok"] = False
            st.rerun()

