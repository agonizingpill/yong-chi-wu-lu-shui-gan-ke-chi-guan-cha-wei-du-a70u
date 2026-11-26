from random import Random

TEAM = (
    ("shu", "沈舒 · 科目观察", "technician"),
    ("fang", "方薇 · 教研核校", "supervisor"),
    ("cheng", "程墨 · 教案查阅", "viewer"),
)
ITEMS = (
    ("CW-01", "开场时学员能复述本轮安排。", "orientation", False, True),
    ("CW-02", "学员清楚下一次轮到自己做哪项。", "orientation", False, False),
    ("CW-03", "等待轮转时，学员仍不清楚接下来要做什么。", "orientation", True, False),
    ("CW-04", "教练解释时，学员开始主动提问。", "interaction", False, False),
    ("CW-05", "分组交流时，学员能互相描述感受。", "interaction", False, False),
    ("CW-06", "收课交流中，学员大多保持沉默。", "interaction", True, False),
)
# 试填题：TW-01 整批作答充分（可估计载荷）；TW-02 留空较多，用来演示门槛未达与随新表继续试填。
TRIAL_ITEMS = (
    ("TW-01", "学员愿意在课后主动说一句自己的水感变化。", "trial", False),
    ("TW-02", "学员能指出自己这一轮换气节奏的进步。", "trial", False),
)
COHORTS = (
    ("COACH-A", "晨课教练组", 16, 10),
    ("COACH-B", "午间教练组", 16, 10),
    ("COACH-C", "晚课教练组", 16, 4),
)


def observations():
    rng = Random(1084)
    for group, _, _, base in COHORTS:
        for i in range(base):
            first, second = rng.randint(1, 5), rng.randint(1, 5)
            values = [max(1, min(5, factor + rng.choice([-1, 0, 0, 0, 1])))
                      for factor in (first, first, first, second, second, second)]
            values[2] = 6 - values[2]
            values[5] = 6 - values[5]
            # TW-01 跟随互动因子（second）作答；TW-02 仅部分班组、部分问卷作答，其余留空。
            tw1 = max(1, min(5, second + rng.choice([-1, 0, 0, 1])))
            tw2 = max(1, min(5, second + rng.choice([-1, 0, 1]))) if (
                group != "COACH-C" and i % 2 == 0) else None
            yield group, f"{group}-{i}", values, tw1, tw2
