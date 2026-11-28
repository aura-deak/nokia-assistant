"""52 周奋斗模拟游戏（重实现自 https://github.com/aura-deak/FightOrRest）。

一年 52 周，每周二选一：休息（精力+5 灵感+3）或奋斗（精力-5 灵感-3，
成就 +7 + 随机数，随机数上限为当前灵感）。精力或灵感归零提前结束。
纯服务端状态，无需 JS，进度存会话 Cookie。"""

import random

from flask import Blueprint, redirect, render_template, request, session

from helpers import msg_page

bp = Blueprint("game", __name__)

CAP = 20
WEEKS = 52
STATE_KEY = "for_game"


def _new_state():
    return {"week": 0, "energy": CAP, "inspiration": CAP,
            "achievements": [0] * WEEKS, "over": False, "log": []}


def _clamp(v):
    return max(0, min(CAP, v))


@bp.route("/game")
def index():
    state = session.get(STATE_KEY)
    if not state or request.args.get("new") == "1":
        state = _new_state()
        session[STATE_KEY] = state
    return render_template("game/index.html", s=state, cap=CAP)


@bp.route("/game/act", methods=["POST"])
def act():
    state = session.get(STATE_KEY)
    if not state:
        return redirect("/game")
    if state.get("over"):
        return redirect("/game")
    choice = request.form.get("c")
    w = state["week"]
    if choice == "r":
        state["energy"] = _clamp(state["energy"] + 5)
        state["inspiration"] = _clamp(state["inspiration"] + 3)
        state["log"].insert(0, "第%d周：休息。精力 %d，灵感 %d。"
                            % (w + 1, state["energy"], state["inspiration"]))
    elif choice == "f":
        state["energy"] = state["energy"] - 5
        state["inspiration"] = state["inspiration"] - 3
        if state["energy"] <= 0 or state["inspiration"] <= 0:
            state["achievements"][w] = 0
            state["over"] = True
            state["log"].insert(0, "第%d周：奋斗后精力或灵感耗尽，游戏结束。" % (w + 1))
            state["week"] = w + 1
            session[STATE_KEY] = state
            return redirect("/game")
        gain = 7 + random.randint(0, state["inspiration"])
        state["achievements"][w] = gain
        state["log"].insert(0, "第%d周：奋斗，本周成就 +%d。精力 %d，灵感 %d。"
                            % (w + 1, gain, state["energy"], state["inspiration"]))
    else:
        return msg_page("提示", ["请选择休息或奋斗。"], back="/game")
    state["week"] = w + 1
    if state["week"] >= WEEKS:
        state["over"] = True
        state["log"].insert(0, "52 周结束，游戏完成。")
    session[STATE_KEY] = state
    return redirect("/game")
