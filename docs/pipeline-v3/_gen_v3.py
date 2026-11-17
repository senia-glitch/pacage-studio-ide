"""Главная схема студии v3 — одна картинка (SVG) из всех принятых пунктов."""

import html
import pathlib

W = 960
FX, FW = 50, 710          # рамки этапов
COLS = [65, 300, 535]     # колонки квадратов
CW = 190
CXS = [160, 395, 630]     # центры колонок
SX, SW = 793, 149         # разделы банка
AMBER = "#b45309"
GREEN = "#3f8a3f"
out: list[str] = []


def esc(s):
    return html.escape(s, quote=False)


def text(x, y, s, size=14, weight=400, anchor="start", fill="#222", italic=False):
    it = ' font-style="italic"' if italic else ""
    out.append(
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
        f'text-anchor="{anchor}" fill="{fill}"{it}>{esc(s)}</text>'
    )


def box(x, y, w, h, title, subs=(), kind="step", num=None, tsize=16):
    fill, stroke, dash = {
        "step": ("#fff", "#333", ""),
        "opt": ("#fff", "#333", ' stroke-dasharray="6 4"'),
        "bank": ("#e3f1e3", GREEN, ""),
    }[kind]
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="1.8"{dash}/>'
    )
    lines = title if isinstance(title, list) else [title]
    subs = subs if isinstance(subs, (list, tuple)) else [subs]
    total = len(lines) * 21 + (len(subs) * 17 + 3 if subs else 0)
    base = y + (h - total) / 2 + 16
    for i, line in enumerate(lines):
        text(x + w / 2, base + i * 21, line, tsize, 700, "middle")
    for i, s in enumerate(subs):
        text(x + w / 2, base + (len(lines) - 1) * 21 + 21 + i * 17, s, 13, 400, "middle", "#444")
    if num:
        text(x + 4, y - 5, num, 11, 600, "start", "#888")


def diamond(cx, cy, l1, l2="код · оценка · вы", w=190, h=92):
    pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
    out.append(f'<polygon points="{pts}" fill="#fff3cd" stroke="{AMBER}" stroke-width="1.8"/>')
    text(cx, cy - 1, l1, 15, 700, "middle")
    text(cx, cy + 17, l2, 12.5, 400, "middle", "#444")


def arrow(pts, back=False, color=None, dashed=False, head=True):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    c = color or (AMBER if back else "#333")
    dash = ' stroke-dasharray="7 5"' if (back or dashed) else ""
    mk = ""
    if head:
        mk = ' marker-end="url(#' + ("ahr" if c == AMBER else "ahg" if c == GREEN else "ah") + ')"'
    out.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="1.8"{dash}{mk}/>')


def label(x, y, s, back=False, anchor="start", size=12.5):
    text(x, y, s, size, 400, anchor, AMBER if back else "#333", italic=back)


def frame(t, h, num, title, note=None):
    out.append(
        f'<rect x="{FX}" y="{t}" width="{FW}" height="{h}" rx="16" fill="#f6f6f2" stroke="#555" stroke-width="1.8"/>'
    )
    out.append(f'<circle cx="100" cy="{t + 38}" r="21" fill="#fff" stroke="#555" stroke-width="1.8"/>')
    text(100, t + 46, str(num), 23, 700, "middle")
    text(138, t + 47, title, 24, 700)
    if note:
        text(FX + FW - 14, t + 26, note, 12, 400, "end", "#777", italic=True)


def band_section(t, h, name, chips):
    out.append(
        f'<rect x="{SX}" y="{t + 14}" width="{SW}" height="{h - 28}" rx="10" fill="#fff" '
        f'stroke="{GREEN}" stroke-width="1.6"/>'
    )
    text(SX + SW / 2, t + 36, name, 13.5, 700, "middle")
    for i, c in enumerate(chips):
        key = c.endswith("*")
        nm = c.rstrip(" *")
        fill, stroke, sw = ("#fff3cd", AMBER, 1.6) if key else ("#f6f6f2", "#999", 1.1)
        cy = t + 46 + i * 28
        out.append(
            f'<rect x="{SX + 8}" y="{cy}" width="{SW - 16}" height="22" rx="11" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
        )
        text(SX + SW / 2, cy + 15.5, nm, 12, 400, "middle")


# ------------------------------------------------------------------ макет
E = 206
hE = 372
heights = [590, 470, 440, 440, 360]
tops = []
y = E + hE + 70
for i, h in enumerate(heights):
    if i == 2:
        y += 30          # место под контейнер «волны»
    tops.append(y)
    y += h + 70
    if i == 3:
        y += 30
H = tops[-1] + heights[-1] + 36
bank_top = E
bank_bottom = tops[-1] + heights[-1] + 8

# ------------------------------------------------------------------ шапка
text(30, 36, "Студия: путь от идеи до работающего ПО — схема v3", 25, 700)
skeleton = [
    ("1 · Идея", "что строим"),
    ("2 · Сценарии, БД", "сценарии и данные"),
    ("3 · Frontend", "на моках"),
    ("4 · Backend", "реальный API"),
    ("5 · Анализ", "приёмка и правки"),
]
for i, (a, b) in enumerate(skeleton):
    x = 30 + i * 185
    box(x, 56, 160, 52, a, [b], tsize=16)
    if i < 4:
        arrow([(x + 160, 82), (x + 185, 82)])
arrow([(850, 108), (850, 128), (110, 128), (110, 108)], back=True)
label(480, 145, "следующая итерация — как «Запрос»", back=True, anchor="middle")

out.append(
    f'<polygon points="42,158 54,168 42,178 30,168" fill="#fff3cd" stroke="{AMBER}" stroke-width="1.5"/>'
)
text(62, 173, "ворота: код → независимая оценка → ваше решение", 13, 400, "start", "#444")
out.append(
    f'<rect x="402" y="159" width="26" height="18" rx="4" fill="#e3f1e3" stroke="{GREEN}" stroke-width="1.5"/>'
)
text(436, 173, "банк знаний", 13, 400, "start", "#444")
arrow([(552, 168), (586, 168)], back=True)
text(594, 173, "возврат на доработку", 13, 400, "start", "#444")
out.append(
    '<rect x="30" y="184" width="26" height="14" rx="4" fill="#fff" stroke="#333" stroke-width="1.5" stroke-dasharray="5 3"/>'
)
text(62, 196, "необязательный шаг", 13, 400, "start", "#444")
out.append(
    f'<rect x="212" y="184" width="26" height="14" rx="4" fill="none" stroke="{AMBER}" stroke-width="1.5" stroke-dasharray="5 3"/>'
)
text(246, 196, "волны (по желанию)", 13, 400, "start", "#444")
text(402, 196, "к банку: пунктир — «черновик», сплошная — «принято»", 13, 400, "start", "#444")

# ------------------------------------------------------------------ полоса банка
out.append(
    f'<rect x="785" y="{bank_top}" width="165" height="{bank_bottom - bank_top}" rx="14" fill="#eef7ee" '
    f'stroke="{GREEN}" stroke-width="2"/>'
)
text(793, E + 26, "БАНК ЗНАНИЙ", 13, 700, "start", GREEN)
for i, s in enumerate([
    "один на проект,",
    "раздел на каждый",
    "этап; хозяин раздела",
    "— его этап.",
    "Шаги пишут «черновик»,",
    "«принято» — только",
    "через ворота.",
    "Жёлтое — «Конституция»:",
    "входит в каждое ТЗ.",
]):
    text(793, E + 50 + i * 17, s, 12, 400, "start", "#444")

# ------------------------------------------------------------------ ВХОДЫ
frame_top = E
out.append(
    f'<rect x="{FX}" y="{frame_top}" width="{FW}" height="{hE}" rx="16" fill="#f6f6f2" stroke="#555" stroke-width="1.8"/>'
)
text(66, frame_top + 28, "Входы — система выбирает сама: есть ли код и есть ли банк", 16, 700)
r1 = frame_top + 48
box(COLS[0], r1, CW, 60, "Новая система", ["папка пуста, банка нет"])
box(COLS[1], r1, CW, 60, "Развитие", ["код есть, банка нет"])
box(COLS[2], r1, CW, 60, "Запрос", ["банк есть: правка или фича"])
r2 = r1 + 80
box(COLS[0], r2 + 5, CW, 70, "Полный путь", ["этапы 1–5, все ворота"])
arrow([(CXS[0], r1 + 60), (CXS[0], r2 + 5)])
box(COLS[1], r2 + 2, CW, 80, "Сканер + замысел", ["сканер: страницы, эндпоинты,", "модели, тесты; агент", "восстанавливает замысел"])
arrow([(CXS[1], r1 + 60), (CXS[1], r2 + 2)])
dcy = r2 + 40
diamond(CXS[2], dcy, "Мелкая правка?", "размер — по связям", h=96)
arrow([(CXS[2], r1 + 60), (CXS[2], dcy - 48)])
r3 = r2 + 124
arrow([(CXS[2], dcy + 48), (CXS[2], dcy + 62), (CXS[1], dcy + 62), (CXS[1], r3)])
arrow([(CXS[2], dcy + 62), (CXS[2], r3)])
label(CXS[1] + 8, dcy + 58, "да", anchor="start")
label(CXS[2] + 8, dcy + 58, "нет", anchor="start")
box(COLS[1], r3, CW, 88, "Быстрый путь", ["критерий → тест →", "реализация → ворота", "по области правки"])
box(COLS[2], r3, CW, 88, ["Полный путь", "с нужного этапа"], ["правит этап-владелец"])
label(66, frame_top + hE - 12, "«Развитие» после восстановления замысла дальше работает как «Запрос».", size=12.5)
arrow([(CXS[0], r2 + 75), (CXS[0], tops[0])])

# ------------------------------------------------------------------ ЭТАП 1
t = tops[0]
frame(t, heights[0], 1, "Идея")
A = t + 100
box(COLS[0], A, CW, 66, "Область", ["о чём система, для кого"], num="1.1")
box(COLS[1], A, CW, 66, "Уточняющие вопросы", ["закрытые, по одному"], num="1.2")
box(COLS[2], A, CW, 66, "Границы системы", ["что входит, что нет"], num="1.3")
arrow([(255, A + 33), (300, A + 33)])
arrow([(490, A + 33), (535, A + 33)])
arrow([(CXS[2], A), (CXS[2], t + 78), (CXS[1], t + 78), (CXS[1], A)], back=True)
label(CXS[1] + 14, t + 72, "по желанию: ещё вопросы", back=True)
bus = t + 196
B = t + 222
arrow([(CXS[2], A + 66), (CXS[2], B)])
arrow([(CXS[2], bus), (CXS[0], bus)], head=False)
arrow([(CXS[1], bus), (CXS[1], B)])
arrow([(CXS[0], bus), (CXS[0], B)])
label(66, bus - 6, "параллельно", size=12)
box(COLS[0], B, CW, 72, "Ограничения", ["чек-лист; значения", "по умолчанию уже есть"], num="1.4")
box(COLS[1], B, CW, 72, "Конкуренты", ["поиск → проверка → анализ", "1 раз, далее по желанию"], num="1.5")
box(COLS[2], B, CW, 72, "Предметная область", ["роли, сущности, процессы", "1 раз, далее по желанию"], num="1.6")
P = t + 330
for cx in CXS:
    arrow([(cx, B + 72), (cx, P)])
box(FX + 15, P, 660, 84, "Проблемы", [
    "на основе границ, предметной области и конкурентов",
    "+ уточняющие вопросы: ответы уточняют, расширяют или сужают границы",
], num="1.7")
arrow([(725, P + 30), (742, P + 30), (742, A + 33), (725, A + 33)], back=True)
G = t + 494
arrow([(CXS[0], P + 84), (CXS[0], G - 50)])
diamond(CXS[0], G, "Проверка проблем", w=180, h=100)
arrow([(70, G), (57, G), (57, t + 78), (CXS[1], t + 78)], back=True, head=False)
label(63, G - 56, "нет: ещё круг", back=True, size=12)
arrow([(250, G), (300, G)])
label(275, G - 7, "да", anchor="middle", size=12)
box(COLS[1], G - 43, CW, 86, ["Функциональные", "требования"], ["+ критерий приёмки,", "флаг «несколько способов»"], num="1.8")
arrow([(490, G), (535, G)])
diamond(CXS[2], G, "Приёмка требований", w=190, h=100)
arrow([(CXS[2], G - 50), (CXS[2], G - 60), (CXS[1], G - 60), (CXS[1], G - 43)], back=True)
label(CXS[2] + 10, G - 66, "нет", back=True, size=12)
arrow([(725, G), (SX, G)], color=GREEN)
arrow([(CXS[2], G + 50), (CXS[2], tops[1])], color=GREEN)
arrow([(FX + FW, t + 70), (SX, t + 70)], back=True, dashed=True)
band_section(t, heights[0], "Идея", ["Область", "Границы *", "Ограничения *", "Конкуренты", "Предметная область", "Проблемы", "Функц. требования", "Критерии приёмки"])

# ------------------------------------------------------------------ ЭТАП 2
t = tops[1]
frame(t, heights[1], 2, "Сценарии и схема БД", note="вход: только принятое из банка")
A = t + 90
box(COLS[0], A, CW, 70, "Роли и словарь", ["люди, системы, сущности", "единый список имён"], num="2.1")
box(COLS[1], A, CW, 70, "Варианты", ["только для требований", "с флагом"], kind="opt", num="2.2")
box(COLS[2], A, CW, 70, "Сценарии", ["цель, ход, ошибки,", "диаграммы, критерии"], num="2.3")
arrow([(255, A + 35), (300, A + 35)])
arrow([(490, A + 35), (535, A + 35)])
gy = t + 262
arrow([(CXS[2], A + 70), (CXS[2], gy - 46)])
diamond(CXS[2], gy, "Приёмка сценариев")
arrow([(725, gy), (742, gy), (742, A + 35), (725, A + 35)], back=True)
label(729, gy - 6, "нет", back=True, size=12)
arrow([(535, gy), (490, gy)])
label(512, gy - 6, "да", anchor="middle", size=12)
box(COLS[1], gy - 35, CW, 70, "Схема БД сценария", ["для каждого сценария,", "параллельно"], num="2.4")
arrow([(300, gy), (255, gy)])
box(COLS[0], gy - 35, CW, 70, "Слияние схем", ["в одну общую схему"], num="2.5")
g2 = t + 380
arrow([(CXS[0], gy + 35), (CXS[0], g2 - 46)])
diamond(CXS[0], g2, "Приёмка схемы БД")
arrow([(65 + 5, g2), (57, g2), (57, t + 196), (CXS[0], t + 196), (CXS[0], gy - 35)], back=True)
label(63, g2 - 52, "нет: доработка", back=True, size=12)
arrow([(255, g2), (SX, g2)], color=GREEN)
label(270, g2 - 7, "да", size=12)
arrow([(CXS[0], g2 + 46), (CXS[0], tops[2])], color=GREEN)
arrow([(FX + FW, t + 70), (SX, t + 70)], back=True, dashed=True)
band_section(t, heights[1], "Сценарии, БД", ["Словарь *", "Варианты", "Сценарии", "Общая схема БД", "Оценки"])

# ------------------------------------------------------------------ контейнер волн
c_top = tops[2] - 36
c_bot = tops[3] + heights[3] + 28
out.append(
    f'<rect x="38" y="{c_top}" width="734" height="{c_bot - c_top}" rx="18" fill="none" '
    f'stroke="{AMBER}" stroke-width="1.8" stroke-dasharray="8 5"/>'
)
text(56, c_top + 18, "ВОЛНЫ (по желанию): этапы 3–4 повторяются для каждого пакета сценариев; по умолчанию один пакет = всё", 12, 600, "start", AMBER)
text(758, c_bot - 8, "↻ следующий пакет", 12.5, 600, "end", AMBER)


def stage34(idx, num, title, steps, gate_title, note=None):
    t = tops[idx]
    frame(t, heights[idx], num, title, note=note)
    A = t + 90
    s = steps
    box(COLS[0], A, CW, 70, s[0][0], s[0][1], num=s[0][2], kind=s[0][3] if len(s[0]) > 3 else "step")
    box(COLS[1], A, CW, 70, s[1][0], s[1][1], num=s[1][2])
    box(COLS[2], A, CW, 70, s[2][0], s[2][1], num=s[2][2])
    arrow([(255, A + 35), (300, A + 35)])
    arrow([(490, A + 35), (535, A + 35)])
    arrow([(CXS[2], A + 70), (CXS[2], t + 210)])
    B = t + 210
    box(COLS[2], B, CW, 70, s[3][0], s[3][1], num=s[3][2])
    box(COLS[1], B, CW, 70, s[4][0], s[4][1], num=s[4][2])
    box(COLS[0], B, CW, 70, s[5][0], s[5][1], num=s[5][2])
    arrow([(535, B + 35), (490, B + 35)])
    arrow([(300, B + 35), (255, B + 35)])
    gy = t + 352
    arrow([(CXS[0], B + 70), (CXS[0], gy - 46)])
    diamond(CXS[0], gy, gate_title)
    arrow([(70, gy), (57, gy), (57, t + 185), (CXS[0], t + 185), (CXS[0], B)], back=True)
    label(63, gy - 52, "нет: доработка", back=True, size=12)
    arrow([(255, gy), (SX, gy)], color=GREEN)
    label(270, gy - 7, "да", size=12)
    if idx < 4:
        arrow([(CXS[0], gy + 46), (CXS[0], tops[idx + 1])], color=GREEN)
    arrow([(FX + FW, t + 70), (SX, t + 70)], back=True, dashed=True)


stage34(2, 3, "Frontend на моках", [
    ("Стиль и каркас", ["один раз; тема, меню,", "каркас; есть по умолчанию"], "3.1"),
    ("План интерфейса", ["страницы по сценариям"], "3.2"),
    ("Моки и контракт", ["данные и вызовы API"], "3.3"),
    ("ТЗ из банка", ["собирает код по связям"], "3.4"),
    ("Тесты из критериев", ["другой агент; красный старт"], "3.5"),
    ("Реализация", ["исполнитель; тесты", "не правит"], "3.6"),
], "Приёмка интерфейса", note="вход: только принятое из банка")
band_section(tops[2], heights[2], "Frontend", ["Стиль и каркас", "Контракт API", "Моки", "ТЗ", "Тесты", "Оценки"])

stage34(3, 4, "Backend", [
    ("План бэкенда", ["эндпоинты и сценарии"], "4.1"),
    ("Модели из схемы БД", ["генерирует код, не агент"], "4.2"),
    ("ТЗ из банка", ["собирает код по связям"], "4.3"),
    ("Тесты из критериев", ["другой агент; красный старт"], "4.4"),
    ("Реализация", ["исполнитель; тесты", "не правит"], "4.5"),
    ("Замена моков", ["фронтенд → реальный API"], "4.6"),
], "Приёмка бэкенда", note="вход: только принятое из банка")
band_section(tops[3], heights[3], "Backend", ["Эндпоинты", "Модели", "ТЗ", "Тесты", "Оценки"])

# ------------------------------------------------------------------ ЭТАП 5
t = tops[4]
frame(t, heights[4], 5, "Общий анализ и правки", note="вход: только принятое из банка")
box(COLS[0], t + 90, CW, 70, "Запуск", ["с демо-данными", "по сценариям"], num="5.1")
arrow([(255, t + 125), (295, t + 125)])
diamond(CXS[1], t + 125, "Итоговая приёмка", w=200, h=100)
arrow([(495, t + 125), (535, t + 125)])
label(515, t + 117, "да", anchor="middle", size=12)
box(COLS[2], t + 80, CW, 90, "Итог итерации", ["Документация.md, схема,", "карты, контракт, отчёт,", "точка отката"])
arrow([(725, t + 125), (SX, t + 125)], color=GREEN)
box(COLS[0], t + 240, CW, 86, "Замечания", ["каждое — запрос на", "изменение: правит", "этап-владелец"])
arrow([(CXS[1], t + 175), (CXS[1], t + 283), (255, t + 283)], back=True)
label(CXS[1] + 8, t + 215, "нет", back=True, size=12)
arrow([(CXS[0], t + 240), (CXS[0], t + 160)], back=True)
label(CXS[0] + 10, t + 205, "после правок — заново", back=True, size=12)
mids = [tops[i] + heights[i] // 2 for i in range(4)]
arrow([(65, t + 283), (28, t + 283), (28, mids[0])], back=True, head=False)
for m in mids:
    arrow([(28, m), (50, m)], back=True)
arrow([(FX + FW, t + 70), (SX, t + 70)], back=True, dashed=True)
band_section(t, heights[4], "Анализ", ["Замечания", "Итог итерации", "Отчёт"])

# ------------------------------------------------------------------ РОЛИ АГЕНТОВ (отдельный блок)
ry = tops[-1] + heights[-1] + 80
text(30, ry, "Роли агентов — отдельный список исполнителей (не часть потока)", 22, 700)
text(30, ry + 26, "Модель для каждой роли выбирается в Настройках студии → Агенты. Рекомендация — подсказка, а не ограничение.", 13.5, 400, "start", "#444")
text(30, ry + 46, "Поддерживаются агенты: Claude Code Pro и OpenCode Go Plus.", 13.5, 700, "start", "#222")
TIER = {3: ("★★★", "топ — самая сильная"), 2: ("★★", "сильная — рабочая"), 1: ("★", "быстрая — дешёвая")}
roles = [
    ("Координатор", "все этапы", ["ведёт этап по режиму участия,", "раздаёт задачи, считает круги,", "маршрутизирует запросы"], 3, "нужна надёжность рассуждений"),
    ("Независимый оценщик", "слой 2 всех ворот", ["оценивает по критериям ворот", "с доказательствами; всегда", "другая модель, чем автор"], 3, "строгий и непредвзятый"),
    ("Аналитик", "этап 1", ["вопросы, границы, проблемы,", "функц. требования", "и критерии приёмки"], 3, "качество формулировок"),
    ("Исследователь", "этап 1", ["конкуренты и предметная", "область; несколько штук", "параллельно, веб-поиск"], 2, "факты проверяются потом"),
    ("Аналитик сценариев", "этап 2", ["словарь, варианты, сценарии,", "диаграммы и критерии", "сценария"], 3, "ошибка расходится на три этапа"),
    ("Проектировщик БД", "этап 2", ["схема БД на сценарий", "и слияние в общую схему"], 3, "согласованность и стандарты"),
    ("Дизайнер интерфейса", "этап 3", ["стиль и каркас, план страниц,", "моки и контракт API"], 2, "вкус и аккуратность"),
    ("Проектировщик бэкенда", "этап 4", ["план эндпоинтов, замена", "моков на реальный API"], 2, "хватает сильной модели"),
    ("Автор тестов", "этапы 3–4", ["тесты из критериев до", "реализации; кода не видит;", "не тот, кто исполнитель"], 2, "точность важнее скорости"),
    ("Исполнитель", "этапы 3–4", ["пишет код по ТЗ; тесты", "не правит; параллельно", "по сценариям"], 3, "главная роль по качеству кода"),
    ("Помощник", "этап 5 и итоги", ["демо-данные по сценариям,", "Документация.md, отчёт"], 1, "текст и рутина"),
]
cw, chh, gap = 215, 158, 13
gy0 = ry + 66
for i, role in enumerate(roles + [None]):
    cx0 = 30 + (i % 4) * (cw + gap)
    cy0 = gy0 + (i // 4) * (chh + 12)
    if role is None:
        out.append(
            f'<rect x="{cx0}" y="{cy0}" width="{cw}" height="{chh}" rx="12" fill="#fff" stroke="#888" '
            f'stroke-width="1.6" stroke-dasharray="6 4"/>'
        )
        text(cx0 + 12, cy0 + 26, "Это не агенты, а код", 15, 700, "start", "#555")
        for k, ln in enumerate([
            "сканер кода", "проверки кодом (слой 1 ворот)", "ТЗ из банка", "модели из схемы БД",
            "карты и связи", "запуск, откат, git-точки",
        ]):
            text(cx0 + 12, cy0 + 52 + k * 19, "• " + ln, 12.5, 400, "start", "#555")
        continue
    nm, where, desc, tier, why = role
    out.append(
        f'<rect x="{cx0}" y="{cy0}" width="{cw}" height="{chh}" rx="12" fill="#f6f6f2" stroke="#555" stroke-width="1.6"/>'
    )
    text(cx0 + 12, cy0 + 24, nm, 15, 700)
    text(cx0 + 12, cy0 + 41, where, 11.5, 400, "start", "#777", italic=True)
    for k, ln in enumerate(desc):
        text(cx0 + 12, cy0 + 62 + k * 15, ln, 12.5, 400, "start", "#333")
    out.append(f'<line x1="{cx0 + 12}" y1="{cy0 + 106}" x2="{cx0 + cw - 12}" y2="{cy0 + 106}" stroke="#ccc" stroke-width="1"/>')
    stars, lab = TIER[tier]
    text(cx0 + 12, cy0 + 128, stars, 16, 700, "start", AMBER)
    text(cx0 + 12 + 14 * tier + 8, cy0 + 128, lab, 12.5, 700, "start", "#333")
    text(cx0 + 12, cy0 + 147, why, 11.5, 400, "start", "#666", italic=True)
ly = gy0 + 3 * (chh + 12) + 6
text(30, ly, "Правила: независимый оценщик — всегда другая модель, чем автор; автор тестов — не тот, кто исполнитель.", 13.5, 700, "start", AMBER)
H = ly + 26

# ------------------------------------------------------------------ вывод
defs = (
    "<defs>"
    '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
    'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker>'
    '<marker id="ahr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
    f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{AMBER}"/></marker>'
    '<marker id="ahg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
    f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{GREEN}"/></marker>'
    "</defs>"
)
page = (
    '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>Схема студии v3</title>"
    "<style>html,body{margin:0;background:#fff}"
    "svg{display:block;width:100%;max-width:1100px;margin:0 auto;"
    "font-family:'Segoe UI',system-ui,Arial,sans-serif}</style></head><body>"
    f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{defs}'
    + "".join(out)
    + "</svg></body></html>"
)
svg_doc = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
    "font-family=\"Segoe UI, Arial, sans-serif\">"
    f'<rect width="{W}" height="{H}" fill="#ffffff"/>{defs}' + "".join(out) + "</svg>"
)
pathlib.Path(__file__).with_name("v3.svg").write_text(svg_doc, encoding="utf-8")
target = pathlib.Path(__file__).with_name("v3.html")
target.write_text(page, encoding="utf-8")
print("ok", target, "H=", H, "tops=", tops)
