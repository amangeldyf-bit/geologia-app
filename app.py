import io
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
st.markdown("""
<style>
.block-container {max-width: 1050px; padding-top: 2rem;}
[data-testid="stMetric"] {background: #edf4f8; border-radius: 12px; padding: 14px;}
</style>
""", unsafe_allow_html=True)
st.title("🛢️ Геологиялық экспедиция")
st.caption("1-бағалау: интерактивті тапсырмалар • 2-бағалау: ауызша сұрақ-жауап")

student_tab, teacher_tab = st.tabs(["📱 Студент", "👩‍🏫 Мұғалім"])

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
            name = st.text_input("Аты-жөніңіз", placeholder="Мысалы: Айдана Нұрланқызы")
            group = st.text_input("Тобыңыз", placeholder="Мысалы: ГР-31")
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
        st.subheader("📱 Студенттерге арналған QR-код")
        lan = guess_lan_ip()
        current_port = st.context.url.split(":")[-1].split("/")[0] if ":" in st.context.url.split("//")[-1] else "8501"
        suggested = f"http://{lan}:{current_port}" if lan else ""
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

