import hashlib
import json
import os
import random
import re
import streamlit as st

st.set_page_config(page_title="1問1答クイズ", page_icon="📝", layout="centered")

# リポジトリに同梱しておく問題ファイル名
DEFAULT_QUESTIONS_FILE = "questions.txt"

# 間違えた問題の履歴を保存するファイル(直前セットで間違えた問題の警告表示用)
WRONG_HISTORY_FILE = "wrong_answers.json"

# 進行状況(リロード対策)を保存するファイル
SESSION_FILE = "session_progress.json"

# 問題ごとの習熟度(連続正解スコア)を保存するファイル
MASTERY_FILE = "question_mastery.json"

# このスコア以上になったら「卒業」とみなし、苦手克服モードの出題プールから除外する
MASTERY_THRESHOLD = 3

# 1セットあたりの出題数
BATCH_SIZE = 30


# ─── パーサー(元のコードから変更なし) ────────────────────────────────────

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


# ─── 間違えた問題の履歴(前回間違えましたマーク用) ──────────────────────

def question_hash(q):
    return hashlib.md5(q["question"].encode("utf-8")).hexdigest()


def load_wrong_history():
    if not os.path.exists(WRONG_HISTORY_FILE):
        return set()
    try:
        with open(WRONG_HISTORY_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_wrong_history(wrong_set):
    with open(WRONG_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(wrong_set), f, ensure_ascii=False, indent=2)


# ─── 問題ごとの習熟度スコア(苦手克服モードの出題プール判定用) ───────────

def load_mastery():
    if not os.path.exists(MASTERY_FILE):
        return {}
    try:
        with open(MASTERY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_mastery(mastery):
    with open(MASTERY_FILE, "w", encoding="utf-8") as f:
        json.dump(mastery, f, ensure_ascii=False, indent=2)


def update_mastery(q, correct):
    """正解なら+1、不正解なら-1。モードを問わず毎回この記録を更新する。"""
    mastery = load_mastery()
    h = question_hash(q)
    score = mastery.get(h, 0) + (1 if correct else -1)
    mastery[h] = score
    save_mastery(mastery)
    return score


def filter_pool_for_mode(all_questions, mode):
    """苦手克服モードでは、卒業基準(MASTERY_THRESHOLD)に達した問題を出題プールから除外する。"""
    if mode != "weak":
        return list(all_questions)
    mastery = load_mastery()
    return [q for q in all_questions if mastery.get(question_hash(q), 0) < MASTERY_THRESHOLD]


# ─── 進行状況の保存/復元(ページリロード対策) ──────────────────────────

def save_progress():
    if st.session_state.get("questions") is None:
        return
    data = {
        "all_questions": st.session_state.all_questions,
        "mode": st.session_state.mode,
        "questions": st.session_state.questions,
        "index": st.session_state.index,
        "batch_index": st.session_state.batch_index,
        "correct_count": st.session_state.correct_count,
        "answered": st.session_state.answered,
        "phase": st.session_state.phase,
        "user_ans": st.session_state.user_ans,
        "wrong_history_snapshot": sorted(st.session_state.wrong_history_snapshot),
        "wrong_history_current": sorted(st.session_state.wrong_history_current),
    }
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_progress():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, encoding="utf-8") as f:
            data = json.load(f)
        data["wrong_history_snapshot"] = set(data.get("wrong_history_snapshot", []))
        data["wrong_history_current"] = set(data.get("wrong_history_current", []))
        return data
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def clear_progress():
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
        except OSError:
            pass


# ─── セッション状態の初期化 ─────────────────────────────────────────────

def init_state():
    defaults = {
        "all_questions": None,  # アップロードされた全問題(フィルタ前・モード切替の元データ)
        "mode": "normal",       # normal(全問) / weak(苦手克服)
        "questions": None,      # 今回出題する問題(シャッフル・フィルタ後)
        "index": 0,             # questions 配列全体での現在位置(0-indexed)
        "batch_index": 0,       # 現在何セット目か(0-indexed)
        "correct_count": 0,     # 現在のセットでの正解数
        "answered": 0,          # 現在のセットでの回答数
        "phase": "upload",      # upload -> question -> result -> final / mastered
        "user_ans": None,
        "selected_radio": None,
        "wrong_history_snapshot": set(),  # このセット開始時点の「前回間違えた問題」
        "wrong_history_current": set(),   # 今回の履歴(都度ディスクへ保存)
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_quiz():
    clear_progress()
    for k in [
        "all_questions", "mode", "questions", "index", "batch_index",
        "correct_count", "answered", "phase", "user_ans", "selected_radio",
        "wrong_history_snapshot", "wrong_history_current",
    ]:
        if k in st.session_state:
            del st.session_state[k]
    init_state()


def start_quiz(all_questions, mode="normal"):
    st.session_state.all_questions = all_questions
    st.session_state.mode = mode

    pool = filter_pool_for_mode(all_questions, mode)

    if not pool:
        # 苦手克服モード対象の問題が1問も残っていない(全問卒業済み)
        st.session_state.questions = []
        st.session_state.phase = "mastered"
        return

    random.shuffle(pool)
    st.session_state.questions = pool
    st.session_state.index = 0
    st.session_state.batch_index = 0
    st.session_state.correct_count = 0
    st.session_state.answered = 0
    st.session_state.phase = "question"
    snapshot = load_wrong_history()
    st.session_state.wrong_history_snapshot = snapshot
    st.session_state.wrong_history_current = set(snapshot)


def total_batches():
    total = len(st.session_state.questions)
    return -(-total // BATCH_SIZE)  # ceil division


def current_batch_bounds():
    total = len(st.session_state.questions)
    start = st.session_state.batch_index * BATCH_SIZE
    end = min(start + BATCH_SIZE, total)
    return start, end


def try_autoload_default_questions():
    """リポジトリ同梱の questions.txt があれば自動で読み込んですぐ出題開始する(通常モード)"""
    if st.session_state.questions is not None:
        return
    if not os.path.exists(DEFAULT_QUESTIONS_FILE):
        return
    with open(DEFAULT_QUESTIONS_FILE, encoding="utf-8") as f:
        raw = f.read()
    questions = load_questions_from_text(raw)
    if not questions:
        return
    start_quiz(questions, "normal")


init_state()

if st.session_state.questions is None:
    _progress = load_progress()
    if _progress is not None:
        for _k, _v in _progress.items():
            st.session_state[_k] = _v
    else:
        try_autoload_default_questions()


MODE_LABELS = {
    "normal": "通常モード(全問)",
    "weak": f"苦手克服モード(連続正解{MASTERY_THRESHOLD}回で卒業)",
}


# ─── 画面: アップロード ───────────────────────────────────────────────────

def screen_upload():
    st.title("📝 1問1答クイズ")
    st.write("問題ファイル(.txt)をアップロードしてください。")
    st.caption(f"※ {BATCH_SIZE}問ずつのセットに分けて出題されます。")

    mode = st.radio(
        "出題モード",
        options=["normal", "weak"],
        format_func=lambda m: MODE_LABELS[m],
        horizontal=True,
    )

    uploaded = st.file_uploader("問題ファイルを選択", type=["txt"])

    if uploaded is not None:
        raw = uploaded.read().decode("utf-8")
        questions = load_questions_from_text(raw)

        if not questions:
            st.error("問題が読み込めませんでした。ファイルの形式を確認してください。")
            return

        start_quiz(questions, mode)
        save_progress()
        st.rerun()

    with st.expander("対応しているファイル形式を見る"):
        st.code(
            "Q: 日本の首都は?\n"
            "A: 大阪\n"
            "B: 東京\n"
            "C: 名古屋\n"
            "ANS: B\n"
            "EXP: 東京は日本の首都です。\n"
            "---\n"
            "(次の問題も同じ形式で続ける)",
            language="text",
        )


# ─── 画面: 出題 ───────────────────────────────────────────────────────────

def screen_question():
    questions = st.session_state.questions
    batch_start, batch_end = current_batch_bounds()
    batch_total = batch_end - batch_start
    index = st.session_state.index
    local_index = index - batch_start
    q = questions[index]

    st.progress(
        local_index / batch_total,
        text=f"第{st.session_state.batch_index + 1}/{total_batches()}セット　{local_index}/{batch_total} 問",
    )
    st.caption(MODE_LABELS.get(st.session_state.mode, "通常モード"))
    st.subheader(f"問題 {local_index + 1}")

    if question_hash(q) in st.session_state.wrong_history_snapshot:
        st.warning("⚠️ 前回間違えました")

    if st.session_state.mode == "weak":
        score = load_mastery().get(question_hash(q), 0)
        st.caption(f"連続正解カウント: {score}/{MASTERY_THRESHOLD}(到達で卒業)")

    st.write(q["question"])

    keys = sorted(q["choices"].keys())

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

        q_hash = question_hash(q)
        is_correct = choice == q["answer"]
        if is_correct:
            st.session_state.correct_count += 1
            st.session_state.wrong_history_current.discard(q_hash)
        else:
            st.session_state.wrong_history_current.add(q_hash)
        save_wrong_history(st.session_state.wrong_history_current)
        update_mastery(q, is_correct)

        st.session_state.phase = "result"
        save_progress()
        st.rerun()


# ─── 画面: 結果表示 ───────────────────────────────────────────────────────

def screen_result():
    questions = st.session_state.questions
    _, batch_end = current_batch_bounds()
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

    is_last_in_batch = index + 1 >= batch_end
    button_label = "結果を見る" if is_last_in_batch else "次の問題へ"

    if st.button(button_label, type="primary"):
        if is_last_in_batch:
            st.session_state.phase = "final"
        else:
            st.session_state.index += 1
            st.session_state.phase = "question"
        save_progress()
        st.rerun()


# ─── 画面: セット結果 / 最終結果 ──────────────────────────────────────────

def screen_final():
    correct_count = st.session_state.correct_count
    answered = st.session_state.answered
    rate = correct_count / answered * 100 if answered > 0 else 0

    is_last_batch = (st.session_state.batch_index + 1) >= total_batches()

    st.title("🏁 クイズ終了！" if is_last_batch else f"✅ 第{st.session_state.batch_index + 1}セット終了！")
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

    if is_last_batch:
        if st.button("もう一度挑戦する", type="primary"):
            reset_quiz()
            st.rerun()
    else:
        if st.button(f"次の{BATCH_SIZE}問へ", type="primary"):
            st.session_state.batch_index += 1
            st.session_state.index = st.session_state.batch_index * BATCH_SIZE
            st.session_state.correct_count = 0
            st.session_state.answered = 0
            st.session_state.phase = "question"
            save_progress()
            st.rerun()


# ─── 画面: 苦手克服モード対象が全問卒業済み ──────────────────────────────

def screen_mastered():
    st.title("🎓 全問マスター！")
    st.success(f"苦手克服モード対象の問題は、すべて卒業基準(連続正解{MASTERY_THRESHOLD}回)を満たしています。")
    st.write("通常モードで復習を続けるか、最初からやり直せます。")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("通常モードで復習する", type="primary"):
            start_quiz(st.session_state.all_questions, "normal")
            save_progress()
            st.rerun()
    with col2:
        if st.button("最初からやり直す"):
            reset_quiz()
            st.rerun()


# ─── サイドバー: モード切替 / 別ファイルで試したいとき用 ─────────────────

with st.sidebar:
    st.markdown("### 出題モード")
    current_mode = st.session_state.get("mode", "normal")
    mode_choice = st.radio(
        "モードを選択",
        options=["normal", "weak"],
        format_func=lambda m: MODE_LABELS[m],
        index=0 if current_mode == "normal" else 1,
        key="sidebar_mode_selector",
    )
    if st.session_state.get("all_questions") is not None and mode_choice != current_mode:
        if st.button("このモードで出題し直す"):
            start_quiz(st.session_state.all_questions, mode_choice)
            save_progress()
            st.rerun()

    st.markdown("### 別の問題ファイルで試す")
    override = st.file_uploader("問題ファイル(.txt)を差し替え", type=["txt"], key="override_uploader")
    if override is not None:
        raw = override.read().decode("utf-8")
        questions = load_questions_from_text(raw)
        if questions:
            start_quiz(questions, mode_choice)
            save_progress()
            st.rerun()
        else:
            st.error("問題が読み込めませんでした。")


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
elif phase == "mastered":
    screen_mastered()
