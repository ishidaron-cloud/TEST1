import hashlib
import json
import os
import random
import re
import streamlit as st
 
st.set_page_config(page_title="1問1答クイズ", page_icon="📝", layout="centered")
 
# リポジトリに同梱しておく問題ファイル名
DEFAULT_QUESTIONS_FILE = "questions.txt"
 
# 苦手克服モード用の永続データ（誤答フラグ・進行中セッション）
PROGRESS_FILE = "progress.json"
ROUND_SIZE = 30                # 通常モード・苦手克服モードで一度に出題する問題数
MASTERED_MIN_ATTEMPTS = 3     # この回数以上解いていて
MASTERED_ACCURACY = 0.9       # 正答率がこの値以上なら「マスター済み」として出題プールから外す
 
 
# ─── パーサー ────────────────────────────────────────────────────────────
 
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
        if "question" in q and "answer" in q and choices and q["answer"] in choices:
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
            if answer_text == c:
                answer_idx = i
                break
        if answer_idx is None:
            for i, c in enumerate(choices):
                if answer_text in c or c in answer_text:
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
 
 
# 【1】問題文 / A. 選択肢 / 正解: A, C / 解説: ... という形式
BRACKET_HEAD_RE = re.compile(r'(?m)^[ \t]*【\d+】')
CHOICE_LINE_RE = re.compile(r'^([A-Z])[.．]\s*(.+)$')
 
 
def parse_bracket_format(raw):
    questions = []
    blocks = [b.strip() for b in BRACKET_HEAD_RE.split(raw) if b.strip()]
 
    for block in blocks:
        q_lines = []
        choices = {}
        answer_keys_found = []
        exp_lines = []
        last_key = None
        state = "QUESTION"
 
        for line in block.splitlines():
            s = line.strip()
            if not s:
                continue
 
            m = re.match(r'^正解[：:]\s*(.*)$', s)
            if m and state in ("QUESTION", "CHOICES"):
                answer_keys_found = re.findall(r'[A-Z]', m.group(1).upper())
                state = "ANSWER"
                continue
            m = re.match(r'^解説[：:]\s*(.*)$', s)
            if m and state != "EXPLANATION":
                state = "EXPLANATION"
                if m.group(1):
                    exp_lines.append(m.group(1))
                continue
 
            if state in ("QUESTION", "CHOICES"):
                # 「A.」から順番に並んでいる行だけを選択肢とみなす（問題文中のコード行の誤検出防止）
                m = CHOICE_LINE_RE.match(s)
                expected = chr(ord("A") + len(choices))
                if m and m.group(1) == expected:
                    last_key = m.group(1)
                    choices[last_key] = m.group(2).strip()
                    state = "CHOICES"
                elif state == "CHOICES":
                    choices[last_key] += "\n" + s   # 選択肢が複数行にまたがる場合
                else:
                    q_lines.append(s)
            elif state == "EXPLANATION":
                exp_lines.append(s)
 
        # 選択肢の一覧が抜けている問題は、解説の「A. 選択肢 - 説明」から復元する
        if not choices:
            for s in exp_lines:
                m = re.match(r'^([A-Z])[.．]\s*(.+?)\s+-\s+', s)
                if m and m.group(1) == chr(ord("A") + len(choices)):
                    choices[m.group(1)] = m.group(2).strip()
 
        if not (q_lines and len(choices) >= 2 and answer_keys_found):
            continue
        if any(k not in choices for k in answer_keys_found):
            continue
 
        keys = sorted(set(answer_keys_found))
        questions.append({
            "question": "\n".join(q_lines),
            "choices": choices,
            # 単一回答は文字列、複数回答はリストで保持する
            "answer": keys[0] if len(keys) == 1 else keys,
            "explanation": "\n".join(exp_lines),
        })
 
    return questions
 
 
def load_questions_from_text(raw):
    raw = raw.lstrip("﻿")
    if re.search(r'問題\d+[：:]', raw):
        return parse_new_format(raw)
    if BRACKET_HEAD_RE.search(raw):
        return parse_bracket_format(raw)
    return parse_original_format(raw)
 
 
def load_and_shuffle_questions(raw):
    """テキストをパースし、出題順と選択肢の表示順をシャッフルする"""
    questions = load_questions_from_text(raw)
    if not questions:
        return questions
    random.shuffle(questions)
    for q in questions:
        q["qid"] = qid_for(q)
        order = list(q["choices"].keys())
        random.shuffle(order)
        q["choice_order"] = order
 
    progress = load_progress()
    register_questions(progress, questions)
    save_progress(progress)
 
    return questions
 
 
# ─── 回答キーの補助関数（単一回答 / 複数回答の両対応） ───────────────────
 
def as_keys(ans):
    """回答（文字列 or リスト or None）をキーのリストにそろえる"""
    if ans is None:
        return []
    if isinstance(ans, (list, tuple, set)):
        return sorted(ans)
    return [ans]
 
 
def fmt_answers(choices, ans):
    keys = as_keys(ans)
    if not keys:
        return "（未回答）"
    if len(keys) == 1:
        return f"{keys[0]}. {choices.get(keys[0], '')}"
    return "\n".join(f"- {k}. {choices.get(k, '')}" for k in keys)
 
 
def md_lines(text):
    """改行をMarkdownの改行として表示する（コード入りの問題文が1行につぶれないように）。
    「$」は数式記号として解釈されないようエスケープする。"""
    return text.replace("$", "\\$").replace("\n", "  \n")
 
 
# ─── 苦手克服モード: 誤答フラグ・進行中セッションの永続化 ─────────────────
 
def qid_for(q):
    """問題文から安定した識別子を作る（ファイルを読み直しても同じ問題は同じID）"""
    return hashlib.md5(q["question"].encode("utf-8")).hexdigest()[:12]
 
 
def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return {"bank": {}, "stats": {}, "session": None}
    try:
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"bank": {}, "stats": {}, "session": None}
    data.setdefault("bank", {})
    data.setdefault("stats", {})
    data.setdefault("session", None)
    return data
 
 
def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
 
 
def register_questions(progress, questions):
    """出題された問題を苦手データのバンクに登録する（既存の誤答記録は保持したまま）"""
    for q in questions:
        qid = q["qid"]
        progress["bank"][qid] = {
            "question": q["question"],
            "choices": q["choices"],
            "answer": q["answer"],
            "explanation": q.get("explanation", ""),
        }
        progress["stats"].setdefault(qid, {"wrong": 0, "correct": 0})
 
 
def record_answer(qid, is_correct):
    progress = load_progress()
    stat = progress["stats"].setdefault(qid, {"wrong": 0, "correct": 0})
    if is_correct:
        stat["correct"] += 1
    else:
        stat["wrong"] += 1
    save_progress(progress)
 
 
def build_weak_round(progress, size=ROUND_SIZE):
    """誤答フラグの多い問題ほど出やすくなるよう重み付けして出題セットを作る。
    正答率が高くなった（マスター済みの）問題はプールから外す。"""
    bank = progress["bank"]
    stats = progress["stats"]
 
    pool = []
    weights = []
    for qid in bank:
        stat = stats.get(qid, {"wrong": 0, "correct": 0})
        attempts = stat["wrong"] + stat["correct"]
        if attempts >= MASTERED_MIN_ATTEMPTS and stat["correct"] / attempts >= MASTERED_ACCURACY:
            continue
        pool.append(qid)
        weights.append(1 + stat["wrong"] * 2)
 
    if not pool:  # 全問マスター済みなら、全問から出し直す
        pool = list(bank.keys())
        weights = [1] * len(pool)
 
    picked = []
    for _ in range(min(size, len(pool))):
        chosen = random.choices(pool, weights=weights, k=1)[0]
        i = pool.index(chosen)
        picked.append(chosen)
        pool.pop(i)
        weights.pop(i)
 
    questions = []
    for qid in picked:
        q = dict(bank[qid])
        q["qid"] = qid
        order = list(q["choices"].keys())
        random.shuffle(order)
        q["choice_order"] = order
        questions.append(q)
    return questions
 
 
# ─── セッション状態の初期化 ─────────────────────────────────────────────
 
def init_state():
    defaults = {
        "questions": None,
        "mode": "normal",    # normal | weak
        "index": 0,          # 0-indexed, 現在の問題番号
        "correct_count": 0,
        "answered": 0,
        "phase": "upload",   # upload -> question -> result -> final
        "user_ans": None,
        "selected_radio": None,
        "history": [],       # 回答履歴（間違えた問題の復習用）
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
 
 
def reset_quiz():
    for k in ["questions", "mode", "index", "correct_count", "answered", "phase", "user_ans", "selected_radio", "history"]:
        if k in st.session_state:
            del st.session_state[k]
    init_state()
    progress = load_progress()
    progress["session"] = None
    save_progress(progress)
 
 
def start_quiz(questions, mode):
    """出題セットとモードをセットして出題を開始し、再開用スナップショットを保存する"""
    st.session_state.questions = questions
    st.session_state.mode = mode
    st.session_state.index = 0
    st.session_state.correct_count = 0
    st.session_state.answered = 0
    st.session_state.history = []
    st.session_state.phase = "question"
    save_session_snapshot()
 
 
def save_session_snapshot():
    """進行中の出題セッションをディスクに保存し、画面リロード後も続きから再開できるようにする"""
    if not st.session_state.get("questions"):
        return
    progress = load_progress()
    progress["session"] = {
        "mode": st.session_state.mode,
        "qids": [{"qid": q["qid"], "choice_order": q["choice_order"]} for q in st.session_state.questions],
        "index": st.session_state.index,
        "correct_count": st.session_state.correct_count,
        "answered": st.session_state.answered,
        "history": st.session_state.history,
        "phase": st.session_state.phase,
    }
    save_progress(progress)
 
 
def try_resume_session():
    """ディスクに進行中セッションがあれば復元する（画面リロード対策）"""
    if st.session_state.questions is not None:
        return
    progress = load_progress()
    snap = progress.get("session")
    if not snap:
        return
    bank = progress["bank"]
    try:
        questions = []
        for item in snap["qids"]:
            q = dict(bank[item["qid"]])
            q["qid"] = item["qid"]
            q["choice_order"] = item["choice_order"]
            questions.append(q)
    except KeyError:
        return
    if not questions:
        return
 
    st.session_state.questions = questions
    st.session_state.mode = snap.get("mode", "normal")
    st.session_state.index = snap.get("index", 0)
    st.session_state.correct_count = snap.get("correct_count", 0)
    st.session_state.answered = snap.get("answered", 0)
    st.session_state.history = snap.get("history", [])
    st.session_state.phase = snap.get("phase", "question")
    # 結果画面でリロードされた場合に備えて、直前の回答も復元する
    if st.session_state.phase == "result" and st.session_state.history:
        st.session_state.user_ans = st.session_state.history[-1]["user_ans"]
 
 
init_state()
try_resume_session()
 
 
# ─── 画面: アップロード ───────────────────────────────────────────────────
 
def screen_upload():
    st.title("📝 1問1答クイズ")
 
    progress = load_progress()
    has_bank = len(progress["bank"]) > 0
 
    mode = st.radio(
        "モードを選択",
        options=["normal", "weak"],
        format_func=lambda m: f"通常モード（{ROUND_SIZE}問ずつランダム出題）" if m == "normal" else "苦手克服モード（間違えやすい問題を優先出題）",
    )
 
    if mode == "weak":
        if not has_bank:
            st.info("苦手克服モードを使うには、まず通常モードで問題を解いて記録を作ってください。")
            return
 
        wrong_total = sum(1 for s in progress["stats"].values() if s["wrong"] > 0)
        st.write(f"これまでの記録: {len(progress['bank'])}問中 {wrong_total}問で誤答あり。")
        st.caption(f"誤答の多い問題を優先して、最大{ROUND_SIZE}問を出題します。")
 
        if st.button("苦手克服モードを開始", type="primary"):
            questions = build_weak_round(progress)
            start_quiz(questions, "weak")
            st.rerun()
        return
 
    st.write("問題ファイル（.txt）をアップロードしてください。")
    st.caption(f"読み込んだ問題の中からランダムに{ROUND_SIZE}問を出題します（残りは苦手克服モードの母集団になります）。")
 
    if os.path.exists(DEFAULT_QUESTIONS_FILE) and st.button("同梱の問題で始める", type="primary"):
        with open(DEFAULT_QUESTIONS_FILE, encoding="utf-8") as f:
            raw = f.read()
        questions = load_and_shuffle_questions(raw)
        if not questions:
            st.error("同梱の問題ファイルが読み込めませんでした。")
        else:
            start_quiz(questions[:ROUND_SIZE], "normal")
            st.rerun()
 
    uploaded = st.file_uploader("問題ファイルを選択", type=["txt"])
 
    if uploaded is not None:
        try:
            raw = uploaded.read().decode("utf-8")
        except UnicodeDecodeError:
            st.error("ファイルの文字コードを確認してください(UTF-8のテキストファイルを想定しています)。")
            return
 
        questions = load_and_shuffle_questions(raw)
 
        if not questions:
            st.error("問題が読み込めませんでした。ファイルの形式を確認してください。")
            return
 
        start_quiz(questions[:ROUND_SIZE], "normal")
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
        st.code(
            "【1】日本の首都は？\n"
            "\n"
            "A. 大阪\n"
            "B. 東京\n"
            "C. 名古屋\n"
            "\n"
            "正解: B\n"
            "（複数回答の場合は「正解: A, C」）\n"
            "\n"
            "解説:\n"
            "東京は日本の首都です。",
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
    st.write(md_lines(q["question"]))
 
    keys = q.get("choice_order") or sorted(q["choices"].keys())
    correct_keys = as_keys(q["answer"])
 
    if len(correct_keys) > 1:
        # 複数回答の問題はチェックボックスで選ぶ
        st.caption(f"{len(correct_keys)}つ選択してください")
        picked = [
            k for k in keys
            if st.checkbox(f"{k}. {q['choices'][k]}", key=f"check_{index}_{k}")
        ]
        choice = sorted(picked)
        ready = len(picked) == len(correct_keys)
    else:
        choice = st.radio(
            "選択肢",
            options=keys,
            format_func=lambda k: f"{k}. {q['choices'][k]}",
            key=f"radio_{index}",
            index=None,
        )
        ready = choice is not None
 
    if st.button("回答する", type="primary", disabled=not ready):
        st.session_state.user_ans = choice
        st.session_state.answered += 1
        is_correct = as_keys(choice) == correct_keys
        if is_correct:
            st.session_state.correct_count += 1
        st.session_state.history.append({
            "question": q["question"],
            "choices": q["choices"],
            "user_ans": choice,
            "correct_ans": q["answer"],
            "is_correct": is_correct,
            "explanation": q.get("explanation", ""),
        })
        record_answer(q["qid"], is_correct)
        st.session_state.phase = "result"
        save_session_snapshot()
        st.rerun()
 
 
# ─── 画面: 結果表示 ───────────────────────────────────────────────────────
 
def screen_result():
    questions = st.session_state.questions
    total = len(questions)
    index = st.session_state.index
    q = questions[index]
    user_ans = st.session_state.user_ans
    correct = q["answer"]
    is_correct = as_keys(user_ans) == as_keys(correct)
    multi = len(as_keys(correct)) > 1
    sep = "\n" if multi else " "
 
    if is_correct:
        st.success("✓ 正解！")
    else:
        st.error("✗ 不正解")
        st.info(
            f"**正解:**{sep}{fmt_answers(q['choices'], correct)}\n\n"
            f"**あなたの回答:**{sep}{fmt_answers(q['choices'], user_ans)}"
        )
 
    exp = q.get("explanation", "").strip()
    if exp:
        with st.container(border=True):
            st.markdown("**解説**")
            st.write(md_lines(exp))
 
    is_last = index + 1 >= total
    button_label = "結果を見る" if is_last else "次の問題へ"
 
    if st.button(button_label, type="primary"):
        if is_last:
            st.session_state.phase = "final"
        else:
            st.session_state.index += 1
            st.session_state.phase = "question"
        save_session_snapshot()
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
 
    wrong = [h for h in st.session_state.history if not h["is_correct"]]
    if wrong:
        with st.expander(f"間違えた問題を復習する（{len(wrong)}問）"):
            for i, h in enumerate(wrong, start=1):
                st.markdown(f"**{i}.** {md_lines(h['question'])}")
                sep = "\n" if len(as_keys(h["correct_ans"])) > 1 else " "
                st.write(f"あなたの回答:{sep}{fmt_answers(h['choices'], h['user_ans'])}")
                st.write(f"正解:{sep}{fmt_answers(h['choices'], h['correct_ans'])}")
                if h["explanation"].strip():
                    st.caption(md_lines(h["explanation"]))
                st.divider()
 
    if st.button("もう一度挑戦する", type="primary"):
        reset_quiz()
        st.rerun()
 
 
# ─── サイドバー: 別ファイルで試したいとき用 ──────────────────────────────
 
with st.sidebar:
    st.markdown("### 別の問題ファイルで試す")
    override = st.file_uploader("問題ファイル(.txt)を差し替え", type=["txt"], key="override_uploader")
    if override is not None:
        try:
            raw = override.read().decode("utf-8")
        except UnicodeDecodeError:
            st.error("ファイルの文字コードを確認してください（UTF-8のテキストファイルを想定しています）。")
        else:
            questions = load_and_shuffle_questions(raw)
            if questions:
                start_quiz(questions[:ROUND_SIZE], "normal")
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
