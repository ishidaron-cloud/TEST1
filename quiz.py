import random
import re
import streamlit as st

st.set_page_config(page_title="1問1答クイズ", page_icon="📝", layout="centered")


# ─── パーサー（元のコードから変更なし） ────────────────────────────────────

def parse_original_format(raw):
    questions = []
    blocks = [b.strip() for b in raw.split("---") if b.strip()]
    for block in blocks:
        q = {}
        choices = {}
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("Q:"):
                q["question"] = line[2:].strip()
            elif line.startswith("ANS:"):
                q["answer"] = line[4:].strip().upper()
            elif line.startswith("EXP:"):
                q["explanation"] = line[4:].strip()
            elif len(line) >= 3 and line[1] == ":" and line[0].isalpha():
                choices[line[0].upper()] = line[2:].strip()
        if "question" in q and "answer" in q and choices:
            q["choices"] = choices
            questions.append(q)
    return questions


def parse_new_format(raw):
    questions = []
    blocks = re.split(r'\n*問題\d+[：:]\s*\n+', raw)
    blocks = [b.strip() for b in blocks if b.strip()]

    for block in blocks:
        q_lines = []
        choices = []
        answer_text = ""
        exp_lines = []
        state = "QUESTION"

        for line in block.splitlines():
            s = line.strip()
            if not s:
                continue
            if re.match(r'^正解[：:]', s):
                state = "ANSWER"
                continue
            if re.match(r'^解説[：:]', s):
                state = "EXPLANATION"
                continue

            if state == "QUESTION":
                if s.startswith("□"):
                    state = "CHOICES"
                    text = s.lstrip("□").strip()
                    if text:
                        choices.append(text)
                else:
                    q_lines.append(s)
            elif state == "CHOICES":
                if s.startswith("□"):
                    text = s.lstrip("□").strip()
                    if text:
                        choices.append(text)
            elif state == "ANSWER":
                if s.startswith("✔") or s.startswith("✓"):
                    answer_text = s.lstrip("✔✓").strip()
            elif state == "EXPLANATION":
                if not re.match(r'^[（(]', s):
                    exp_lines.append(s)

        if not (q_lines and choices and answer_text):
            continue

        answer_idx = None
        for i, c in enumerate(choices):
            if answer_text == c or answer_text in c or c in answer_text:
                answer_idx = i
                break

        if answer_idx is None:
            continue

        questions.append({
            "question": "\n".join(q_lines),
            "choices": {str(i + 1): c for i, c in enumerate(choices)},
            "answer": str(answer_idx + 1),
            "explanation": "\n".join(exp_lines),
        })

    return questions


def load_questions_from_text(raw):
    if re.search(r'問題\d+[：:]', raw):
        return parse_new_format(raw)
    return parse_original_format(raw)


# ─── セッション状態の初期化 ─────────────────────────────────────────────

def init_state():
    defaults = {
        "questions": None,
        "index": 0,          # 0-indexed, 現在の問題番号
        "correct_count": 0,
        "answered": 0,
        "phase": "upload",   # upload -> question -> result -> final
        "user_ans": None,
        "selected_radio": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_quiz():
    for k in ["questions", "index", "correct_count", "answered", "phase", "user_ans", "selected_radio"]:
        if k in st.session_state:
            del st.session_state[k]
    init_state()


init_state()


# ─── 画面: アップロード ───────────────────────────────────────────────────

def screen_upload():
    st.title("📝 1問1答クイズ")
    st.write("問題ファイル（.txt）をアップロードしてください。")

    uploaded = st.file_uploader("問題ファイルを選択", type=["txt"])

    if uploaded is not None:
        raw = uploaded.read().decode("utf-8")
        questions = load_questions_from_text(raw)

        if not questions:
            st.error("問題が読み込めませんでした。ファイルの形式を確認してください。")
            return

        random.shuffle(questions)
        st.session_state.questions = questions
        st.session_state.phase = "question"
        st.rerun()

    with st.expander("対応しているファイル形式を見る"):
        st.code(
            "Q: 日本の首都は？\n"
            "A: 大阪\n"
            "B: 東京\n"
            "C: 名古屋\n"
            "ANS: B\n"
            "EXP: 東京は日本の首都です。\n"
            "---\n"
            "（次の問題も同じ形式で続ける）",
            language="text",
        )


# ─── 画面: 出題 ───────────────────────────────────────────────────────────

def screen_question():
    questions = st.session_state.questions
    total = len(questions)
    index = st.session_state.index
    q = questions[index]

    st.progress(index / total, text=f"{index}/{total} 問")
    st.subheader(f"問題 {index + 1}")
    st.write(q["question"])

    keys = sorted(q["choices"].keys())
    labels = [f"{k}. {q['choices'][k]}" for k in keys]

    choice = st.radio(
        "選択肢",
        options=keys,
        format_func=lambda k: f"{k}. {q['choices'][k]}",
        key=f"radio_{index}",
        index=None,
    )

    if st.button("回答する", type="primary", disabled=(choice is None)):
        st.session_state.user_ans = choice
        st.session_state.answered += 1
        if choice == q["answer"]:
            st.session_state.correct_count += 1
        st.session_state.phase = "result"
        st.rerun()


# ─── 画面: 結果表示 ───────────────────────────────────────────────────────

def screen_result():
    questions = st.session_state.questions
    total = len(questions)
    index = st.session_state.index
    q = questions[index]
    user_ans = st.session_state.user_ans
    correct = q["answer"]
    is_correct = user_ans == correct

    if is_correct:
        st.success("✓ 正解！")
    else:
        st.error("✗ 不正解")
        st.info(
            f"**正解:** {correct}. {q['choices'][correct]}\n\n"
            f"**あなたの回答:** {user_ans}. {q['choices'][user_ans]}"
        )

    exp = q.get("explanation", "").strip()
    if exp:
        with st.container(border=True):
            st.markdown("**解説**")
            st.write(exp)

    is_last = index + 1 >= total
    button_label = "結果を見る" if is_last else "次の問題へ"

    if st.button(button_label, type="primary"):
        if is_last:
            st.session_state.phase = "final"
        else:
            st.session_state.index += 1
            st.session_state.phase = "question"
        st.rerun()


# ─── 画面: 最終結果 ───────────────────────────────────────────────────────

def screen_final():
    correct_count = st.session_state.correct_count
    answered = st.session_state.answered
    rate = correct_count / answered * 100 if answered > 0 else 0

    st.title("🏁 クイズ終了！")
    st.metric("正解数", f"{correct_count} / {answered}")
    st.progress(rate / 100, text=f"正答率 {rate:.1f}%")

    if rate == 100:
        st.balloons()
        st.success("🎉 パーフェクト！素晴らしい！")
    elif rate >= 80:
        st.success("✨ よくできました！")
    elif rate >= 60:
        st.info("📖 もう少し！復習しましょう。")
    else:
        st.warning("💪 もう一度チャレンジしてみましょう！")

    if st.button("もう一度挑戦する", type="primary"):
        reset_quiz()
        st.rerun()


# ─── メイン ──────────────────────────────────────────────────────────────

phase = st.session_state.phase

if phase == "upload":
    screen_upload()
elif phase == "question":
    screen_question()
elif phase == "result":
    screen_result()
elif phase == "final":
    screen_final()