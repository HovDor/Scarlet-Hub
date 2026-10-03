import csv
import os
import threading
import random
import time
import ctypes
from ctypes import wintypes
import numpy as np
import mss
import pyautogui
from pynput import keyboard

VERSION = "1.0"
APP_NAME = "Scarlet hub"
LANG = "en"                # настоящее значение читается ниже, через _cfg
# до 7.3 программа называлась "Клёв", и до 8.6 её файлы - klev.ini и
# klev.log. При первом запуске 8.6 они сами переименовываются в
# scarlet.ini и scarlet.log (см. _load_ini).

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0        # ВАЖНО: по умолчанию 0.1с паузы после КАЖДОГО вызова.
                          # С ШИМ это резало частоту в разы.


# ---------------------------------------------------------------
# Настройки читаются из scarlet.ini рядом с программой (если он есть).
# Так друзьям не нужно лезть в код - правят обычный текстовый файл.
# ---------------------------------------------------------------
def _load_ini():
    import configparser
    import sys
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
    path = os.path.join(base, "scarlet.ini")
    # 8.6: klev.ini/klev.log -> scarlet.ini/scarlet.log. Переименовываем,
    # только если нового файла ещё нет: настройки и удочки сохраняются.
    for old, new in (("klev.ini", "scarlet.ini"), ("klev.log", "scarlet.log")):
        po, pn = os.path.join(base, old), os.path.join(base, new)
        if os.path.exists(po) and not os.path.exists(pn):
            try:
                os.replace(po, pn)
            except OSError:
                pass
    if not os.path.exists(path):
        return base, {}
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8-sig")
    out = {}
    for sect in cp.sections():
        # Секции профилей в общие настройки НЕ идут. Раньше читались
        # все секции подряд, и последняя по порядку побеждала: при
        # пустом PROFILE значение LEAD молча бралось из [профиль:tryhard],
        # если тот стоял в конце файла. Профиль применяется отдельно,
        # только когда он выбран.
        low = sect.lower()
        if low.startswith("профиль:") or low.startswith("profile:"):
            continue
        for k, v in cp[sect].items():
            out[k.upper()] = v
    return base, out


APP_DIR, _INI = _load_ini()


PROFILE = (_INI.get("PROFILE") or "").split("#")[0].strip().lower()


def _load_profile():
    """Секция [профиль:имя] в scarlet.ini переопределяет общие настройки."""
    if not PROFILE:
        return {}
    import configparser
    path = os.path.join(APP_DIR, "scarlet.ini")
    if not os.path.exists(path):
        return {}
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8-sig")
    for sect in cp.sections():
        if sect.strip().lower() in (PROFILE, "профиль:" + PROFILE,
                                    "profile:" + PROFILE):
            return {k.upper(): v for k, v in cp[sect].items()}
    return {}


_PROF = _load_profile()


_REG = {}               # все настройки, какие программа вообще читает
_OVERRIDE = {}          # те, что scarlet.ini перебил относительно кода


def _parse_val(name, raw, default):
    """Строка из scarlet.ini -> значение того же типа, что и в коде."""
    if raw is None:
        return default
    # комментарий в строке - только после пробела: "0.3  # пояснение".
    # Раньше резалось всё после первой "#", и цвет "#434b5b" становился
    # пустой строкой.
    import re
    raw = re.split(r"\s[#;]", raw.strip(), maxsplit=1)[0].strip()
    try:
        if isinstance(default, bool):
            return raw.lower() in ("1", "true", "да", "yes", "on")
        if isinstance(default, int):
            return int(float(raw))
        if isinstance(default, float):
            return float(raw.replace(",", "."))
        return raw
    except ValueError:
        print(f"  scarlet.ini: не понял значение {name} = {raw}, беру {default}")
        return default


def _cfg(name, default):
    # Раньше настройки только ЧИТАЛИСЬ. Если ключа не было в scarlet.ini,
    # молча брался запасной вариант из кода - и новая настройка в файле
    # так и не появлялась, найти её поиском было невозможно.
    # Теперь каждая прочитанная настройка попадает в список, и при
    # запуске недостающие дописываются в файл.
    _REG[name] = default
    raw = _PROF.get(name, _INI.get(name))
    if raw is None:
        return default
    val = _parse_val(name, raw, default)
    # Значение из файла ПЕРЕБИВАЕТ то, что записано в коде. Из-за этого
    # рассчитанный GAIN однажды не применился: в scarlet.ini лежало старое
    # 0.0100, дописанное туда автоматически, и человек об этом не знал.
    # Теперь о каждом таком случае говорим вслух при запуске.
    if val != default:
        _OVERRIDE[name] = (default, val)
    return val


# Зона полосы миниигры ("Fish box"). Под 1920x1080 - 500,872 920x88; на
# другом разрешении её ставят сами (Main -> Change area или F2).
REGION = {"left": _cfg("FISH_LEFT", 500), "top": _cfg("FISH_TOP", 872),
          "width": _cfg("FISH_WIDTH", 920), "height": _cfg("FISH_HEIGHT", 88)}
FISH_LEFT, FISH_TOP = REGION["left"], REGION["top"]
FISH_WIDTH, FISH_HEIGHT = REGION["width"], REGION["height"]
# Полоса прогресса - та, что под полосой ловли. По ней видно, поймана
# рыба или сорвалась: на успехе она заполняется, на срыве пустеет.
# Замерено на снимках 1920x1080: строки 969-979, полоса x 745..1173.
# Карточка поимки: картинка рыбы с сиянием. Замер по двум снимкам
# (поймана / сорвалась) с одного места: картинка занимает строки
# 700-900, но в 835-890 лежат НАДПИСИ - стрик, чужие поимки, ивенты.
# Строки 745-825 покрыты картинкой на 91-100% и свободны от любого
# текста. Сравниваем с тем, как зона выглядела ДО поклёвки, а не с
# цветом: рыбы разного цвета, сияние зависит от редкости.
CARD = {"left": _cfg("CARD_LEFT", 860), "top": _cfg("CARD_TOP", 745),
        "width": _cfg("CARD_WIDTH", 200), "height": _cfg("CARD_HEIGHT", 80)}
CARD_DIFF = 60        # насколько пиксель должен измениться
CARD_FRAC = _cfg("CARD_FRAC", 0.35)   # какая доля зоны должна измениться
CARD_WAIT = _cfg("CARD_WAIT", 2.5)    # сколько ещё ждать карточку после
                       # конца миниигры (время вычитается из AFTER_CATCH)
PROGRESS = {"left": _cfg("PROG_LEFT", 720), "top": _cfg("PROG_TOP", 963),
            "width": _cfg("PROG_WIDTH", 480), "height": _cfg("PROG_HEIGHT", 24)}
# XP над экраном (7.4): при поимке сверху появляются уровень крупными
# кремовыми цифрами, полоска опыта над ним и "+NNNxp". Замер по четырём
# поимкам: кремовых пикселей в зоне 14-16%; без поимки (stuck.png) - 0%.
# Опыт даёт только поимка, поэтому это самый прямой признак исхода.
XP_ZONE = {"left": _cfg("XP_LEFT", 825), "top": _cfg("XP_TOP", 185),
           "width": _cfg("XP_WIDTH", 235), "height": _cfg("XP_HEIGHT", 105)}
XP_FRAC = _cfg("XP_FRAC", 0.025)   # столько кремового должно ПРИБАВИТЬСЯ
XP_LEFT, XP_TOP = XP_ZONE["left"], XP_ZONE["top"]
XP_WIDTH, XP_HEIGHT = XP_ZONE["width"], XP_ZONE["height"]
# Те же числа отдельными именами: окно настроек правит их на лету,
# а словари выше пересобираются из них (_apply_derived).
CARD_LEFT, CARD_TOP = CARD["left"], CARD["top"]
CARD_WIDTH, CARD_HEIGHT = CARD["width"], CARD["height"]
PROG_LEFT, PROG_TOP = PROGRESS["left"], PROGRESS["top"]
PROG_WIDTH, PROG_HEIGHT = PROGRESS["width"], PROGRESS["height"]

# --- детекция ---
FLASH_JUMP = 26        # скачок средней яркости кадра = вспышка (cryogenic)
FLASH_HOLD = 0.45      # столько после вспышки не доверяем детекции:
                       # честное "не вижу" безопаснее ложной позиции
FISH_MIN_W, FISH_MAX_W = 3, 24
SAVE_BITES = 2         # сохранить столько картинок области при поклёвке
SAVE_FAILS = 4         # сохранить столько кадров, ГДЕ РЫБА ПОТЕРЯЛАСЬ
SAVE_AFTER = _cfg("SAVE_AFTER", 4)   # снимков ВСЕГО ЭКРАНА после миниигры:
                       # по ним ищем карточку "You just caught a ..."

# --- регулятор ---
BASE_NEUTRAL = _cfg("BASE_NEUTRAL", 0.50)   # скважность, при которой блок стоит на месте.
                       # Если скрипт подскажет другое значение - поставь его.
# Калибровка 3.7 дала настоящие числа объекта:
#   усиление K = 1262 px/с на единицу скважности
#   запаздывание L = 249 мс
# Блок - интегратор с запаздыванием. Для него пропорциональный
# регулятор устойчив, пока K*Kp*L < pi/2, то есть Kp < 0.0050.
# Прежний GAIN = 0.0100 был РОВНО ВДВОЕ выше предела устойчивости -
# отсюда и раскачка, и скважность, упёртая в упор.
# Перебор по девяти записанным траекториям рыбы даёт оптимум 0.0012:
# рыба внутри блока 57% времени против 43% на прежних настройках.
# Сравнение на воде, по 36 рыб на вариант в одной сессии:
#   GAIN 0.0012 -> 10.8 с на рыбу, ошибка 66
#   GAIN 0.0035 ->  8.0 с на рыбу, ошибка 42   (быстрее на 26%)
# Моя симуляция ставила на 0.0012 и ошиблась: модель блока оказалась
# грубее, чем я думал, и занижала возможности высокого усиления.
# Верим воде, а не модели. 0.0035 - это 70% от предела устойчивости.
GAIN = _cfg("GAIN", 0.0035)
# Сравнивать настройки "на глаз" между сессиями бесполезно: рыбы
# разные, и четыре штуки ничего не доказывают. Впиши сюда через
# запятую два-три значения GAIN - макрос будет чередовать их от рыбы
# к рыбе внутри ОДНОЙ сессии и сам покажет сводку по каждому.
# Пример: AB_TEST = 0.0012, 0.0035
AB_TEST = _cfg("AB_TEST", "")
AB_VALUES = [float(x) for x in AB_TEST.replace(";", ",").split(",")
             if x.strip()] if AB_TEST else []
AB_STATS = {}
DAMP = _cfg("DAMP", 0.030)
LEAD = _cfg("LEAD", 0.30)   # упреждение: целимся туда, где рыба БУДЕТ
# Блок физически медленнее рыбы. Замер по записи: на полном газу блок
# разгоняется до ~200 px/с вправо и ~300 влево, а трудная рыба идёт
# 554 px/с на трёх четвертях времени и 1500 на десятой части. Гнаться
# за каждым её рывком нельзя - скважность и так упиралась в упор 70%
# времени. Поэтому целимся в СГЛАЖЕННУЮ позицию рыбы.
# Постоянные подобраны перебором по записи. Критерий: цель должна
# двигаться так, чтобы блок успевал (90% времени не быстрее ~400 px/с),
# и при этом отставать от рыбы как можно меньше.
#   tau 0.45 доля 0.30 -> отставание 57 px, скорость 161/385  посильно
#   tau 0.35 доля 0.00 -> отставание 56 px, скорость 156/376  посильно
#   tau 0.30 доля 1.00 -> отставание 27 px, скорость 244/742  НЕ посильно
# Полная компенсация задержки фильтра заманчива (27 px), но возвращает
# в цель всю скорость рыбы - то есть ровно то, из-за чего скважность и
# упиралась в упор. Берём частичную.
# Сглаживание цели я вводил, чтобы регулятор не гнался за рывками
# рыбы. Теперь понятно, что гонка была следствием завышенного
# усиления, а не самостоятельной бедой. С верным GAIN сглаживание
# только добавляет задержки к и без того запаздывающему объекту:
# в переборе выключенное сглаживание выигрывает 57% против 53.6%.
AIM_TAU = _cfg("AIM_TAU", 0.0)     # >0 - целиться в сглаженный ход рыбы
AIM_LEAD = _cfg("AIM_LEAD", 0.30)  # доля компенсации задержки фильтра
PERIOD = _cfg("PERIOD", 0.075)   # период ШИМ: ~13 нажатий/сек (было 0.040 = 25/сек)
NONLIN = 220
MAX_SPEED = 1200
ACT_SCALE = 400.0
ADAPT_MAX = 1.5        # потолок адаптации, иначе разгоняется
TRIM_LIMIT = 0.10      # нейтраль проверена вручную: 0.5 верна

# Всё время - в СЕКУНДАХ, не в кадрах. Иначе поведение меняется вместе с FPS.
DUTY_TAU = 0.50        # за сколько скважность возвращается к нейтрали
SPEED_TAU = 0.08       # сглаживание скорости сближения
ACT_TAU = 1.00         # сглаживание оценки активности
TRIM_TAU = 20.0        # утечка автоподстройки нейтрали
TRIM_GAIN = 0.00007    # скорость автоподстройки (на секунду)
MAX_JUMP = 60
JUMP_SEC = 0.15        # столько держимся за старую позицию при скачке
GHOST_SEC = 0.60       # сколько едем по инерции, потеряв рыбу
GHOST_DECAY = 0.5
GHOST_CLAMP = 0.30     # без видимой рыбы не уходим от нейтрали дальше этого:
                       # иначе блок улетает в стену по одной лишь догадке

# --- автозаброс ---
CHARGE_TIME = _cfg("CHARGE_TIME", 0.90)
BITE_TIMEOUT = _cfg("BITE_TIMEOUT", 35.0)
# ------------------------------------------------------------------
#  SHAKE (7.9) - второй из трёх этапов ловли: заброс -> shake -> бой.
#  После заброса на экране появляются кнопки Shake; чем быстрее их
#  нажимать, тем раньше клюёт. Четыре способа:
#    pixel      - ищем БЕЛЫЙ пиксель кнопки в зоне поиска и щёлкаем по
#                 ближайшему к центру;
#    navigation - включаем навигацию интерфейса Roblox (клавиша NAV_KEY),
#                 и выбранную ею кнопку жмём клавишей NAV_PRESS;
#    circle     - ищем на экране КРУГИ кнопок shake (кольцо светлых
#                 пикселей) и щёлкаем в их центр;
#    disabled   - ничего не жмём, ждём, пока рыба клюнет сама.
# ------------------------------------------------------------------
SHAKE_MODES = ["pixel", "navigation", "circle", "disabled"]
SHAKE_MODE = _cfg("SHAKE_MODE", "navigation")
SHAKE_INTERVAL = _cfg("SHAKE_INTERVAL", 0.45)   # navigation: как часто жать
SHAKE_CLICKS = _cfg("SHAKE_CLICKS", 1)          # щелчков по одной кнопке
SHAKE_TOL = _cfg("SHAKE_TOL", 12)               # допуск белого: 0 - только #ffffff
SHAKE_DIST = _cfg("SHAKE_DIST", 10)             # ближе стольких px - та же кнопка
SHAKE_FPS = _cfg("SHAKE_FPS", 60)               # сколько раз в секунду искать
SHAKE_DUP = _cfg("SHAKE_DUP", 1.0)              # сколько помнить нажатую кнопку, с
SHAKE_FAIL = _cfg("SHAKE_FAIL", 3.0)            # нет shake столько с - заброс не удался
NAV_KEY = _cfg("NAV_KEY", "\\")                 # включить навигацию интерфейса
NAV_PRESS = _cfg("NAV_PRESS", "enter")          # чем жать выбранную кнопку
NAV_AUTO = _cfg("NAV_AUTO", False)              # жать NAV_KEY на старте ловли
# Зона, где ищем кнопки: середина экрана. Край экрана (окно статуса,
# XP сверху, полоса миниигры снизу) в неё не входит - иначе макрос
# щёлкал бы по белому тексту собственного окна.
# 8.6.2: почти весь экран. Кнопки shake в Fisch появляются где угодно, в
# том числе у краёв; в узкой зоне макрос нажимал первую-вторую и "терял"
# остальные (журнал: после смены зоны на весь экран - 15-29 нажатий).
# Не входят: верхняя полоса 60 px (значки Roblox) и низ ниже 1000 (панель
# предметов). Окно статуса вырезается отдельно, где бы оно ни стояло.
SHAKE_ZONE = {"left": _cfg("SHAKE_LEFT", 40), "top": _cfg("SHAKE_TOP", 60),
              "width": _cfg("SHAKE_WIDTH", 1840), "height": _cfg("SHAKE_HEIGHT", 940)}
SHAKE_VERIFY = _cfg("SHAKE_VERIFY", True)   # pixel: белое только внутри кольца кнопки
# Окна самого макроса, которые могут попасть в зону (окно статуса): их
# прямоугольники выставляет окно, поиск shake их не видит.
SKIP_RECTS = []
SHAKE_LEFT, SHAKE_TOP = SHAKE_ZONE["left"], SHAKE_ZONE["top"]
SHAKE_WIDTH, SHAKE_HEIGHT = SHAKE_ZONE["width"], SHAKE_ZONE["height"]
SHAKE_OLD = {"enter": "navigation", "none": "disabled"}   # значения до 7.9
# ------------------------------------------------------------------
#  ЧАСТОТА ДЕТЕКТОРА ПО ЭТАПАМ (8.0). Чем чаще смотрим на экран, тем
#  точнее, но тем больше грузим процессор. Каждый этап - своя частота:
#    CAST_FPS  - как часто проверять, не началась ли миниигра (поклёвка);
#    SHAKE_FPS - как часто искать кнопки shake (выше);
#    FISH_FPS  - кадров в секунду в самом бою; 0 - сколько потянет ПК.
#  Бой важнее всего: ниже 30 блок начинает запаздывать за рыбой.
# ------------------------------------------------------------------
CAST_FPS = _cfg("CAST_FPS", 60)
FISH_FPS = _cfg("FISH_FPS", 0)
PERF_PRESETS = {"low": (20, 30, 30), "medium": (30, 60, 60), "high": (60, 120, 0)}
KEY_HOLD = 0.06        # держим клавишу, иначе игра не заметит нажатие
FIRST_PERSON = _cfg("FIRST_PERSON", True)  # завести камеру внутрь персонажа
FP_SCROLLS = _cfg("FP_SCROLLS", 18)        # сколько щелчков колеса вниз
# ------------------------------------------------------------------
#  ЗАБРОС (8.1) - два способа, у каждого своя ПОСЛЕДОВАТЕЛЬНОСТЬ шагов:
#    normal  - просто: подождать, зажать ЛКМ, подержать, отпустить;
#    perfect - поставить камеру, зажать ЛКМ и отпустить, когда белая
#              заливка полоски заброса дойдёт до ЗЕЛЁНОЙ зоны наверху.
#  Шаги (строка в scarlet.ini, через ";"):
#    delay:сек  hold  release  zoom_out:щелчков  zoom_in:щелчков
#    look_down:px  look_up:px  perfect  (отпустить по полоске заброса)
# ------------------------------------------------------------------
CAST_STYLE = _cfg("CAST_STYLE", "normal")
# Перед каждым забросом: взять рюкзак, потом снова удочку. Fisch при
# долгой ловле подвисает - удочка "застревает", и заброс не проходит;
# переэкипировка это сбрасывает. Шаги: delay:сек  bag  rod
AUTO_ROD = _cfg("AUTO_ROD", True)
ROD_SEQ = _cfg("ROD_SEQ", "delay:1.25;bag;delay:0.5;rod;delay:0")
ROD_KEY = _cfg("ROD_KEY", "1")
BAG_KEY = _cfg("BAG_KEY", "2")
CAST_SEQ_NORMAL = _cfg("CAST_SEQ_NORMAL",
                       f"delay:0.35;hold;delay:{CHARGE_TIME:g};release;delay:0.3")
CAST_SEQ_PERFECT = _cfg("CAST_SEQ_PERFECT",
                        "zoom_out:13;zoom_in:6;look_down:2000;delay:0.2;look_up:900;"
                        "delay:0.2;hold;perfect;delay:0.2;zoom_in:5;look_up:2000;delay:0.2")
# Полоска заброса (замер по снимку): высота ~290 px, ширина ~20; сверху
# зелёная зона ~10 px (G 105-170, R 60-96, B 52-74), под ней тёмная
# пустая часть, снизу поднимается белая заливка (251,250,239).
# green    - (по умолчанию) целимся в ЗЕЛЁНУЮ зону: при нынешней скорости
#            заливки отпускаем за "задержку" до неё; задержку макрос сам
#            подстраивает после каждого заброса, глядя, где заливка встала;
# velocity - по заданным процентам (старый способ, для ручной настройки)
PC_STYLE = _cfg("PC_STYLE", "green")
PC_GREEN_TOL = _cfg("PC_GREEN_TOL", 0)          # послабление зелёного
PC_WHITE_TOL = _cfg("PC_WHITE_TOL", 0)          # послабление белого
PC_FAIL = _cfg("PC_FAIL", 3.0)                  # полоску не нашли за столько - отпускаем
PC_FPS = _cfg("PC_FPS", 200)                    # как часто смотреть на полоску
PC_EARLY_MS = _cfg("PC_EARLY_MS", 40)           # отпускать раньше касания зелёного, мс (учится)
PC_AUTOLAT = _cfg("PC_AUTOLAT", True)           # подстраивать задержку по итогам
# velocity: отпускать на таком % пути до зелёного - по скорости заливки
PC_B400, PC_B600, PC_B800 = _cfg("PC_B400", 95), _cfg("PC_B600", 94), _cfg("PC_B800", 92)
PC_B1000, PC_B1200, PC_BMAX = _cfg("PC_B1000", 91), _cfg("PC_B1200", 89), _cfg("PC_BMAX", 88)
PC_BANDS = [(400, PC_B400), (600, PC_B600), (800, PC_B800), (1000, PC_B1000),
            (1200, PC_B1200), (10 ** 9, PC_BMAX)]
PC_BFALL = _cfg("PC_BFALL", 85)                 # скорость ещё не измерена
CAST_ZONE = {"left": _cfg("CAST_LEFT", 560), "top": _cfg("CAST_TOP", 120),
             "width": _cfg("CAST_WIDTH", 800), "height": _cfg("CAST_HEIGHT", 800)}
CAST_LEFT, CAST_TOP = CAST_ZONE["left"], CAST_ZONE["top"]
CAST_WIDTH, CAST_HEIGHT = CAST_ZONE["width"], CAST_ZONE["height"]
CAST_GRACE = _cfg("CAST_GRACE", 3.0)   # не кликаем, пока удочка летит - иначе смотаем леску
AFTER_CATCH = _cfg("AFTER_CATCH", 1.6)
BAR_CONFIRM = 4        # кадров подряд с блоком И рыбой
LOST_SEC = 1.5         # блока нет столько секунд -> миниигра кончилась
NOFISH_SEC = 2.0       # блок есть, а рыбы нет столько -> это не миниигра
MAX_CATCHES = 0        # 0 = без ограничения
MAX_TIMEOUTS = _cfg("MAX_TIMEOUTS", 4)   # пустых забросов подряд -> стоп
MIN_CATCH_SEC = 2.5    # короче - считаем ложной поклёвкой, не пишем в CSV
MIN_CATCH_SEEN = 45    # и видимость рыбы должна быть не ниже, %

CSV_PATH = os.path.join(APP_DIR, "catches.csv")
SHOW_VIEW = _cfg("SHOW_VIEW", True)    # живая схема полосы в консоли
USE_GUI = _cfg("USE_GUI", True)        # окно с трекером
CONF_WIN = 1.0                         # окно оценки уверенности, секунд
CONF_FLOOR = 0.35                      # ниже этого не душим управление совсем
# Режим отладки (8.6). Выключен - макрос НЕ пишет рядом с собой снимки и
# trace.csv: обычному пользователю это только мусор в папке. Включают,
# когда присылают баг. Снимки по клавишам (F7, F3) и "Проверить shake"
# работают всегда - их просят явно. catches.csv и error.log - тоже всегда.
DEBUG_MODE = _cfg("DEBUG_MODE", False)
TRACE_CATCHES = _cfg("TRACE_CATCHES", 8)   # записать траекторию стольких рыб
TRACE_ALL = _cfg("TRACE_ALL", True)   # писать трассу и у СОРВАВШИХСЯ попыток:
                       # иначе при полном сбое детекции файл вообще не создаётся
TRACE_PATH = os.path.join(APP_DIR, "trace.csv")
VIEW_W = 62                            # ширина схемы в символах
VIEW_HZ = 10.0                         # раз в секунду перерисовывать
DEBUG = True

running = False
should_exit = False
holding = False
hold_changes = 0
hold_error = None
diagnose = False
pwm_on = False         # ШИМ работает только во время вываживания
snap_pending = False   # F7: сохранить кадр области
burst_pending = False  # F3: записать СЕРИЮ кадров области
burst = None           # [(t, кадр), ...] пока идёт запись
bursts = 0
burst_t0 = 0.0
burst_n = 0
BURST_SEC = _cfg("BURST_SEC", 4.0)    # длина серии
BURST_EVERY = _cfg("BURST_EVERY", 3)  # каждый какой кадр писать
snaps = 0
calib_pending = False  # F4: снять характеристику блока
calib_t0 = 0.0
calib_ready = 0
calib_hold0 = 0
calib_rows = []

# Разомкнутая проверка блока. Регулятор на это время отключается и
# кнопка жмётся по расписанию, не глядя на рыбу. Иначе объект не
# опознать: в замкнутом контуре скважность зависит от положения блока,
# и корреляция входа с выходом ничего не говорит о физике.
# Первая калибровка показала две вещи. Блок стоит ровно при
# скважности 0.50 (40 кадров, 0 px/с) - BASE_NEUTRAL верна. И блок
# БЫСТРЫЙ: на полном газу он проходит всю полосу меньше чем за
# полторы секунды. Из-за этого ступени по 1.5с были втрое длиннее
# нужного, блок половину времени стоял в упоре у края, и подгонка
# объясняла лишь 61-70% - данные были испорчены упором.
# Теперь короткие знакопеременные импульсы: блок качается около
# середины и в края не бьётся. И короче по времени, потому что рыба
# 13 секунд без управления не живёт.
# Длина ступени - главный параметр замера, и я дважды ошибся с ней.
# Ступени по 1.5с были слишком длинными: блок долетал до края полосы и
# там стоял, а время в упоре про физику не говорит ничего. Ступени по
# 0.45с оказались слишком короткими: у блока запаздывание около 270 мс
# (измерено), то есть больше половины ступени - это отклик на
# ПРЕДЫДУЩУЮ, и ступени перемешиваются.
# Берём 1.0с: запаздывание съедает четверть, и блок не успевает
# долететь до края. Уровни чередуются, чтобы он качался около середины.
_P = [0.75, 0.25, 1.0, 0.0, 0.65, 0.35]
CALIB_PLAN = [(0.8, 0.5)] + [(0.8 + 1.0 * (i + 1), v)
                             for i, v in enumerate(_P)]
CALIB_LAG_MAX = 0.55   # в каких пределах искать запаздывание


def focus_game():
    """Вернуть фокус окну Roblox. True - фокус и так был у игры,
    "switched" - пришлось переключить и получилось, False - не вышло,
    None - не Windows или окно не найдено.

    Зачем: после любого клика по окну макроса (Настройки, Копировать
    журнал) фокус остаётся у макроса. Первое нажатие мыши по
    неактивной игре Windows тратит на её активацию - заброс не
    происходит, а все Enter уходят в окно макроса. Так один раз
    пропал целый заброс: удочка стояла пустой, макрос ждал поклёвку."""
    try:
        import ctypes
        u = ctypes.windll.user32
    except Exception:
        return None
    try:
        h = u.FindWindowW(None, "Roblox")
        if not h:
            return None
        if u.GetForegroundWindow() == h:
            return True
        # Windows не даёт чужому процессу забирать фокус просто так.
        # Нажатие Alt снимает этот запрет. Alt уходит в окно макроса,
        # которое сейчас активно, - игре оно не мешает.
        u.keybd_event(0x12, 0, 0, 0)
        u.keybd_event(0x12, 0, 2, 0)
        u.ShowWindow(h, 9)
        u.SetForegroundWindow(h)
        time.sleep(0.15)
        return "switched" if u.GetForegroundWindow() == h else False
    except Exception:
        return False


def active_window():
    """Заголовок окна, которое сейчас в фокусе. Если это не Roblox,
    игра не получит ни одного нашего нажатия: Windows отдаёт ввод
    только активному окну."""
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        return buf.value or "(без заголовка)"
    except Exception:
        return None


def calib_verdict(rows, n_hold=None):
    """Доходят ли нажатия до игры.

    Смысл разомкнутой проверки именно в этом: скважность там задана
    расписанием и ни от чего не зависит, поэтому разницу в движении
    блока можно толковать прямо, без оговорок про обратную связь."""
    if len(rows) < 120:
        return "калибровка слишком короткая, судить не берусь"
    t = np.array([r[0] for r in rows], float)
    d = np.array([r[1] for r in rows], float)
    c = np.array([r[2] for r in rows], float)
    dt = float(np.median(np.diff(t))) or 1 / 60
    v = np.gradient(np.convolve(c, np.ones(5) / 5, mode="same"), dt)
    # У блока есть запаздывание. Без его учёта ступени перемешиваются
    # и разница между сильным и слабым нажатием смазывается в ноль.
    best = (0, -2.0)
    for lag in range(0, int(CALIB_LAG_MAX / dt)):
        a = d[:len(d) - lag] if lag else d
        b = v[lag:] if lag else v
        if len(a) < 60:
            break
        rr = float(np.corrcoef(a, b)[0, 1])
        if rr > best[1]:
            best = (lag, rr)
    lag = best[0]
    d = d[:len(d) - lag] if lag else d
    v = v[lag:] if lag else v
    t = t[:len(t) - lag] if lag else t
    m = t > CALIB_PLAN[0][0] + 0.8        # пропускаем начало миниигры
    hi, lo = m & (d >= 0.65), m & (d <= 0.35)
    if hi.sum() < 20 or lo.sum() < 20:
        return "мало данных по ступеням"
    diff = float(v[hi].mean() - v[lo].mean())
    out = [f"запаздывание блока {lag * dt * 1000:.0f} мс (связь {best[1]:+.2f})",
           f"    сильное нажатие {v[hi].mean():+.0f} px/с, "
           f"слабое {v[lo].mean():+.0f} px/с, разница {diff:+.0f} px/с"]
    if n_hold is not None and n_hold < 20:
        out += ["!!! МЫ САМИ НЕ НАЖИМАЛИ: кнопка почти не переключалась.",
                "    Это наша поломка, а не игра. Смотри строку про поток",
                "    ШИМ и про ошибку при нажатии выше."]
    elif diff < 60:
        out += ["!!! БЛОК НЕ ОТВЕТИЛ ВООБЩЕ. Кнопка переключалась, значит",
                "    код работает - ввод не принимает игра. Обычно так",
                "    бывает, когда окно Roblox не активно: Windows отдаёт",
                "    ввод только активному окну."]
    elif diff < 250:
        out += [f"    отклик ЕСТЬ, но слабый ({diff:.0f} px/с).",
                "    Нажатия доходят. Либо замер смазан, либо рыба тянет",
                "    сильнее, чем мы толкаем. Повтори - если цифра скачет",
                "    от раза к разу, дело в замере, а не в игре."]
    else:
        out.append(f"    нажатия доходят: блок отзывается на {diff:.0f} px/с")
    return "\n".join(out)


def ab_report():
    """Сводка по сравниваемым настройкам.

    Главное теперь - доля ВЗЯТЫХ рыб, а не секунды: быстрая
    настройка, теряющая каждую пятую рыбу, хуже медленной, которая
    не теряет ни одной. Секунды - медиана по всем рыбам варианта.
    Исход берётся по карточке поимки; "неясные" в долю не входят."""
    out = ["--- сравнение настроек ---",
           f"{'GAIN':>8} {'рыб':>4} {'взято':>6} {'сорв':>5} {'доля':>6} "
           f"{'секунд':>7} {'упор':>5}"]
    rows = []
    for g in sorted(AB_STATS):
        v = AB_STATS[g]
        if not v:
            continue
        a = np.array([x[:4] for x in v], dtype=float)
        oc = [x[4] for x in v if len(x) > 4 and x[4] is not None]
        got, n_lost = sum(oc), len(oc) - sum(oc)
        rate = got / len(oc) if oc else None
        rows.append((g, len(v), got, n_lost, rate, float(np.median(a[:, 0]))))
        rs = f"{100 * rate:5.0f}%" if rate is not None else "   ? "
        out.append(f"{g:8.4f} {len(v):4d} {got:6d} {n_lost:5d} {rs} "
                   f"{np.median(a[:, 0]):7.1f} {np.median(a[:, 1]):4.0f}%")
    if len(rows) > 1:
        if min(r[1] for r in rows) < 10:
            out.append("  (меньше 10 рыб на вариант - судить рано)")
        else:
            best = max(rows, key=lambda r: ((r[4] if r[4] is not None else 0),
                                            -r[5]))
            out.append(f"  лучше всех GAIN {best[0]}: доля взятых выше, "
                       f"при равной - быстрее")
            if all(r[3] == 0 for r in rows):
                out.append("  (срывов нет ни у кого - сравнение идёт по времени)")
    return "\n".join(out)


def calib_duty(el):
    """Какую скважность держать на el-й секунде калибровки."""
    for until, val in CALIB_PLAN:
        if el < until:
            return val
    return None            # расписание кончилось
nav_pending = False    # нужно нажать \\ для входа в navigation mode
fp_pending = False     # нужно завести камеру внутрь персонажа


CAPTURE = _cfg("CAPTURE", "mss")     # способ захвата экрана: mss / dxcam
TRACK_STYLE = _cfg("TRACK_STYLE", "line")   # как вести рыбу: line / color
SHOW_OVERLAY = _cfg("SHOW_OVERLAY", True)   # треугольники поверх игры
# цвета деталей миниигры для режима color ("none" - не искать)
COLOR_DEFAULTS = {"TARGET": ("#434b5b", 8), "ARROW": ("none", 8),
                  "LEFT": ("#f1f1f1", 10), "RIGHT": ("#ffffff", 10)}
for _k, (_c, _t) in COLOR_DEFAULTS.items():
    globals()["COL_" + _k] = _cfg("COL_" + _k, _c)
    globals()["TOL_" + _k] = _cfg("TOL_" + _k, _t)
CAPTURE_NOTE = ""                     # почему dxcam не заработал (для окна)


class Capture:
    """Захват экрана двумя способами - и переключение между ними на лету.

    mss - обычный снимок области через Windows (BitBlt). Работает везде.
    dxcam - Desktop Duplication: кадр берётся прямо у видеокарты. На
    некоторых компьютерах быстрее и ровнее, особенно в полноэкранной игре.
    Нужен пакет dxcam; если его нет или он не запустился, макрос сам
    остаётся на mss и пишет почему.

    Особенность dxcam: grab() отдаёт None, если экран не менялся с
    прошлого раза, - а за один кадр мы снимаем несколько зон (полоса,
    XP, карточка). Поэтому берём весь экран и режем зоны из него: если
    новый кадр не пришёл, прошлый ТОЧНО верен - экран-то не менялся.
    Отдаёт то же, что mss: массив высота x ширина x 4, порядок BGRA."""

    def __init__(self):
        self._mss = mss.mss()
        self.monitors = self._mss.monitors
        self.cam = None
        self.frame = None
        self.kind = None
        self._switch(CAPTURE)

    def _switch(self, want):
        global CAPTURE_NOTE
        if want == "dxcam":
            try:
                import dxcam
                if self.cam is None:
                    self.cam = dxcam.create(output_color="BGRA")
                    if self.cam is None:
                        raise RuntimeError("dxcam.create() не дал камеру")
                self.kind = "dxcam"
                self.frame = None
                CAPTURE_NOTE = ""
                say("захват экрана: dxcam")
                return
            except Exception as e:
                CAPTURE_NOTE = str(e)[:120]
                say(f"dxcam недоступен ({CAPTURE_NOTE}) - захват через mss")
        if self.kind != "mss":
            self.kind = "mss"
            say("захват экрана: mss")

    def grab(self, reg):
        want = CAPTURE if CAPTURE in ("mss", "dxcam") else "mss"
        if want != self.kind and not (want == "dxcam" and CAPTURE_NOTE):
            self._switch(want)
        if self.kind == "dxcam":
            f = self.cam.grab()
            if f is not None:
                self.frame = f
            elif self.frame is None:
                return np.asarray(self._mss.grab(reg))
            l, t = reg["left"], reg["top"]
            return self.frame[t:t + reg["height"], l:l + reg["width"]]
        return np.asarray(self._mss.grab(reg))

    def shot(self, **kw):
        return self._mss.shot(**kw)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        try:
            self._mss.close()
        except Exception:
            pass
        try:
            if self.cam is not None:
                self.cam.release()
        except Exception:
            pass
        return False


# ======================= ГОРЯЧИЕ КЛАВИШИ =======================
# (настройка, по умолчанию, (англ., рус.)) - меняются в пульте, Main -> Hotkeys
HOTKEYS = [("KEY_START", "f8", ("start / pause", "старт / пауза")),
           ("KEY_SNAP", "f7", ("detector snapshot", "снимок детектора")),
           ("KEY_BURST", "f3", ("frame burst", "серия кадров")),
           ("KEY_CALIB", "f4", ("calibrate", "калибровка")),
           ("KEY_NAV", "f5", ("navigation mode", "navigation mode")),
           ("KEY_DIAG", "f6", ("diagnostics", "диагностика")),
           ("KEY_AREA", "f2", ("change area", "изменить зоны")),
           ("KEY_EXIT", "f9", ("exit", "выход"))]
for _n, _d, _l in HOTKEYS:
    globals()[_n] = _cfg(_n, _d)
# их нельзя: Enter жмёт сам макрос (shake), пробел/Tab/Esc нужны игре
HOTKEY_BANNED = {"enter", "space", "tab", "esc"}
hotkey_capture = False      # пока в пульте назначают клавишу - не реагируем
area_pending = False        # попросили открыть редактор зон (клавиша)


def key_name(key):
    """Клавиша pynput -> имя для scarlet.ini: f8, insert, page_up, x, 7..."""
    n = getattr(key, "name", None)
    if n:
        return n.lower()
    ch = getattr(key, "char", None)
    if ch:
        return ch.lower()
    vk = getattr(key, "vk", None)
    return f"vk{vk}" if vk is not None else ""


KEY_SHORT = {"page_up": "PgUp", "page_down": "PgDn", "insert": "Ins", "delete": "Del",
             "scroll_lock": "ScrLk", "num_lock": "NumLk", "caps_lock": "Caps",
             "print_screen": "PrtSc", "pause": "Pause", "home": "Home", "end": "End",
             "backspace": "Bksp", "up": "\u2191", "down": "\u2193", "left": "\u2190",
             "right": "\u2192"}


def key_label(name):
    """Имя клавиши -> как показать на кнопке: F8, Ins, X."""
    return KEY_SHORT.get(name, name.upper())


CHECK_UPDATES = _cfg("CHECK_UPDATES", True)
WELCOME_DONE = _cfg("WELCOME_DONE", False)   # 4: приветствие при первом запуске
UPDATE_INFO = None          # (версия, ссылка), если на GitHub есть новее


def _ver_tuple(v):
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", str(v))[:4])


def check_updates():
    """Последний релиз на GitHub (в фоне, при запуске). Нет сети, нет
    репозитория, лимит запросов - молча ничего."""
    global UPDATE_INFO
    repo = globals().get("GITHUB_REPO", "")
    if not CHECK_UPDATES or not repo:
        return
    try:
        import json
        import urllib.request
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"User-Agent": f"ScarletHub/{VERSION}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        tag = str(data.get("tag_name", "")).lstrip("vV")
        url = data.get("html_url") or f"https://github.com/{repo}/releases/latest"
        if tag and _ver_tuple(tag) > _ver_tuple(VERSION):
            UPDATE_INFO = (tag, url)
            say(f"доступна новая версия {tag}: {url}")
    except Exception:
        pass


def toggle_running():
    """Старт/пауза - и по F8, и по кнопке в окне Scarlet hub."""
    global running, nav_pending, fp_pending
    running = not running
    if running and SHAKE_MODE == "navigation" and NAV_AUTO:
        nav_pending = True

    if running and FIRST_PERSON and not shake_clicks_mode():
        fp_pending = True
    elif running and FIRST_PERSON:
        say("    вид от первого лица НЕ включаю: в нём Roblox держит курсор в "
            "центре, и щелчки по кнопкам shake (pixel/circle) не доходят")
    print(">>> РАБОТАЮ" if running else ">>> ПАУЗА")
    # Если при запуске проверка что-то нашла - перепроверяем на старте:
    # вдруг уже исправили. Не мешает ловле: идёт в своём потоке.
    if running and any(r[0] == "bad" for r in last_env):
        threading.Thread(target=log_env_check, args=("проверка перед стартом",),
                         daemon=True).start()


def on_press(key):
    global running, should_exit, diagnose, nav_pending, fp_pending, snap_pending
    global calib_pending, burst_pending
    if hotkey_capture:
        return                  # клавишу сейчас назначают в пульте
    k = key_name(key)
    if not k:
        return
    if k == KEY_START:
        toggle_running()
    elif k == KEY_NAV:
        nav_pending = True
        print(">>> перевключаю navigation mode")
    elif k == KEY_DIAG:
        diagnose = True
    elif k == KEY_SNAP:
        snap_pending = True
        print(">>> сохраняю кадр области")
    elif k == KEY_BURST:
        burst_pending = True
        print(">>> записываю серию кадров")
    elif k == KEY_CALIB:
        calib_pending = True
        print(">>> следующая рыба пойдёт на калибровку")
    elif k == KEY_AREA:
        globals()["area_pending"] = True
    elif k == KEY_EXIT:
        should_exit = True
        return False


# ==================================================================
#  ДЕТЕКТОР 2.0.  Снимки snap_1..3 показали главное, чего мы не знали:
#  интерфейс Fisch ПОЛУПРОЗРАЧНЫЙ. Он не красит пиксели в свой цвет,
#  а лишь подкрашивает и затемняет мир за собой. Поэтому все прежние
#  признаки - абсолютная яркость, насыщенность, длинный ровный участок -
#  держались только над водой и разваливались над досками и травой.
#
#  Зато два признака держатся на ЛЮБОМ фоне (проверено на трёх снимках
#  с водой, деревом и травой):
#    1. полоса ЗАТЕМНЯЕТ всё под собой  -> её строки самые тёмные;
#    2. блок и рыба ОТЛИЧАЮТСЯ от собственного фона полосы.
#  Измерено: строки полосы 37..67 во всех трёх снимках, блок 100-101 px
#  (это и есть tryhard), запас по рыбе 40-68 единиц при пороге 12.
# ==================================================================
REF_GAP, REF_WIN = 8, 22    # окна-образцы слева и справа от кандидата
REF_MIN = 6            # меньше стольких колонок в окне - сторона не в счёт
FISH_DIFF_MIN = 30     # цветовой разрыв, ниже которого кандидат не рыба
FISH_SIDES = 0.6       # слева и справа от рыбы должно быть одно и то же
# Рыба РОВНО НА КРАЮ блока: условие "слева и справа одно и то же" там
# невыполнимо в принципе - с одной стороны блок, с другой полоса.
# Все четыре сохранённых кадра потери - ровно этот случай: рыба в 5-7
# пикселях от края блока, тогда как во всех удачных кадрах она была в
# 50-95. Поэтому если строгая проверка молчит, а в прошлом кадре рыба
# была видна, ищем рядом с прошлым положением и берём БЛИЖАЙШЕГО
# кандидата, а не сильнейшего: сильнейшим там оказывается стрелка.
TRACK_RAD = 18         # радиус поиска вокруг прошлого положения
TRACK_DIFF = 25        # порог цветового разрыва в режиме сопровождения
# В 2.8 запасной путь работал только если рыба была видна В ПРОШЛОМ
# кадре. Стоило ему один раз не найти её у края - и он отключался до
# тех пор, пока строгая проверка сама не увидит рыбу. А она у края и
# не видит. Получался капкан: рыба стоит на границе, оба пути молчат,
# через 2 секунды миниигра сбрасывается. Теперь запасной путь живёт
# ещё столько секунд после пропажи.
TRACK_HOLD = 0.6

_EDGE_HIT = False      # последнюю рыбу нашли запасным путём у края
_V2 = None             # (цвет колонок, лево_полосы, право_полосы, лево, право)


def _win_mean(c, a, b, blo, bhi):
    """Средний цвет колонок окна [x+a, x+b) для каждого x, ОБРЕЗАННОГО
    границами blo/bhi (свои для каждой колонки). Через накопленную
    сумму, чтобы не гонять цикл на каждый кадр.

    Обрезка - самое важное здесь. Без неё у рыбы, стоящей ближе 34
    пикселей к краю блока, одно окно попадало на блок, другое на полосу,
    они не совпадали, и рыба отбрасывалась. При блоке в 100 пикселей
    работала только середина шириной 30. Замер на snap_3: без обрезки
    |лево-право| 55-171, с обрезкой 3-6."""
    cs = np.cumsum(np.vstack([np.zeros(c.shape[1], c.dtype), c]), axis=0)
    n = c.shape[0]
    idx = np.arange(n)
    lo = np.clip(idx + a, blo, bhi)
    hi = np.clip(idx + b, blo, bhi)
    cnt = hi - lo
    return (cs[hi] - cs[lo]) / np.maximum(cnt, 1)[:, None], cnt


def find_fish_v2(prev=None):
    global _EDGE_HIT
    """Рыба - узкий ЦВЕТОВОЙ РАЗРЫВ, по обе стороны от которого одно и
    то же. Так она находится и на полосе (краснее фона), и на блоке
    (бледнее его) - раньше мы искали выброс только в одну сторону и
    внутри блока рыбу теряли.

    Условие "слева и справа одинаково" отсекает доски и стыки текстур:
    у края доски слева одно, справа другое. У рыбы - одинаково."""
    if _V2 is None:
        return None
    c, lo, hi, left, right = _V2
    n = c.shape[0]
    idx = np.arange(n)
    # окна-образцы не должны пересекать край блока: слева и справа от
    # рыбы обязано быть ОДНО И ТО ЖЕ - либо полоса, либо блок
    blo = np.where(idx < left, lo, np.where(idx > right, right + 3, left + 3))
    bhi = np.where(idx < left, left - 3, np.where(idx > right, hi, right - 3))
    blo = np.clip(blo, 0, n)
    bhi = np.clip(np.maximum(bhi, blo), 0, n)
    lw, cl = _win_mean(c, -REF_GAP - REF_WIN, -REF_GAP, blo, bhi)
    rw, cr = _win_mean(c, REF_GAP, REF_GAP + REF_WIN, blo, bhi)
    d = np.minimum(np.abs(c - lw).sum(axis=1), np.abs(c - rw).sum(axis=1))
    ok = ((d > FISH_DIFF_MIN)
          & (np.abs(lw - rw).sum(axis=1) < d * FISH_SIDES)
          & (cl >= REF_MIN) & (cr >= REF_MIN))
    ok[:lo + 5] = False
    ok[hi - 5:] = False
    pad = np.concatenate(([0], ok.astype(np.int8), [0]))
    e = np.where(np.diff(pad) != 0)[0]
    # Никакого ограничения "рыба не дальше N от блока" здесь быть не должно.
    # Полоса 778 пикселей, а блок tryhard всего 101: рыба законно уходит
    # на 300+ пикселей. Прежний предел (полублок + 150 = 200) срезал
    # распределение ровно по 200 - в записи максимум был ровно 200, и
    # рыба "пропадала" на 2 секунды, пока не возвращалась ближе.
    # Границы полосы (lo/hi выше) - единственное честное ограничение.
    best = None
    for p, q in zip(e[0::2], e[1::2]):
        p, q = int(p), int(q)
        if not (FISH_MIN_W <= q - p <= FISH_MAX_W + 2):
            continue
        cand = (p + q) // 2
        score = float(d[p:q].max())
        if prev is not None:
            score -= abs(cand - prev) * 0.15
        if best is None or score > best[1]:
            best = (cand, score)
    _EDGE_HIT = False
    if best is not None:
        return best[0]

    # --- запасной путь: рыба у края блока ---
    if prev is None:
        return None          # не за чем идти
    if t_fish_lost != 0.0 and time.perf_counter() - t_fish_lost > TRACK_HOLD:
        return None          # потеряли давно, это уже не сопровождение
    a0 = max(lo + 6, int(prev) - TRACK_RAD)
    b0 = min(hi - 6, int(prev) + TRACK_RAD)
    if b0 - a0 < 5:
        return None
    hits = []
    for x in range(a0, b0):
        la = c[max(lo, x - REF_GAP - REF_WIN):x - REF_GAP]
        rb = c[x + REF_GAP:min(hi, x + REF_GAP + REF_WIN)]
        if la.shape[0] < REF_MIN or rb.shape[0] < REF_MIN:
            continue
        dd = min(float(np.abs(c[x] - la.mean(axis=0)).sum()),
                 float(np.abs(c[x] - rb.mean(axis=0)).sum()))
        if dd > TRACK_DIFF:
            hits.append(x)
    if not hits:
        return None
    runs, cur = [], [hits[0]]
    for x in hits[1:]:
        if x - cur[-1] <= 2:
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    runs.append(cur)
    mids = [(r[0] + r[-1]) // 2 for r in runs
            if FISH_MIN_W - 1 <= r[-1] - r[0] + 1 <= FISH_MAX_W + 2]
    if not mids:
        return None
    _EDGE_HIT = True
    return min(mids, key=lambda x: abs(x - prev))


# ==================================================================
#  ДЕТЕКТОР 6.0 - УНИВЕРСАЛЬНЫЙ, НЕ ЗНАЕТ ЦВЕТОВ УДОЧКИ
#
#  Прежний детектор держался на двух допущениях, выведенных из tryhard:
#    1) полоса ТЕМНЕЕ мира под собой -> её строки искались как самые
#       тёмные. У белой удочки строки полосы наоборот самые яркие, а
#       на тёмном камне даже tryhard давал не те строки (0..35 вместо
#       37..67 - в серии 6 он нашёл блок лишь в 18 кадрах из 61);
#    2) блок уже половины полосы -> фон полосы считался медианой по
#       всей ширине. У белой удочки блок занимает почти всю полосу.
#
#  Теперь:
#    - строки полосы - по её ГРАНИЦАМ: у любой полосы есть резкий верх
#      и низ на всю ширину;
#    - блок - отрезок, сильнее всего отличающийся от остальной полосы
#      (как метод Оцу, только для отрезка); плюс разбиение на три куска,
#      когда фон слева и справа разный (доски | блок | вода);
#    - ПАМЯТЬ: в начале боя блок по центру, там ошибиться нельзя, и
#      детектор запоминает его цвет, цвет фона и ширину. Когда блок
#      упирается в край полосы, по одному кадру не понять, какой кусок
#      блок, - а по памяти можно. Если облик резко сменился (Ruinous
#      покраснел, блок потускнел) - память сбрасывается и учится заново.
#
#  Проверено на 6 сериях по 61 кадру (белая, Ruinous во всех четырёх
#  обликах, tryhard): блок верно в 247 из 249 кадров с известной
#  истиной, рыба на белой - 122 из 122 с ошибкой 1 px. 2 мс на кадр.
# ==================================================================
U_MIN_H, U_MAX_H = 12, 60
U_MIN_W = 40
U_GAP_MERGE = 70       # стрелка внутри блока шириной около 60 px
# Границы полосы - свойство интерфейса игры, а не удочки: на всех трёх
# удочках одни и те же 72..848, строго по центру области. Искать их в
# каждом кадре ненадёжно (чёрная полоса Ruinous на тёмном камне почти
# не видна), поэтому они задаются.
BAR_LO = _cfg("BAR_LO", 72)
BAR_HI = _cfg("BAR_HI", 848)
# Есть ли полоса вообще: сила её верхнего и нижнего края по ширине.
# Замер: где полоса есть - минимум 73, где её нет - максимум 18.
U_PRESENT = _cfg("U_PRESENT", 40.0)


def u_band_rows(a, dy=None):
    """Строки полосы по её краям: у любой полосы - тёмной прозрачной,
    белой непрозрачной, чёрной - есть резкий верх и низ на всю ширину."""
    if dy is None:
        dy = np.abs(np.diff(a, axis=0)).sum(axis=2)
    d = dy.mean(axis=1)
    n = len(d)
    y = np.arange(n)
    gap = y[None, :] - y[:, None]
    ok = (gap >= U_MIN_H) & (gap < U_MAX_H) & (y[:, None] >= 2)
    sc = np.where(ok, np.minimum(d[:, None], d[None, :]), -1.0)
    y1, y2 = np.unravel_index(np.argmax(sc), sc.shape)
    return int(y1) + 1, int(y2)


def u_best_segment(c, min_w=U_MIN_W, step=6, min_out=16):
    """Отрезок [L,R), сильнее всего отличающийся от остальной полосы:
    максимум межклассовой дисперсии, как у Оцу, но для отрезка."""
    n = c.shape[0]
    S = np.vstack([np.zeros((1, c.shape[1])), np.cumsum(c, axis=0)])
    idx = np.arange(0, n + 1, step)
    L = idx[:, None]
    R = idx[None, :]
    w = (R - L).astype(float)
    ok = (w >= min_w) & (w <= n - min_out)
    Si = S[R] - S[L]                                   # сумма внутри
    tot = S[n]
    m_in = Si / np.maximum(w, 1)[..., None]
    m_out = (tot - Si) / np.maximum(n - w, 1)[..., None]
    score = ((m_in - m_out) ** 2).sum(axis=-1) * w * (n - w) / n
    score[~ok] = -1
    i, j = np.unravel_index(np.argmax(score), score.shape)
    return int(idx[i]), int(idx[j]), float(score[i, j])


def u_three_segments(c, min_w=U_MIN_W, step=6):
    """Разбиение полосы на три куска: фон | блок | фон, где фон слева и
    справа МОЖЕТ отличаться (сквозь полупрозрачную полосу слева видны
    доски, справа вода). Минимум суммы квадратов отклонений от средних
    по кускам. Возвращает (L, R) - границы среднего куска."""
    n = c.shape[0]
    z = np.zeros((1, c.shape[1]))
    S = np.vstack([z, np.cumsum(c, axis=0)])
    Q = np.vstack([z, np.cumsum(c * c, axis=0)])
    def sse(a, b):
        w = np.maximum(b - a, 1)[..., None]
        s = S[b] - S[a]
        return (Q[b] - Q[a] - s * s / w).sum(axis=-1)
    idx = np.arange(0, n + 1, step)
    L = idx[:, None] + 0 * idx[None, :]
    R = 0 * idx[:, None] + idx[None, :]
    total = sse(np.zeros_like(L), L) + sse(L, R) + sse(R, np.full_like(R, n))
    bad = (R - L < min_w) | (R - L > n - 16)
    total = np.where(bad, np.inf, total)
    i, j = np.unravel_index(np.argmin(total), total.shape)
    return int(idx[i]), int(idx[j])


def u_refine(c, L, R, fn_score, rad=6):
    """Уточнить границы, найденные грубым перебором, с шагом 1."""
    best = (fn_score(L, R), L, R)
    for l in range(max(0, L - rad), L + rad + 1):
        for r in range(R - rad, min(len(c), R + rad) + 1):
            if r - l < U_MIN_W:
                continue
            sc = fn_score(l, r)
            if sc > best[0]:
                best = (sc, l, r)
    return best[1], best[2]


def u_best_run_by_width(mask, gap, w, narrow=30):
    """Самый длинный сплошной кусок маски (разрывы до gap сшиты), но с
    поправкой: если на краю куска висит узкая полоска - это скорее
    линия рыбы, прилипшая к блоку (у тусклого блока Ruinous серая рыба
    на чёрной полосе светлее фона, как и сам блок). Пробуем варианты
    без таких полосок и берём тот, что ближе к запомненной ширине."""
    xs = np.where(mask)[0]
    if len(xs) < U_MIN_W:
        return None
    segs = np.split(xs, np.where(np.diff(xs) > 1)[0] + 1)
    segs = [(int(g[0]), int(g[-1]) + 1) for g in segs]
    runs, cur = [], [segs[0]]
    for sg in segs[1:]:
        if sg[0] - cur[-1][1] <= gap:
            cur.append(sg)
        else:
            runs.append(cur)
            cur = [sg]
    runs.append(cur)
    main = max(runs, key=lambda rr: rr[-1][1] - rr[0][0])
    variants = [main]
    a, b = 0, len(main)
    if b - a > 1 and main[0][1] - main[0][0] < narrow:
        variants.append(main[1:])
    if b - a > 1 and main[-1][1] - main[-1][0] < narrow:
        variants.append(main[:-1])
    if len(main) > 2 and main[0][1] - main[0][0] < narrow \
            and main[-1][1] - main[-1][0] < narrow:
        variants.append(main[1:-1])
    best = min(variants, key=lambda v: abs((v[-1][1] - v[0][0]) - w))
    if best[-1][1] - best[0][0] < U_MIN_W:
        return None
    return best[0][0], best[-1][1]


def u_pieces_stats(c, l, r, lo, hi):
    inside = c[l:r].mean(axis=0)
    rest = [c[lo:l]] if l > lo else []
    if r < hi:
        rest.append(c[r:hi])
    outside = np.concatenate(rest).mean(axis=0) if rest else inside * 0
    return inside, outside


U_EDGE_FIX = _cfg("U_EDGE_FIX", True)    # 0 - не уточнять края блока
U_EDGE_REACH = 100     # насколько дальше найденного края искать настоящий


def u_find_edge(c, e, lo, hi, side, reach=U_EDGE_REACH):
    """Настоящий край блока-ГРАДИЕНТА.

    Блок crowbar серый, светлый в середине и тёмный к краям. Детектор по
    цвету считал блоком только светлую середину: 278 пикселей из 348.
    Рыба в тёмных краях блока оказывалась "вне блока", регулятор дёргал
    блок к ней, а поиск рыбы там её терял - отсюда и "часто теряется".

    Край - самый сильный скачок цвета в пределах reach наружу, если:
      - он заметно сильнее скачка на нынешнем краю (у резких блоков
        белой, tryhard, Ruinous нынешний край и есть самый сильный -
        для них ничего не меняется);
      - между ним и нынешним краем плавно, без других скачков;
      - снаружи рядом нет парного скачка - иначе это узкий предмет
        (рыба возле блока), а не край.
    side = -1: e - первая колонка блока; side = +1: e - за последней."""
    j = np.abs(np.diff(c, axis=0)).sum(axis=1)       # j[x]: скачок x -> x+1
    if side < 0:
        a = max(lo + 1, e - reach)
        if a >= e - 2:
            return None
        k = a + int(np.argmax(j[a - 1:e - 3]))        # новый край: колонка k
        J = float(j[k - 1])
        cur = float(j[max(0, e - 3):e + 1].max())
        inner = j[k:e - 1]
        out = j[max(0, k - 31):max(0, k - 7)]
    else:
        b = min(hi - 1, e + reach)
        if b <= e + 2:
            return None
        k = e + 2 + int(np.argmax(j[e + 1:b - 1]))    # новый край: за колонкой k-1
        J = float(j[k - 1])
        cur = float(j[max(0, e - 2):e + 2].max())
        inner = j[e:k - 1]
        out = j[min(len(j), k + 6):min(len(j), k + 30)]
    if J < 40 or J < 1.8 * cur:
        return None
    # У crowbar рамка блока (красная/чёрная, ~5 px): два сильных скачка
    # рядом - снаружи в рамку и из рамки в серое. Край - внутренний из них,
    # иначе второй скачок попадал в "между" и край отбрасывался.
    if side < 0:
        for m in range(k, min(k + 8, e - 2)):
            if j[m] >= 0.5 * J:
                k = m + 1
        inner = j[k:e - 1]
    else:
        for m in range(k - 2, max(k - 10, e), -1):
            if j[m] >= 0.5 * J:
                k = m + 1
        inner = j[e:k - 1]
    if len(inner) and float(inner.max()) > 0.35 * J:
        return None
    if len(out) and float(out.max()) > 0.5 * J:
        return None
    return k


class BarTracker:
    """Блок с памятью между кадрами одного боя."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.m_blk = self.m_bg = self.w = None
        self.last_band = None
        self.reset_edges()

    def reset_edges(self):
        # насколько настоящий край дальше найденного по цвету; копится
        # только по уверенным кадрам и подставляется, когда край не виден
        # (рыба в нём или блок упёрся в стенку)
        self.dl = self.dr = 0.0
        self.nl = self.nr = 0

    def present(self, a, top, bot, lo, hi, tol=12, dy=None):
        # tol был 5. У crowbar рамка блока выше и ниже самой полосы на ~10
        # строк. На тёмном фоне сильнее были внутренние края блока - и
        # края полосы попадали в допуск. На светлом (розовая скала) сильнее
        # стала ВНЕШНЯЯ рамка, края полосы оказались в 10 строках от неё,
        # и полосы "не было" весь бой: 8-10 при пороге 40. С допуском 12:
        # полоса 120-148, пустой экран не выше 25 (stuck, after_1-4, burst).
        if dy is None:
            dy = np.abs(np.diff(a, axis=0)).sum(axis=2)
        def strongest(y):
            y0, y1 = max(0, y - tol), min(dy.shape[0], y + tol + 1)
            return dy[y0:y1].max(axis=0)
        e = np.minimum(strongest(top - 1), strongest(bot))[lo:hi]
        return float(np.percentile(e, 25))

    def step(self, bgr):
        # int16 вместо float32 и скачки по строкам - ОДИН раз на кадр:
        # раньше их считали дважды (строки полосы и проверка наличия),
        # это была треть времени детектора.
        a = bgr[:, :, :3].astype(np.int16)
        dy = np.abs(np.diff(a, axis=0)).sum(axis=2)
        top, bot = u_band_rows(a, dy)
        lo, hi = BAR_LO + 5, BAR_HI - 5
        if hi - lo < 200 or bot - top < U_MIN_H:
            return None
        if self.present(a, top, bot, lo, hi, dy=dy) < U_PRESENT:
            self.last_band = None
            return None
        self.last_band = (top, bot)
        c = a[top + 2:bot - 1].mean(axis=0)
        n = hi - lo
        mem_hit = None
        if self.m_blk is not None:
            db = np.linalg.norm(c[lo:hi] - self.m_blk, axis=1)
            dg = np.linalg.norm(c[lo:hi] - self.m_bg, axis=1)
            run = u_best_run_by_width(db < dg, U_GAP_MERGE, self.w)
            if run is not None:
                l, r = lo + run[0], lo + run[1]
                if 0.6 * self.w <= r - l <= 1.45 * self.w:
                    mi, mo = u_pieces_stats(c, l, r, lo, hi)
                    mem_hit = (l, r, mi, mo)
        if mem_hit is not None:
            l, r, mi, mo = mem_hit
            contrast = float(np.linalg.norm(mi - mo))
        else:
            cands = set()
            L, R, _ = u_best_segment(c[lo:hi])
            cands.add((lo + L, lo + R))
            L3, R3 = u_three_segments(c[lo:hi])
            cands.add((lo + L3, lo + R3))
            best = None
            for l, r in cands:
                if not (U_MIN_W <= r - l <= n - 12):
                    continue
                if l <= lo + 3 or r >= hi - 3:
                    continue          # без памяти у края не решаем
                mi, mo = u_pieces_stats(c, l, r, lo, hi)
                contrast = float(np.linalg.norm(mi - mo))
                if best is None or contrast > best[0]:
                    best = (contrast, l, r)
            if best is None:
                return None
            _, l, r = best
            def _sc(ll, rr):
                a_, b_ = u_pieces_stats(c, ll, rr, lo, hi)
                w_ = rr - ll
                return float(np.sum((a_ - b_) ** 2)) * w_ * (n - w_) / n
            l, r = u_refine(c, l, r, _sc)
            mi, mo = u_pieces_stats(c, l, r, lo, hi)
            contrast = float(np.linalg.norm(mi - mo))
            self.m_blk = None
            self.reset_edges()
        if contrast > 25:
            if self.m_blk is None:
                self.m_blk, self.m_bg, self.w = mi, mo, float(r - l)
            else:
                k = 0.3
                self.m_blk = (1 - k) * self.m_blk + k * mi
                self.m_bg = (1 - k) * self.m_bg + k * mo
                self.w = (1 - k) * self.w + k * (r - l)
        L2, R2 = l, r
        if U_EDGE_FIX:
            wid = r - l
            # Рыба у края сама даёт скачок цвета, и край "находился" на
            # её дальнем контуре: на белом блоке так выучивалось +10 px.
            # Поэтому возле последней известной рыбы край не ищем.
            fh = FISH_MODEL.last
            near_l = fh is not None and abs(fh - l) < 40
            near_r = fh is not None and abs(fh - r) < 40
            nl = None if near_l else u_find_edge(c, l, lo, hi, -1)
            if nl is not None and 0 < l - nl <= 0.5 * wid:
                self.dl = (l - nl) if self.nl == 0 else 0.8 * self.dl + 0.2 * (l - nl)
                self.nl += 1
            nr = None if near_r else u_find_edge(c, r, lo, hi, +1)
            if nr is not None and 0 < nr - r <= 0.5 * wid:
                self.dr = (nr - r) if self.nr == 0 else 0.8 * self.dr + 0.2 * (nr - r)
                self.nr += 1
            # подставляем только выученное по 5+ кадрам: одна случайная
            # находка не должна раздувать блок
            if self.nl >= 5:
                L2 = nl if nl is not None and abs((l - nl) - self.dl) < 8 \
                    else l - int(round(self.dl))
            if self.nr >= 5:
                R2 = nr if nr is not None and abs((nr - r) - self.dr) < 8 \
                    else r + int(round(self.dr))
            L2, R2 = max(BAR_LO, L2), min(BAR_HI, R2)
        # Профиль ещё и по двум половинам высоты: у рыбы crowbar верх
        # тёмный, низ светлый, и в среднем по высоте она почти сливается
        # с блоком. По половинам - заметна.
        mid = (top + bot) // 2
        c6 = np.concatenate([a[top + 2:mid].mean(axis=0),
                             a[mid:bot - 1].mean(axis=0)], axis=1)
        return dict(left=int(L2), right=int(R2), top=int(top), bot=int(bot),
                    lo=int(lo), hi=int(hi), prof=c, prof6=c6)


BAR_TRACKER = BarTracker()
_V6 = None


FISH_MODEL_ON = _cfg("FISH_MODEL_ON", True)
LANG = _cfg("LANG", "en")          # язык окон: en / ru (журнал - по-русски)


def tr(en, ru):
    """Строка окна на выбранном языке."""
    return ru if LANG == "ru" else en


class FishModel:
    """Как ДОЛЖНА выглядеть полоса без рыбы - и рыба как отличие от этого.

    Прежний поиск искал узкое место, которое отличается от соседей. Это
    ломается дважды:
      - на ГРАДИЕНТЕ блока crowbar соседи слева и справа разные сами по
        себе, и рыба в краях блока отбрасывалась;
      - неподвижная деталь мира под полупрозрачной полосой (на снимке
        crowbar - жёлтый огонёк слева) тоже узкая и тоже отличается от
        соседей. Когда рыба видна слабо, поиск прыгал на огонёк.
    Здесь запоминаем фон полосы (он стоит на месте: камера от первого
    лица неподвижна) и облик самого блока от его левого края. Всё, что
    совпадает с запомненным, - не рыба, как бы оно ни выглядело.

    Модель только ПОДСТРАХОВЫВАЕТ прежний поиск: её ответ берётся, если
    прежний рыбу не нашёл или нашёл то, что модель узнаёт как фон или
    блок. На белой, Ruinous и tryhard прежний поиск работал - там он и
    остаётся главным."""

    FISH_PAD = 14          # столько вокруг рыбы не учим (это рыба, не фон)
    BLOCK_PAD = 4

    def __init__(self):
        self.reset()

    def reset(self):
        self.last = None
        self.bg = None
        self.seen = None
        self.tpl = None
        self.tpl_n = 0

    def _expected(self, n, left, right):
        exp = np.full((n, 6), np.nan, np.float32)
        if self.bg is not None:
            exp[self.seen] = self.bg[self.seen]
        L = 0
        if self.tpl is not None and self.tpl_n >= 5:
            L = min(len(self.tpl), right - left)
            exp[left:left + L] = self.tpl[:L]
        exp[left + L:right] = np.nan          # блок, облик которого не знаем
        return exp

    def residual(self, c6, left, right):
        """Отличие от ожидаемого, с допуском сдвига на 2 пикселя: край
        блока, найденный на пиксель иначе, иначе давал бы узкий всплеск,
        похожий на рыбу. Рыба шириной 13 такой допуск переживает."""
        exp = self._expected(c6.shape[0], left, right)
        best = None
        for s_ in (-2, -1, 0, 1, 2):
            e = np.roll(exp, s_, axis=0)
            d = np.abs(c6 - e).sum(axis=1)
            best = d if best is None else np.fmin(best, d)
        return best                            # nan там, где ждать нечего

    def find(self, c6, lo, hi, left, right, prev):
        r = self.residual(c6, left, right)
        known = ~np.isnan(r)
        known[:lo + 5] = False
        known[hi - 5:] = False
        if known.sum() < 150:
            return None, r, False
        noise = float(np.median(r[known]))
        healthy = noise < 20.0
        thr = max(45.0, 5.0 * noise)
        ok = known & (r > thr)
        pad = np.concatenate(([0], ok.astype(np.int8), [0]))
        e = np.where(np.diff(pad) != 0)[0]
        best = None
        for p, q in zip(e[0::2], e[1::2]):
            p, q = int(p), int(q)
            if not (FISH_MIN_W <= q - p <= FISH_MAX_W + 4):
                continue
            cand = (p + q) // 2
            score = float(r[p:q].max())
            if prev is not None:
                score -= abs(cand - prev) * 0.15
            if best is None or score > best[1]:
                best = (cand, score)
        return (best[0] if best else None), r, healthy

    UPD_TOL = 40.0         # сильнее отличается - это рыба, в фон не учим
    STALE = 60             # ...если только не держится секунду (мир сменился)

    def _learn(self, store, stale, cur, cols):
        d = np.abs(cur[cols] - store[cols]).sum(axis=1)
        empty = np.isnan(d)                   # ещё не видели - берём как есть
        store[cols[empty]] = cur[cols[empty]]
        cols, d = cols[~empty], d[~empty]
        agree = (d < self.UPD_TOL) | (stale[cols] > self.STALE)
        ci = cols[agree]
        store[ci] = 0.8 * store[ci] + 0.2 * cur[ci]
        stale[ci] = 0
        stale[cols[~agree]] += 1

    def update(self, c6, lo, hi, left, right, fish):
        """Учим по столбцам, которые СОГЛАСНЫ с тем, что уже знаем.
        Иначе рыба, которую не нашли, впечатывалась в фон, и на её месте
        потом мерещилась вторая рыба."""
        n = c6.shape[0]
        if self.bg is None or self.bg.shape[0] != n:
            self.bg = np.zeros_like(c6)
            self.seen = np.zeros(n, bool)
            self.bg_stale = np.zeros(n, np.int32)
        idx = np.arange(n)
        m = (idx >= lo) & (idx < hi)
        m &= (idx < left - self.BLOCK_PAD) | (idx >= right + self.BLOCK_PAD)
        if fish is not None:
            m &= np.abs(idx - fish) > self.FISH_PAD
        new = m & ~self.seen
        self.bg[new] = c6[new]
        self.seen |= new
        self._learn(self.bg, self.bg_stale, c6, np.where(m & ~new)[0])
        w = right - left
        if w < 20:
            return
        blk = c6[left:right]
        if self.tpl is None or abs(len(self.tpl) - w) > 6:
            # новый облик (или блок сменил ширину, как Ruinous) - учим заново
            self.tpl = blk.copy()
            if fish is not None:
                # в начале боя рыба стоит посреди блока - её в облик блока
                # не берём, иначе после её ухода там мерещилась бы рыба
                near = np.abs(np.arange(left, right) - fish) <= self.FISH_PAD
                self.tpl[near] = np.nan
            self.tpl_stale = np.zeros(w, np.int32)
            self.tpl_n = 1
            return
        L = min(len(self.tpl), w)
        cols = np.arange(L)
        if fish is not None:
            cols = cols[np.abs(cols + left - fish) > self.FISH_PAD]
        cur = np.zeros_like(self.tpl)
        cur[:L] = blk[:L]
        self._learn(self.tpl, self.tpl_stale, cur, cols)
        self.tpl_n += 1

    def match_block(self, c6, left, right, lo, hi, reach=40):
        """Где блок на самом деле - по его запомненному облику.

        Край блока-градиента по одному кадру находится ненадёжно: рыба у
        края сбивает и цвет, и скачок. Облик же целиком (348 столбцов)
        рыба закрывает лишь на 13 - медиана отличий её не замечает."""
        if self.tpl is None or self.tpl_n < 10:
            return None
        L = len(self.tpl)
        if abs((right - left) - L) > 12:
            return None                       # блок сменил ширину
        a = max(BAR_LO, left - reach)
        b = min(BAR_HI - L, left + reach)
        if b <= a:
            return None
        offs = np.arange(a, b + 1)
        # столбцы облика, где рыба стояла при обучении (NaN), не сравниваем;
        # np.median по оставшимся в разы быстрее nanmedian
        ok = ~np.isnan(self.tpl).any(axis=1)
        if ok.sum() < 20:
            return None
        win = np.lib.stride_tricks.sliding_window_view(c6, L, axis=0)
        win = win[offs][:, :, ok]              # (сдвиги, 6, столбцы)
        d = np.abs(win - self.tpl[ok].T[None]).sum(axis=1)
        score = np.median(d, axis=1)
        k = int(np.argmin(score))
        if not score[k] < 15.0:
            return None
        # Минимум должен быть ОСТРЫМ. У однотонного блока (белая, tryhard)
        # сдвиг на 30 пикселей портит лишь столбцы у краёв, и медиана его
        # не замечает - там такое сопоставление врёт, и блок там и так
        # находится точно. У градиента сдвиг портит каждый столбец.
        for kk in (k - 5, k + 5):
            if 0 <= kk < len(score) and not score[kk] >= score[k] + 6.0:
                return None
        return int(offs[k]), int(offs[k]) + L

    def fuse(self, f_old, prev):
        """Итоговая рыба: прежний ответ или ответ модели."""
        if _V2 is None or _V6 is None or _V6.shape[0] != _V2[0].shape[0]:
            return f_old, False
        _c, lo, hi, left, right = _V2
        f_new, r, healthy = self.find(_V6, lo, hi, left, right, prev)
        f, by_model = f_old, False
        if healthy:
            old_is_bg = False
            if f_old is not None:
                seg = r[max(0, f_old - 3):f_old + 4]
                seg = seg[~np.isnan(seg)]
                # то, что нашёл прежний поиск, модель знает как фон/блок
                old_is_bg = len(seg) > 0 and float(seg.max()) < 45.0
            if f_old is None and f_new is not None:
                f, by_model = f_new, True
            elif old_is_bg:
                # честное "не вижу" лучше неподвижной детали мира
                f, by_model = f_new, True
        self.update(_V6, lo, hi, left, right, f)
        if f is not None:
            self.last = f
        return f, by_model


FISH_MODEL = FishModel()


# ------------------------------------------------------------------
#  Два способа вести рыбу (7.8), выбираются у каждой удочки свои:
#
#  LINE  - прежний универсальный детектор 6.x: сам находит полосу,
#          блок и рыбу по форме и контрасту, настраивать нечего.
#  COLOR - поиск по ЗАДАННЫМ цветам. Для каждой детали миниигры
#          указан цвет и допуск:
#            Target line - цвет самой рыбы (вертикальной черты);
#            Arrow       - цвет стрелки-указателя, запасной признак рыбы;
#            Left bar    - цвет ЛЕВОГО края твоего блока;
#            Right bar   - цвет ПРАВОГО края блока.
#          Пиксель "подходит", если каждый его канал R, G и B отличается
#          от заданного не больше допуска: допуск 0 - точное совпадение,
#          10-20 - прощает сглаживание и лёгкие тени. "None" - деталь не
#          ищем. Столбец считается деталью, если подходящих пикселей в
#          нём хотя бы COLOR_MIN_ROWS по высоте (случайный пиксель мира не
#          пройдёт). Блок - от первого столбца цвета Left bar до
#          последнего цвета Right bar в самом длинном сплошном куске;
#          рыба - середина самого крупного куска цвета Target line, а если
#          её не видно - Arrow.
#          Плюсы: быстро и предсказуемо. Минус: у удочки с градиентом
#          (crowbar) или меняющимся цветом (Ruinous) один цвет не
#          описывает блок - там лучше LINE.
# ------------------------------------------------------------------
COLOR_MIN_ROWS = 3
_COLOR_FISH = None


def hex_bgr(h):
    """'#rrggbb' -> (b, g, r) или None для 'none'/пусто/ошибки."""
    h = str(h or "").strip().lower()
    if len(h) != 7 or h[0] != "#":
        return None
    try:
        r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    except ValueError:
        return None
    return (b, g, r)


def color_mask(a16, hexcol, tol):
    c = hex_bgr(hexcol)
    if c is None:
        return None
    t = int(tol)
    m = np.abs(a16[:, :, 0] - c[0]) <= t
    m &= np.abs(a16[:, :, 1] - c[1]) <= t
    m &= np.abs(a16[:, :, 2] - c[2]) <= t
    return m


def _runs(cols_ok, gap):
    """Сплошные куски True (разрывы до gap склеиваем): [(начало, конец)]."""
    idx = np.where(cols_ok)[0]
    if len(idx) == 0:
        return []
    cut = np.where(np.diff(idx) > gap)[0]
    starts = np.r_[idx[0], idx[cut + 1]]
    ends = np.r_[idx[cut], idx[-1]] + 1
    return list(zip(starts.tolist(), ends.tolist()))


def find_bar_color(rgb):
    """Блок и рыба по цветам удочки. Та же выдача, что у find_bar;
    рыбу кладём в _COLOR_FISH (её отдаст find_fish)."""
    global _COLOR_FISH, _V2
    _V2 = None
    _COLOR_FISH = None
    lo, hi = BAR_LO, BAR_HI
    # каждая вторая строка: полоса высотой ~30 строк, этого хватает с
    # запасом, а считать вдвое меньше
    a = rgb[::2, lo:hi, :3].astype(np.int16)
    memo = {}

    def mask(col, tol):
        key = (str(col).lower(), int(tol))
        if key not in memo:
            memo[key] = color_mask(a, col, tol)
        return memo[key]
    ml = mask(COL_LEFT, TOL_LEFT)
    mr = mask(COL_RIGHT, TOL_RIGHT)
    if ml is None and mr is None:
        return None
    mb = ml if mr is None else (mr if ml is None else (ml | mr))
    MINR = max(1, (COLOR_MIN_ROWS + 1) // 2)       # строки через одну
    colcnt = mb.sum(axis=0)
    runs = _runs(colcnt >= MINR, 12)
    if not runs:
        BAR_TRACKER.last_band = None
        return None
    s_, e_ = max(runs, key=lambda r: r[1] - r[0])
    if e_ - s_ < 8:
        return None
    left, right = s_, e_
    if ml is not None:
        cl = np.where(ml[:, s_:e_].sum(axis=0) >= MINR)[0]
        if len(cl):
            left = s_ + int(cl[0])
    if mr is not None:
        cr = np.where(mr[:, s_:e_].sum(axis=0) >= MINR)[0]
        if len(cr):
            right = s_ + int(cr[-1]) + 1
    rows = np.where(mb[:, s_:e_].any(axis=1))[0]
    top, bot = int(rows[0]) * 2, int(rows[-1]) * 2 + 2
    BAR_TRACKER.last_band = (top, bot)
    for col, tol in ((COL_TARGET, TOL_TARGET), (COL_ARROW, TOL_ARROW)):
        m = mask(col, tol)
        if m is None:
            continue
        fr = _runs(m.sum(axis=0) >= MINR, 2)
        fr = [r for r in fr if r[1] - r[0] <= FISH_MAX_W + 6]
        if fr:
            f0, f1 = max(fr, key=lambda r: r[1] - r[0])
            _COLOR_FISH = lo + (f0 + f1) // 2
            break
    row = (top + bot) // 2
    return lo + left, lo + right, row, 128, top


def find_bar(brightness, sat=None, rgb=None, why=None):
    if TRACK_STYLE == "color" and rgb is not None:
        return find_bar_color(rgb)
    return find_bar_line(brightness, sat, rgb, why)


def find_bar_line(brightness, sat=None, rgb=None, why=None):
    """Полоса и блок - детектор 6.x с памятью между кадрами.

    Детекторы 1.x и 2.x (запасные пути) удалены в 7.0: с версии 6.0 они
    не включались, но их код, настройки и ветки мешали искать ошибки."""
    global _V2
    if rgb is None:
        return None
    r = BAR_TRACKER.step(rgb)
    if r is None:
        _V2 = None
        if why is not None:
            why.append("полосы на экране нет")
        return None
    if FISH_MODEL_ON:
        m = FISH_MODEL.match_block(r["prof6"], r["left"], r["right"],
                                   r["lo"], r["hi"])
        if m is not None:
            r["left"], r["right"] = m
    _V2 = (r["prof"], r["lo"], r["hi"], r["left"], r["right"])
    globals()["_V6"] = r["prof6"]
    row = (r["top"] + r["bot"]) // 2
    lvl = int(np.median(rgb[row][r["left"]:r["right"] + 1].max(axis=1)))
    return r["left"], r["right"], row, max(lvl, 8), r["top"]


def xp_cream(sct):
    """Доля кремовых пикселей (цифры уровня, полоска опыта) в зоне XP
    и сам снимок зоны. Цвет цифр ~(255,230,150): красный и зелёный
    высокие, синий заметно ниже."""
    try:
        z = np.array(sct.grab(XP_ZONE))[:, :, :3].astype(np.int16)   # BGR
    except Exception:
        return None, None
    B, G, R = z[:, :, 0], z[:, :, 1], z[:, :, 2]
    cream = (R > 215) & (G > 180) & (B < 200) & (R - B > 45)
    return float(cream.mean()), z


def card_change(sct, base):
    """Какая доля зоны карточки изменилась относительно пустой сцены."""
    if base is None:
        return None
    try:
        cur = np.array(sct.grab(CARD))[:, :, :3].astype(np.int16)
    except Exception:
        return None
    if cur.shape != base.shape:
        return None
    return float(np.mean(np.abs(cur - base).sum(axis=2) > CARD_DIFF))


def read_progress(img):
    """Доля заполнения полосы прогресса, 0..1, или None если не видно.

    Полоса состоит из яркой красной заливки слева и тёмного остатка
    справа. Считаем, где кончается заливка. Это единственный признак,
    по которому можно отличить поимку от срыва: сама миниигра в обоих
    случаях просто исчезает."""
    if img is None or img.size == 0:
        return None
    a = img[:, :, :3].astype(np.int16)
    b, g, r = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    red = (r - np.maximum(g, b)).mean(axis=0)
    lum = a.max(axis=2).mean(axis=0)
    filled = red > 45
    empty = (~filled) & (lum < 90)
    if filled.sum() + empty.sum() < 40:
        return None
    xs = np.where(filled | empty)[0]
    lo, hi = int(xs[0]), int(xs[-1])
    if hi - lo < 40:
        return None
    seg = filled[lo:hi + 1]
    return float(seg.mean())


def bar_report(brightness, sat=None, rgb=None):
    """Пошагово объясняет, что видит детектор."""
    out = []
    if rgb is not None:
        a = rgb[:, :, :3].astype(np.float32)
        top, bot = u_band_rows(a)
        pres = BAR_TRACKER.present(a, top, bot, BAR_LO + 5, BAR_HI - 5)
        out.append(f"строки {top}..{bot}, край полосы {pres:.0f} "
                   f"(есть полоса, если > {U_PRESENT:.0f})")
    why = []
    b = find_bar(brightness, sat, rgb, why)
    for w in why:
        out.append("  " + w)
    if b:
        left, right, row, lvl, top = b
        out.append(f"блок: {left + REGION['left']}..{right + REGION['left']} "
                   f"ширина {right - left}, строка {row}, уровень {lvl}")
        lum = rgb.max(axis=2) if rgb is not None else brightness
        f = find_fish(lum, row, left, right, lvl, None, top, rgb)
        out.append(f"рыба: {f + REGION['left'] if f is not None else 'НЕ НАЙДЕНА'}")
        if f is not None:
            out.append(f"ошибка: {f - (left + right) // 2:+d} пикс")
    else:
        out.append("блок: НЕ НАЙДЕН")
    return "\n".join(out)


LAST_METHOD = "нет"     # каким способом нашлась рыба в последнем кадре


def find_fish(brightness, row, left, right, peak, prev=None, top_row=None,
              rgb=None):
    """Рыба по профилю полосы (поиск 2.x) + модель "фон и блок" (6.4).

    Старые пути - над полосой, по цвету, сопровождением, сканом - удалены
    в 7.0. Они работали, только когда полосу нашёл старый детектор, а его
    больше нет. Честное "не вижу" лучше догадки."""
    global LAST_METHOD
    if TRACK_STYLE == "color":
        LAST_METHOD = "цвет" if _COLOR_FISH is not None else "нет"
        return _COLOR_FISH
    if _V2 is None or _V2[3] != left or _V2[4] != right:
        LAST_METHOD = "нет"
        return None
    f = find_fish_v2(prev)
    LAST_METHOD = ("полоса" if f is not None and not _EDGE_HIT
                   else "край" if f is not None else "нет")
    if FISH_MODEL_ON:
        f, by_model = FISH_MODEL.fuse(f, prev)
        if by_model:
            LAST_METHOD = "фон"
    return f


SCANCODES = {"enter": 0x1C, "\\": 0x2B, "space": 0x39, "e": 0x12}

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _PUL = ctypes.POINTER(ctypes.c_ulong)

    class _KBD(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", _PUL)]

    class _MOUSE(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", _PUL)]

    class _UNION(ctypes.Union):
        _fields_ = [("ki", _KBD), ("mi", _MOUSE), ("pad", ctypes.c_ubyte * 32)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    def _send_scan(code, up):
        flags = 0x0008 | (0x0002 if up else 0)      # SCANCODE | KEYUP
        inp = _INPUT(type=1, u=_UNION(ki=_KBD(0, code, flags, 0, None)))
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    SCAN_OK = True
except Exception:
    SCAN_OK = False


def wheel(clicks):
    """Колесо: clicks < 0 - как scroll_in (камера ближе), > 0 - дальше."""
    n = abs(int(clicks))
    step = -120 if clicks < 0 else 120
    for _ in range(n):
        if SCAN_OK:
            inp = _INPUT(type=0, u=_UNION(mi=_MOUSE(0, 0, step, 0x0800, 0, None)))
            _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        else:
            pyautogui.scroll(1 if step > 0 else -1)
        time.sleep(0.02)


def look(dy):
    """Наклон камеры: зажать ПКМ и провести мышью по вертикали на dy px
    (плюс - вниз). Кусками, чтобы Roblox не счёл это рывком."""
    n = max(1, abs(int(dy)) // 40)
    part = int(dy) / n
    if not SCAN_OK:
        pyautogui.mouseDown(button="right")
        for _ in range(n):
            pyautogui.moveRel(0, part)
        pyautogui.mouseUp(button="right")
        return

    def send(flags, mx=0, my=0):
        inp = _INPUT(type=0, u=_UNION(mi=_MOUSE(mx, my, 0, flags, 0, None)))
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    send(0x0008)                                  # ПКМ вниз
    time.sleep(0.03)
    acc = 0.0
    for _ in range(n):
        acc += part
        d = int(round(acc))
        acc -= d
        send(0x0001, 0, d)
        time.sleep(0.004)
    time.sleep(0.03)
    send(0x0010)                                  # ПКМ вверх


def scroll_in(times=None):
    """Колесо вниз - камера уезжает внутрь персонажа (вид от первого лица).
    Тогда свечение пассивок и сам персонаж не попадают в область захвата.
    Как и с клавишами, шлём через SendInput: pyautogui игры часто игнорируют."""
    n = FP_SCROLLS if times is None else times
    if not SCAN_OK:
        for _ in range(n):
            pyautogui.scroll(-1)
            time.sleep(0.02)
        return
    MOUSEEVENTF_WHEEL = 0x0800
    for _ in range(n):
        inp = _INPUT(type=0, u=_UNION(mi=_MOUSE(0, 0, -120,
                                                MOUSEEVENTF_WHEEL, 0, None)))
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        time.sleep(0.02)


def scan_code(name):
    """Скан-код клавиши: из таблицы, а для любого символа - у самой Windows
    (раскладка пользователя), чтобы NAV_KEY мог быть любой клавишей."""
    code = SCANCODES.get(name)
    if code or not SCAN_OK or len(name) != 1:
        return code
    try:
        vk = _user32.VkKeyScanW(ord(name)) & 0xFF
        return _user32.MapVirtualKeyW(vk, 0) or None
    except Exception:
        return None


def click_at(x, y, n=1):
    """Щелчок(и) левой кнопкой в точке экрана - через SendInput, как и
    клавиши: игры часто не замечают обычные синтетические щелчки."""
    x, y = int(x), int(y)
    if not SCAN_OK:
        pyautogui.click(x, y, clicks=max(1, n), interval=0.03)
        return
    W_ = _user32.GetSystemMetrics(0) or 1920
    H_ = _user32.GetSystemMetrics(1) or 1080
    ax, ay = round(x * 65535 / max(1, W_ - 1)), round(y * 65535 / max(1, H_ - 1))
    MOVE, ABS, DOWN, UP = 0x0001, 0x8000, 0x0002, 0x0004

    def send(flags, dx=0, dy=0):
        inp = _INPUT(type=0, u=_UNION(mi=_MOUSE(dx, dy, 0, flags, 0, None)))
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    # Roblox узнаёт, что курсор над кнопкой, только на следующем кадре
    # после движения. Сразу после прыжка курсора щелчок кнопка не видит,
    # поэтому: навести, шевельнуть на пиксель, подождать пару кадров.
    send(MOVE | ABS, ax, ay)
    time.sleep(0.03)
    send(MOVE, 1, 0)
    time.sleep(0.02)
    send(MOVE, -1, 0)
    time.sleep(0.03)
    for i in range(max(1, n)):
        send(DOWN)
        time.sleep(0.045)
        send(UP)
        if i + 1 < n:
            time.sleep(0.05)


def tap_key(name, hold=KEY_HOLD):
    """Нажатие с удержанием. Скан-коды - игры игнорируют обычные VK-события."""
    code = scan_code(name)
    if SCAN_OK and code:
        _send_scan(code, False)
        time.sleep(hold)
        _send_scan(code, True)
    else:
        pyautogui.keyDown(name)
        time.sleep(hold)
        pyautogui.keyUp(name)


CAST_STEPS = ["delay", "hold", "release", "zoom_out", "zoom_in", "look_down",
              "look_up", "perfect"]
ROD_STEPS = ["delay", "bag", "rod"]
CAST_STEP_VALUE = {"delay": 0.5, "zoom_out": 10, "zoom_in": 5, "look_down": 1000,
                   "look_up": 1000}


def parse_seq(text):
    """'delay:0.35;hold;...' -> [(шаг, число или None)]. Непонятное - пропускаем."""
    out = []
    for part in str(text or "").split(";"):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition(":")
        k = k.strip().lower()
        if k not in CAST_STEPS and k not in ROD_STEPS:
            continue
        if k in CAST_STEP_VALUE:
            try:
                val = float(v.replace(",", "."))
            except ValueError:
                val = CAST_STEP_VALUE[k]
            out.append((k, val))
        else:
            out.append((k, None))
    return out


def seq_text(seq):
    return ";".join(k if v is None else f"{k}:{v:g}" for k, v in seq)


class CastBar:
    """Полоска заброса. Замер по снимкам из игры:
      - наверху ЗЕЛЁНАЯ зона ~10 px (G 150-175, R 75-100, B 70-100);
      - под ней пустая часть - ПОЛУПРОЗРАЧНАЯ: на тёмной сцене почти
        чёрная (4-5, как и фон, и даже рамка - её там не отличить), на
        синем свечении бирюзовая (31,100,147). По ней полоску не узнать;
      - снизу БЕЛАЯ заливка (слева 172, справа 255) той же ширины, что и
        зелёное; ходит вверх-вниз, пока держишь кнопку.
    Поэтому полоска - это пара "зелёное пятно + под ним белый столбец ТОЙ
    ЖЕ ширины, стоящий на одном и том же низу". Низ запоминаем, когда
    заливку видно; когда она внизу и её не видно - доля 0."""

    MX, MY = 50, 40

    def __init__(self):
        self.roi = None               # (x, y, w, h) на экране
        self.bottom_off = None        # низ полоски относительно низа зелёного, px
        self.last_frac = 0.0
        self.fill_seen = True
        self.last_a = None            # последний кадр окна (для cast_N.png)

    def _green(self, a):
        t = PC_GREEN_TOL
        a = a.astype(np.int16)
        B, G, R = a[..., 0], a[..., 1], a[..., 2]
        return (G >= 90 - t) & (G - R >= 40 - t // 2) & (G - B >= 30 - t // 2)

    @staticmethod
    def _blobs(g, step=1):
        """Куски зелёного: [(x1, x2, y1, y2)] (в пикселях исходного кадра)."""
        ys, xs = np.nonzero(g)
        out = []
        if len(xs) == 0:
            return out
        # сначала - по столбцам: соседние столбцы с зелёным (разрыв до 6) -
        # один кусок. Раньше делили на полосы по 20 px, и зелёное шириной 27
        # на границе полосы разваливалось надвое.
        ux = np.unique(xs)
        cutx = np.nonzero(np.diff(ux) > 3)[0]
        groups = np.split(ux, cutx + 1)
        for gcols in groups:
            sel = np.nonzero((xs >= gcols[0]) & (xs <= gcols[-1]))[0]
            order = sel[np.argsort(ys[sel])]
            cut = np.nonzero(np.diff(ys[order]) > 6)[0]
            for part in np.split(order, cut + 1):
                if len(part) >= 3:
                    out.append((int(xs[part].min()) * step, int(xs[part].max()) * step + step - 1,
                                int(ys[part].min()) * step, int(ys[part].max()) * step + step - 1))
        return out

    def _column(self, a, x1, x2, y2):
        """Белый столбец под зелёным: (верх, низ) самого нижнего белого
        куска или None. Белое - по правой (светлой) половине ширины."""
        H, W = a.shape[:2]
        w = x2 - x1 + 1
        if not (6 <= w <= 60) or y2 + 20 >= H:
            return None
        # Белое - по СЕРЕДИНЕ правой половины, без крайних столбцов. В 8.4
        # брались столбцы до самого края зелёного: когда полоска стоит между
        # пикселями, зелёное на 1 px шире заливки, крайний столбец - тёмная
        # рамка, и строка "не белая". Заливку не видело в 2 кадрах из 3
        # (cast_2/cast_3: доля скачет 0 -> 0.5 -> 0), скорость выходила
        # 6000-10000 px/с, и отпускало невпопад.
        cx = (x1 + x2) // 2
        edge = max(3, w // 6)
        band = a[y2 + 1:, cx:max(cx + 2, x2 - edge + 1)].min(axis=2)
        band = np.percentile(band, 25, axis=1) if band.shape[1] > 2 else band.min(axis=1)
        white = band >= 200 - PC_WHITE_TOL
        idx = np.nonzero(white)[0]
        if len(idx) < 12:
            return None
        # куски белого сверху вниз; заливка - ПЕРВЫЙ длинный кусок под
        # зелёным (ниже полоски бывает белый текст интерфейса - не он)
        cut = np.nonzero(np.diff(idx) > 3)[0]
        segs = [(int(p[0]), int(p[-1]) + 1) for p in np.split(idx, cut + 1)]
        first = next((i for i, (a_, b_) in enumerate(segs) if b_ - a_ >= 12), None)
        if first is None:
            return None
        start, end = segs[first]
        for a_, b_ in segs[first + 1:]:
            if a_ - end > 10:
                break
            end = b_
        # ширина белого - как у зелёного, а не белая стена: по краям не белое
        # "белое" здесь - по НАИМЕНЬШЕМУ каналу: синее свечение за полоской
        # яркое (223), но не белое (синий фон 48,152,223)
        # ширина белого в средней строке: сплошной кусок вокруг середины
        mid = y2 + 1 + (start + end) // 2
        wh = a[mid].min(axis=1).astype(np.int16)
        if wh[cx] < 140:
            return None
        l_, r_ = cx, cx
        while l_ > 0 and wh[l_ - 1] >= 140 and cx - l_ < 120:
            l_ -= 1
        while r_ < len(wh) - 1 and wh[r_ + 1] >= 140 and r_ - cx < 120:
            r_ += 1
        fw = r_ - l_ + 1
        if not (0.5 * w <= fw <= w + 8):
            return None                           # уже зелёного или белая стена
        return y2 + 1 + start, y2 + 1 + end

    def locate(self, sct, zone=None, dbg=None, a=None):
        """Найти полоску (нужна видимая заливка). dbg - список (x, y, длина
        белого или 0, 0) по каждому зелёному куску - для cast_miss.png."""
        z = zone or CAST_ZONE
        if a is None:
            a = np.asarray(sct.grab(z))[:, :, :3]
        best = None
        for bx1, bx2, by1, by2 in self._blobs(self._green(a[::2, ::2]), 2):
            if by2 - by1 > 50:
                continue
            ys0, xs0 = max(0, by1 - 2), max(0, bx1 - 3)
            sub = self._green(a[ys0:by2 + 3, xs0:bx2 + 4])
            ys, xs = np.nonzero(sub)
            if len(xs) < 6:
                continue
            x1, x2 = xs0 + int(xs.min()), xs0 + int(xs.max())
            y1, y2 = ys0 + int(ys.min()), ys0 + int(ys.max())
            col = self._column(a, x1, x2, y2)
            if dbg is not None and len(dbg) < 60:
                dbg.append((z["left"] + (x1 + x2) // 2, z["top"] + y1,
                            (col[1] - col[0]) if col else 0, 0))
            if col and 80 <= col[1] - y2 <= 1000:
                if best is None or col[1] - col[0] > best[0]:
                    best = (col[1] - col[0], x1, x2, y1, y2, col[1])
        if best is None:
            return False
        _n, x1, x2, y1, y2, bot = best
        self.bottom_off = bot - y2
        cx = (x1 + x2) // 2
        self.roi = (z["left"] + cx - self.MX, z["top"] + y1 - self.MY,
                    2 * self.MX, (bot - y1) + 2 * self.MY)
        return True

    def read(self, sct):
        """(доля пути до зелёного, y верха заливки, y низа зелёного, y верха
        зелёного) - экранные; None - полоски (зелёного) не видно."""
        x, y, w, h = self.roi
        x, y = max(0, x), max(0, y)
        a = np.asarray(sct.grab({"left": x, "top": y, "width": w, "height": h}))[:, :, :3]
        self.last_a = a
        blobs = [b_ for b_ in self._blobs(self._green(a)) if b_[3] - b_[2] <= 40
                 and 6 <= b_[1] - b_[0] <= 60]
        if not blobs:
            return None
        x1, x2, y1, y2 = min(blobs, key=lambda b_: abs((b_[0] + b_[1]) / 2 - w / 2))
        bot = y2 + (self.bottom_off or 0)
        col = self._column(a, x1, x2, y2)
        if col is not None and abs(col[1] - bot) <= 8:
            top = col[0]
            bot = col[1]
            self.bottom_off = bot - y2
        elif col is not None and self.bottom_off is None:
            top, bot = col
            self.bottom_off = bot - y2
        else:
            top = bot                         # заливка внизу (или не видна)
        frac = (bot - top) / max(1, bot - y2 - 1)
        # Заливку не увидели, а кадр назад она была высоко - это сбой чтения,
        # а не "упала до нуля": помечаем, чтобы такой кадр не портил скорость
        self.fill_seen = col is not None or self.last_frac < 0.12
        if self.fill_seen:
            self.last_frac = frac
        # окно едет за полоской, размер - прежний
        cx = x + (x1 + x2) // 2
        self.roi = (cx - self.MX, y + y1 - self.MY, w, h)
        return frac, y + top, y + y2 + 1, y + y1


CAST_BAR = CastBar()
PC_SAVE = 3                 # столько первых идеальных забросов записываем для разбора
pc_saved = 0


def _save_cast_trace(rows, cast_frames, released_at):
    """cast_N.csv (время, доля, верх заливки, скорость) и cast_N.png - кадры
    полоски в ряд, кадр отпускания в красной рамке."""
    global pc_saved
    if not DEBUG_MODE or pc_saved >= PC_SAVE or not rows:
        return
    pc_saved += 1
    try:
        with open(os.path.join(APP_DIR, f"cast_{pc_saved}.csv"), "w", encoding="utf-8") as fh:
            fh.write("t,доля,верх,зелёное,скорость,отпуск\n")
            for r in rows:
                fh.write(",".join(str(v) for v in r) + "\n")
        from PIL import Image, ImageDraw
        fr = cast_frames[-40:]
        if fr:
            hmax = max(f.shape[0] for f in fr)
            wsum = sum(f.shape[1] + 4 for f in fr)
            im = Image.new("RGB", (wsum, hmax), (40, 0, 0))
            x = 0
            for i, f in enumerate(fr):
                im.paste(Image.fromarray(np.ascontiguousarray(f[:, :, ::-1])), (x, 0))
                if released_at is not None and i == len(fr) - 1:
                    ImageDraw.Draw(im).rectangle((x, 0, x + f.shape[1] - 1, f.shape[0] - 1),
                                                 outline=(255, 0, 0), width=2)
                x += f.shape[1] + 4
            im.save(os.path.join(APP_DIR, f"cast_{pc_saved}.png"))
        say(f"    сохранил cast_{pc_saved}.png / .csv - как шла полоска заброса")
    except Exception as e:
        say(f"    не смог сохранить разбор заброса: {e}")


def perfect_release(sct):
    """Держим ЛКМ (уже зажата) и отпускаем, чтобы заливка ВСТАЛА В ЗЕЛЁНОМ.

    Заливка ходит вверх-вниз. Отпускаем, когда она на подъёме КАСАЕТСЯ
    зелёного - или раньше на PC_EARLY_MS: пока отпускание дойдёт до игры,
    она успевает подняться. PC_EARLY_MS макрос учит сам по итогу каждого
    заброса: не дотянула - отпускать позже; уже шла обратно вниз -
    раньше. Скорость у каждого заброса своя - её меряем на ходу, и время
    "до касания" считаем из неё, а не из подобранных процентов."""
    global PC_EARLY_MS
    t0 = time.perf_counter()
    hist, rows, cast_frames = [], [], []
    have = False
    dt_avg = 0.02
    t_last_read = None
    seen_rise = False       # видели, как заливка поднимается (а не застали её наверху)
    while True:
        now = time.perf_counter()
        if not running or should_exit:
            set_hold(False)
            return False
        if now - t0 > PC_FAIL:
            set_hold(False)
            say(f"    perfect: полоску {'потерял' if have else 'не нашёл'} за "
                f"{PC_FAIL:g} с - отпустил (полная сила)")
            _save_cast_trace(rows, cast_frames, None)
            return False
        r = CAST_BAR.read(sct) if CAST_BAR.roi is not None else None
        if r is None:
            found = False
            if CAST_BAR.roi is not None:
                x, y, w, h = CAST_BAR.roi
                near = {"left": max(0, x - 80), "top": max(0, y - 80),
                        "width": w + 160, "height": h + 160}
                found = CAST_BAR.locate(sct, near)
            if not found:
                found = CAST_BAR.locate(sct)
            if not found and not have and not cast_miss_saved and now - t0 > 0.5:
                found = _cast_miss_shot(sct, t0)
            if found:
                r = CAST_BAR.read(sct)
        if r is None:
            time.sleep(0.005)
            continue
        have = True
        now = time.perf_counter()
        if t_last_read is not None:
            dt_avg = 0.8 * dt_avg + 0.2 * (now - t_last_read)
        t_last_read = now
        frac, top, gbot, gtop = r
        if not CAST_BAR.fill_seen:
            time.sleep(0.002)
            continue                              # кадр без заливки - пропуск
        hist.append((now, top))
        hist = [h_ for h_ in hist if now - h_[0] < 0.1][-5:]
        v = 0.0
        if len(hist) >= 2 and hist[-1][0] > hist[0][0]:
            tt = np.array([h_[0] for h_ in hist])
            yy = np.array([h_[1] for h_ in hist], float)
            v = -float(np.polyfit(tt - tt[0], yy, 1)[0])      # px/с, + вверх
            if len(hist) < 3 or abs(v) > 4000:
                v = 0.0          # 2 точки или скачок - скорость ещё не знаем
        if DEBUG_MODE and pc_saved < PC_SAVE and CAST_BAR.last_a is not None:
            cast_frames.append(CAST_BAR.last_a.copy())
        dist = top - gbot                          # px до касания зелёного
        wait = None
        if v > 150 and dist > 2:
            seen_rise = True
        if PC_STYLE == "velocity":
            pct = PC_BFALL if v <= 50 else next(p for lim, p in PC_BANDS if v < lim)
            go = frac >= 1.0 or frac * 100 >= pct
        elif dist <= 2:
            # в зелёном. Если застали её уже наверху (не видели подъёма) -
            # неизвестно, сколько она там стоит: этот круг пропускаем
            go = seen_rise
        elif v > 150:
            t_touch = dist / v                     # через сколько коснётся
            lead = PC_EARLY_MS / 1000.0
            # следующий кадр будет через dt_avg - если касание раньше него,
            # дождёмся точного момента сном, а не следующего кадра
            go = t_touch - lead <= dt_avg
            wait = max(0.0, t_touch - lead) if go else None
        else:
            go = False                             # стоит внизу или опускается
        rows.append((round(now - t0, 4), round(frac, 3), top, gbot, round(v), int(go)))
        if go:
            if wait:
                time.sleep(wait)
            set_hold(False)
            res = _watch_stop(sct, gbot)
            msg = (f"    perfect: отпустил за {dist:.0f} px до зелёного "
                   f"(скорость {v:.0f} px/с, раньше на {PC_EARLY_MS:.0f} мс)")
            if res is not None:
                peak, final = res
                came_back = final > peak + 6       # дошла докуда-то и пошла вниз
                reached = peak <= gbot + 8
                if came_back:
                    late, early = True, False
                    msg += f" -> ПОЗДНО: пошла обратно вниз на {final - peak} px"
                elif not reached:
                    late, early = False, True
                    msg += f" -> РАНО: не дотянула {peak - gbot} px"
                else:
                    late = early = False
                    msg += " -> встала В ЗЕЛЁНОМ"
                if PC_STYLE == "green" and PC_AUTOLAT and (late or early):
                    vv = max(300.0, v)
                    if late:
                        step_ms = min(30.0, max(8.0, 0.5 * (final - peak) / vv * 1000))
                    else:
                        step_ms = -min(30.0, max(5.0, 0.5 * (peak - gbot) / vv * 1000))
                    new_ms = min(200.0, max(0.0, PC_EARLY_MS + step_ms))
                    if round(new_ms) != PC_EARLY_MS:
                        msg += f"; раньше на {PC_EARLY_MS:.0f} -> {new_ms:.0f} мс"
                        PC_EARLY_MS = int(round(new_ms))
                        try:
                            ini_write({"PC_EARLY_MS": str(PC_EARLY_MS)})
                        except Exception:
                            pass
            else:
                msg += " (где встала - не видно)"
            say(msg)
            _save_cast_trace(rows, cast_frames, True)
            return True
        time.sleep(0.002)


cast_miss_saved = False


def _cast_miss_shot(sct, t_hold):
    """Полоску не видно: снимок ВСЕГО экрана с зоной поиска (красная рамка)
    и всем зелёным, что нашлось (кружки: длина столбца под ним). Заодно ищем
    полоску по всему экрану - вдруг она просто вне зоны."""
    global cast_miss_saved
    try:
        mon = sct.monitors[1]
        full = np.asarray(sct.grab(mon))[:, :, :3]
        dbg = []
        whole = {"left": mon["left"], "top": mon["top"], "width": mon["width"],
                 "height": mon["height"]}
        found = CAST_BAR.locate(sct, whole, dbg, a=full)
        from PIL import Image, ImageDraw
        im = Image.fromarray(np.ascontiguousarray(full[:, :, ::-1]))
        d = ImageDraw.Draw(im)
        z = CAST_ZONE
        d.rectangle((z["left"], z["top"], z["left"] + z["width"], z["top"] + z["height"]),
                    outline=(255, 40, 60), width=3)
        for x, y, run, n in dbg:
            col = (0, 255, 0) if run > 0 else (255, 200, 0)
            d.ellipse((x - 14, y - 14, x + 14, y + 14), outline=col, width=3)
            d.text((x + 16, y - 8), f"{run}", fill=col)
        if DEBUG_MODE:
            im.save(os.path.join(APP_DIR, "cast_miss.png"))
        cast_miss_saved = True
        where = ""
        if found and CAST_BAR.roi is not None:
            rx, ry, _w, _h = CAST_BAR.roi
            inz = (z["left"] <= rx + 45 <= z["left"] + z["width"]
                   and z["top"] <= ry <= z["top"] + z["height"])
            where = (f"; полоска нашлась в {rx + 45},{ry + 70}"
                     + ("" if inz else " - ВНЕ зоны поиска: поправь зону (F2, Cast bar box)"))
        say(f"    perfect: полоски не видно {time.perf_counter() - t_hold:.1f} с"
            + (" - сохранил cast_miss.png" if DEBUG_MODE else "")
            + f" (зелёных пятен на экране: {len(dbg)}{where})")
        return found
    except Exception as e:
        cast_miss_saved = True
        say(f"    perfect: снимок cast_miss не удался: {e}")
        return False


def _watch_stop(sct, gbot):
    """После отпускания 0.3 с смотрим на заливку: (самая высокая точка,
    где стоит в конце) в экранных y. Если белое закрыло зелёное (зелёного
    не видно, а заливка была у самого зелёного) - считаем, что дошла.
    None - полоска сразу исчезла."""
    t0 = time.perf_counter()
    peak = final = None
    while time.perf_counter() - t0 < 0.3:
        r = CAST_BAR.read(sct) if CAST_BAR.roi is not None else None
        if r is None:
            if final is not None and final <= gbot + 12:
                peak = final = min(peak, gbot)
            break
        top = r[1]
        peak = top if peak is None else min(peak, top)
        final = top
        time.sleep(0.004)
    return None if peak is None else (peak, final)

def run_rod_select():
    """Рюкзак -> удочка (шаги из ROD_SEQ), перед забросом."""
    for kind, val in parse_seq(ROD_SEQ):
        if not running or should_exit:
            return
        if kind == "delay":
            time.sleep(max(0.0, val))
        elif kind == "bag":
            tap_key(BAG_KEY)
        elif kind == "rod":
            tap_key(ROD_KEY)


def run_cast(sct):
    """Выполнить последовательность заброса выбранного способа."""
    seq = parse_seq(CAST_SEQ_PERFECT if CAST_STYLE == "perfect" else CAST_SEQ_NORMAL)
    if not seq:
        seq = parse_seq("delay:0.35;hold;delay:0.9;release")
    for kind, val in seq:
        if not running or should_exit:
            break
        if kind == "delay":
            time.sleep(max(0.0, val))
        elif kind == "hold":
            set_hold(True)
        elif kind == "release":
            set_hold(False)
        elif kind == "zoom_out":
            wheel(+val)
        elif kind == "zoom_in":
            wheel(-val)
        elif kind == "look_down":
            look(+val)
        elif kind == "look_up":
            look(-val)
        elif kind == "perfect":
            if not holding:
                set_hold(True)
            perfect_release(sct)
    set_hold(False)
    # perfect двигает камеру (отъезжает) - вернём вид от первого лица,
    # если он включён и не мешает щелчкам по shake
    if CAST_STYLE == "perfect" and FIRST_PERSON and not shake_clicks_mode():
        time.sleep(0.1)
        scroll_in()


def shake_clicks_mode():
    return SHAKE_OLD.get(SHAKE_MODE, SHAKE_MODE) in ("pixel", "circle")


class ShakeScanner:
    """Ищет кнопки shake в SHAKE_ZONE.

    Как выглядит кнопка (два снимка из игры):
      - обычная: ПОЛУПРОЗРАЧНЫЙ тёмный круг радиусом ~60 px, по нему тонкое
        (1 px) светло-серое кольцо радиусом ~56 px, внутри белая надпись
        SHAKE (254-255);
      - выбранная навигацией: то же, но с толстым (~6 px) синим кольцом
        радиусом ~66 px (синий 255, зелёный 145-220, красный 85-125).
    Общее у обеих - белая надпись и КОЛЬЦО вокруг неё на одном радиусе.
    Кольцо проверяем "лучами": из центра надписи 48 лучей, на каждом
    ищем пиксели кольца (светло-серый или синий) на расстоянии 30-100 px.
    У кнопки почти все лучи находят их на ОДНОМ радиусе; у текста в мире,
    снега, подписей над игроками - нет."""

    STEP = 2
    WIN = 48          # полуокно вокруг белого пикселя, в шагах (96 px)
    MIN_WHITE = 12    # без проверки кольца: меньше белых точек - соринка
    ANG = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    RAD = np.arange(30, 101)

    def __init__(self):
        self.recent = []
        self.last = None             # последний снимок зоны (для отладки)

    def reset(self):
        self.recent.clear()

    def _grab(self, sct):
        full = np.asarray(sct.grab(SHAKE_ZONE))[:, :, :3]
        if SKIP_RECTS:
            full = full.copy()
            for x1, y1, x2, y2 in SKIP_RECTS:
                ax1 = max(0, x1 - SHAKE_ZONE["left"])
                ay1 = max(0, y1 - SHAKE_ZONE["top"])
                ax2 = x2 - SHAKE_ZONE["left"] + 1
                ay2 = y2 - SHAKE_ZONE["top"] + 1
                if ax2 > 0 and ay2 > 0:
                    full[ay1:ay2, ax1:ax2] = 0
        self.full = full
        a = full[::self.STEP, ::self.STEP]
        self.last = a
        return a

    # Повторное нажатие. В 8.6 и раньше нажатое место "запрещалось" на
    # SHAKE_DUP секунд. Если следующая кнопка появлялась на ТОМ ЖЕ месте,
    # её пропускали целую секунду - и на деле макрос жал 3-5 раз за заброс.
    # Теперь место свободно, как только кнопка с него ИСЧЕЗЛА (значит,
    # нажатие прошло); а если она так и висит SHAKE_DUP секунд - жмём ещё
    # раз (значит, щелчок не дошёл).
    def _same(self, x, y, r):
        rad = max(SHAKE_DIST, 30)
        return (x - r[1]) ** 2 + (y - r[2]) ** 2 <= rad * rad

    def _seen(self, now, buttons):
        """Отметить нажатые кнопки, которых на экране больше нет."""
        self.recent = [r for r in self.recent if now - r[0] < 3.0]
        for r in self.recent:
            if not any(self._same(bx, by, r) for bx, by in buttons):
                r[3] = True                       # исчезла

    def _fresh(self, now, x, y):
        return not any(self._same(x, y, r) and not r[3] and now - r[0] < SHAKE_DUP
                       for r in self.recent)

    def remember(self, now, x, y):
        self.recent.append([now, x, y, False])

    @staticmethod
    def white(a, tol=None):
        t = SHAKE_TOL if tol is None else tol
        return (a >= 255 - t).all(axis=2)

    @staticmethod
    def ring(a):
        """Пиксели кольца (BGR): синее кольцо выбранной кнопки ИЛИ
        светло-серое тонкое кольцо обычной."""
        a = a.astype(np.int16)
        B, G, R = a[..., 0], a[..., 1], a[..., 2]
        mx, mn = a.max(axis=-1), a.min(axis=-1)
        blue = (B >= 200) & (G >= 110) & (R <= 170) & (B - R >= 80)
        gray = (mx >= 150) & (mx - mn <= 40)
        return blue | gray

    def ring_around(self, fx, fy):
        """(да/нет, уточнённый центр) - есть ли кольцо вокруг точки. Если
        середина белого сдвинута (рядом другой белый текст, и пятна слились),
        кольцо "с этой точки" выглядит неровным. Тогда пробуем центры рядом
        (до 12 px) - но только если кольцо хоть отчасти видно (доля 0.3+),
        чтобы не тратить время на всё белое подряд."""
        ok, c, score = self._ring_at(fx, fy)
        if ok or score < 0.3:
            return ok, c
        best = (score, ok, c)
        for dx, dy in ((12, 0), (-12, 0), (0, 12), (0, -12),
                       (8, 8), (-8, 8), (8, -8), (-8, -8)):
            ok2, c2, sc2 = self._ring_at(fx + dx, fy + dy)
            if ok2:
                return True, c2
            best = max(best, (sc2, ok2, c2), key=lambda b: b[0])
        return False, (fx, fy)

    def _ring_at(self, fx, fy):
        """(да/нет, центр по кольцу, доля лучей на общем радиусе)."""
        full = self.full
        H, W = full.shape[:2]
        rad = np.arange(self.RAD[0] - 3, self.RAD[-1] + 4)          # с запасом для соседей
        X = (fx + np.cos(self.ANG)[:, None] * rad[None, :]).round().astype(int)
        Y = (fy + np.sin(self.ANG)[:, None] * rad[None, :]).round().astype(int)
        inside_all = (X >= 0) & (X < W) & (Y >= 0) & (Y < H)
        col = np.zeros(X.shape + (3,), np.int16)
        col[inside_all] = full[Y[inside_all], X[inside_all]]
        lum = col.max(axis=2)
        sat = lum - col.min(axis=2)
        B, G, R = col[..., 0], col[..., 1], col[..., 2]
        blue = (B >= 200) & (G >= 110) & (R <= 170) & (B - R >= 80)
        # Тонкое кольцо обычной кнопки - полупрозрачное белое: его яркость
        # зависит от фона (замер 128-161), поэтому ищем не цвет, а СВЕТЛУЮ
        # ЛИНИЮ - пиксель заметно ярче соседей на 3 px дальше и ближе.
        line = np.zeros_like(blue)
        line[:, 3:-3] = ((lum[:, 3:-3] - (lum[:, :-6] + lum[:, 6:]) / 2 >= 30)
                         & (sat[:, 3:-3] <= 50))
        hit = (blue | line)[:, 3:-3]
        inside = inside_all[:, 3:-3]
        X, Y = X[:, 3:-3], Y[:, 3:-3]
        # надпись белая и тоже "кольцевого" цвета - её не считаем: лучи,
        # идущие через текст, дают попадания на малом радиусе, а кольцо -
        # общий для ВСЕХ лучей радиус. Ищем радиус, где попадают больше всего
        # лучей (с допуском +-3 px).
        # край зоны обрезает часть лучей - считаем долю среди тех, что
        # целиком внутри (кнопка у края зоны тоже должна находиться)
        per_r = np.zeros(len(self.RAD))
        for i in range(len(self.RAD)):
            sl = slice(max(0, i - 3), i + 4)
            valid = inside[:, sl].all(axis=1)
            if valid.sum() >= 24:
                per_r[i] = hit[valid, sl].any(axis=1).mean()
        i = int(np.argmax(per_r))
        if per_r[i] < 0.6:
            return False, (fx, fy), float(per_r[i])
        rays = hit[:, max(0, i - 3):i + 4]
        ok = rays.any(axis=1)
        # центр по точкам кольца на этих лучах
        j = max(0, i - 3) + rays.argmax(axis=1)
        px = X[np.arange(len(self.ANG)), j][ok].astype(float)
        py = Y[np.arange(len(self.ANG)), j][ok].astype(float)
        # центр - подгонкой окружности по точкам кольца (а не средним: если
        # с одной стороны часть лучей промахнулась, среднее уезжает вбок)
        A = np.c_[2 * px, 2 * py, np.ones_like(px)]
        sol, *_ = np.linalg.lstsq(A, px ** 2 + py ** 2, rcond=None)
        cx, cy = float(sol[0]), float(sol[1])
        if np.hypot(cx - fx, cy - fy) > 40:
            cx, cy = fx, fy                          # подгонка ушла - берём надпись
        return True, (cx, cy), float(per_r[i])

    def _candidates(self, a):
        """Середины белых пятен (надписи SHAKE) по ВСЕЙ зоне, от центра к краям.

        В 8.6 и раньше перебирались только 1200 белых точек, ближайших к
        центру: если у центра было что-то большое и белое (одежда, снег,
        луна, надпись), запас кончался на нём, и кнопка у края не
        находилась вовсе. Теперь белое собирается в пятна (клетки 6x6 px),
        огромные пятна (больше надписи) отбрасываются, проверяются все."""
        m = self.white(a)
        n = int(m.sum())
        if n == 0:
            return [], 0
        B = 3                                     # 3 шага по 2 px = клетка 6 px
        h, w = m.shape[0] // B * B, m.shape[1] // B * B
        g = m[:h, :w].reshape(h // B, B, w // B, B).any(axis=(1, 3))
        seen = np.zeros_like(g)
        gh, gw = g.shape
        blobs = []
        for y0, x0 in zip(*np.nonzero(g)):
            if seen[y0, x0]:
                continue
            stack, cells, big = [(y0, x0)], [], False
            seen[y0, x0] = True
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                if len(cells) > 600:
                    big = True                    # белая стена, не надпись
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < gh and 0 <= xx < gw and g[yy, xx] and not seen[yy, xx]:
                            seen[yy, xx] = True
                            stack.append((yy, xx))
            if big:
                continue
            c = np.array(cells)
            bh = (c[:, 0].max() - c[:, 0].min() + 1) * B * self.STEP
            bw = (c[:, 1].max() - c[:, 1].min() + 1) * B * self.STEP
            if bw > 240 or bh > 120:
                continue                          # больше надписи на кнопке
            blobs.append((c[:, 0].mean() * B + 1, c[:, 1].mean() * B + 1))
        cy0, cx0 = m.shape[0] / 2, m.shape[1] / 2
        blobs.sort(key=lambda p: (p[1] - cx0) ** 2 + (p[0] - cy0) ** 2)
        need = 5 if SHAKE_VERIFY or SHAKE_MODE == "circle" else self.MIN_WHITE
        out = []
        for fy, fx in blobs[:60]:
            ok = True
            for _k in range(3):                   # к середине белого вокруг
                y1, y2 = max(0, int(fy) - self.WIN), int(fy) + self.WIN
                x1, x2 = max(0, int(fx) - self.WIN), int(fx) + self.WIN
                wy, wx = np.nonzero(m[y1:y2, x1:x2])
                if len(wx) < need:
                    ok = False
                    break
                fx, fy = x1 + wx.mean(), y1 + wy.mean()
            if ok and not any(abs(fx * self.STEP - ox) < 20 and abs(fy * self.STEP - oy) < 20
                              for ox, oy in out):
                out.append((fx * self.STEP, fy * self.STEP))     # полное разрешение
        return out, n

    def find_pixel(self, sct, now):
        """Белая надпись, ближайшая к центру зоны; щелчок в её середину.
        С SHAKE_VERIFY - только если вокруг есть кольцо кнопки."""
        a = self._grab(sct)
        cands, n = self._candidates(a)
        buttons = []
        for fx, fy in cands:
            if SHAKE_VERIFY and not self.ring_around(fx, fy)[0]:
                continue
            buttons.append((SHAKE_ZONE["left"] + fx, SHAKE_ZONE["top"] + fy))
        self._seen(now, buttons)
        for X, Y in buttons:
            if self._fresh(now, X, Y):
                return (int(X), int(Y)), n
        return None, n

    def any_white(self, sct):
        a = self._grab(sct)
        cands, _n = self._candidates(a)
        return sum(1 for fx, fy in cands[:4] if self.ring_around(fx, fy)[0]) * 100

    def find_circles(self, sct, now):
        """Круги кнопок: кольцо, найденное лучами вокруг надписи; щелчок -
        в центр КОЛЬЦА (он точнее середины надписи)."""
        a = self._grab(sct)
        cands, _n = self._candidates(a)
        buttons = []
        for fx, fy in cands:
            ok, (cx, cy) = self.ring_around(fx, fy)
            if ok:
                buttons.append((SHAKE_ZONE["left"] + cx, SHAKE_ZONE["top"] + cy))
        self._seen(now, buttons)
        return [(int(X), int(Y), 0) for X, Y in buttons if self._fresh(now, X, Y)]


SHAKER = ShakeScanner()


def shake_diagnose(sct, name="shake_test.png"):
    """Прогнать поиск shake по экрану СЕЙЧАС: что нашёл, какие белые пятна
    видел и сколько лучей нашли кольцо. Снимок зоны с пометками."""
    try:
        from PIL import Image, ImageDraw
        a = SHAKER._grab(sct)
        cands, n = SHAKER._candidates(a)
        im = Image.fromarray(np.ascontiguousarray(SHAKER.full[:, :, ::-1]))
        d = ImageDraw.Draw(im)
        lines = []
        for fx, fy in cands[:8]:
            ok, (cx, cy) = SHAKER.ring_around(fx, fy)
            col = (0, 255, 0) if ok else (255, 60, 60)
            d.ellipse((fx - 6, fy - 6, fx + 6, fy + 6), outline=col, width=2)
            if ok:
                d.ellipse((cx - 56, cy - 56, cx + 56, cy + 56), outline=col, width=2)
            lines.append(f"({int(fx + SHAKE_ZONE['left'])},{int(fy + SHAKE_ZONE['top'])})"
                         f"{' кольцо' if ok else ' без кольца'}")
        im.save(os.path.join(APP_DIR, name))
        say(f"проверка shake: белых точек {n}, пятен {len(cands)}: "
            + (", ".join(lines) if lines else "нет") + f" - снимок {name}")
    except Exception as e:
        say(f"проверка shake не удалась: {e}")


def save_shake_shot(name, pt):
    """Снимок зоны поиска shake (последний, что видел поиск): с крестом в
    точке щелчка, если она есть."""
    try:
        from PIL import Image, ImageDraw
        a = getattr(SHAKER, "full", None)
        if a is None:
            return
        im = Image.fromarray(np.ascontiguousarray(a[:, :, ::-1]))
        if pt is not None:
            d = ImageDraw.Draw(im)
            x = pt[0] - SHAKE_ZONE["left"]
            y = pt[1] - SHAKE_ZONE["top"]
            d.line((x - 12, y, x + 12, y), fill=(0, 255, 0), width=2)
            d.line((x, y - 12, x, y + 12), fill=(0, 255, 0), width=2)
        im.save(os.path.join(APP_DIR, name))
    except Exception:
        pass


import threading
from collections import deque

STATE = {"state": "cast", "left": None, "right": None, "fish": None,
         "error": 0, "duty": 0.5, "fps": 0, "conf": 0.0, "catches": 0,
         "caught": 0, "lost": 0,
         "runtime": 0.0, "peak": 0, "width": 0, "msg": ""}
STATE_LOCK = threading.Lock()
seen_hist = deque()          # (время, найдена_ли рыба) за последнюю секунду
fight_hist = deque(maxlen=2000)   # (время, рыба, лево, право, скважность, центр)
trace = []                   # покадровая траектория для анализа физики


def confidence(now):
    """Доля кадров с найденной рыбой за последнюю секунду.
    Низкая уверенность = не гнать блок уверенно по мусорным данным."""
    while seen_hist and now - seen_hist[0][0] > CONF_WIN:
        seen_hist.popleft()
    if len(seen_hist) < 5:
        return 1.0
    return sum(v for _, v in seen_hist) / len(seen_hist)


def flush_trace():
    """Сбросить накопленную траекторию в файл."""
    if not trace:
        return
    try:
        new = not os.path.exists(TRACE_PATH)
        with open(TRACE_PATH, "a", newline="", encoding="utf-8") as tf:
            tw = csv.writer(tf)
            if new:
                tw.writerow(["рыба_№", "t", "рыба", "центр",
                             "лево", "право", "скв", "жмём", "метод"])
            tw.writerows(trace)
        n = len(trace)
        trace.clear()
        return n
    except Exception as e:
        trace.clear()
        print(f"    не смог записать trace.csv: {e}")
        return 0


def save_region(img, name):
    """Сохранить кадр области крупно - по нему видно, что спутал детектор."""
    try:
        from PIL import Image
        pic = np.ascontiguousarray(img[:, :, :3][:, :, ::-1])
        Image.fromarray(pic).resize(
            (REGION["width"] * 2, REGION["height"] * 4),
            Image.NEAREST).save(os.path.join(APP_DIR, name))
        return True
    except Exception as e:
        print(f"    не смог сохранить {name}: {e}")
        return False


def render_bar(left, right, fish):
    """Схема полосы: = блок, + его центр, I рыба, . пусто."""
    cells = ["."] * VIEW_W

    def pos(x):
        return max(0, min(VIEW_W - 1, int(x * VIEW_W / REGION["width"])))

    if left is not None:
        a, b = pos(left), pos(right)
        for i in range(a, b + 1):
            cells[i] = "="
        cells[(a + b) // 2] = "+"
    if fish is not None:
        cells[pos(fish)] = "I"
    return "".join(cells)


LOG_LINES = deque(maxlen=300)   # то же самое, но для окна
LOG_SEQ = 0


def say(msg):
    """Сообщение идёт И в консоль, И в окно.

    Раньше было только print(). В оконной сборке консоли нет, поэтому
    вся диагностика - какой метод нашёл рыбу, разброс ширины блока,
    доля кадров со скважностью в упоре, сохранённые снимки - уходила
    в никуда. Снаружи это выглядело как "строка не появилась"."""
    global LOG_SEQ
    print(("\r" + " " * (VIEW_W + 34) + "\r" if SHOW_VIEW else "") + msg)
    stamp = time.strftime("%H:%M:%S")
    for line in str(msg).rstrip().split("\n"):
        LOG_LINES.append(line)
        # Пишем журнал и в файл. Из окна текст не выделяется мышью, и
        # человеку приходилось слать скриншот вместо строк.
        try:
            with open(os.path.join(APP_DIR, "scarlet.log"), "a",
                      encoding="utf-8") as fh:
                fh.write(f"{stamp}  {line}\n")
        except OSError:
            pass
    LOG_SEQ += 1


HOLD_LOCK = threading.Lock()


HOLD_REFRESH = _cfg("HOLD_REFRESH", 0.25)   # 0 - не подтверждать нажатие
t_hold_change = 0.0
hold_refreshes = 0


def set_hold(want):
    # hold_changes считает РЕАЛЬНЫЕ переключения кнопки. Он нужен, чтобы
    # отличить две совершенно разные беды: "мы сами не нажимаем" (поток
    # ШИМ умер, счётчик стоит) и "мы нажимаем, а игра не принимает"
    # (счётчик растёт, а блок не двигается).
    global holding, hold_changes, hold_error
    with HOLD_LOCK:
        try:
            if want and not holding:
                pyautogui.mouseDown()
                holding = True
                hold_changes += 1
                globals()["t_hold_change"] = time.perf_counter()
            elif not want and holding:
                pyautogui.mouseUp()
                holding = False
                hold_changes += 1
                globals()["t_hold_change"] = time.perf_counter()
        except Exception as e:
            hold_error = repr(e)


def pwm_alive():
    return any(t.name == "ШИМ" and t.is_alive()
               for t in threading.enumerate())


def pwm_loop():
    """ШИМ живёт отдельно от детекции.

    Раньше кнопку переключал главный цикл - то есть раз в кадр, каждые
    17 мс. При периоде ШИМ 75 мс это всего 4 ступени на период:
    скважность 0.50 и 0.70 превращались в одно и то же нажатие.
    Здесь шаг 2 мс - около 37 ступеней, и заданная скважность наконец
    отрабатывается такой, какой её посчитал регулятор."""
    while not should_exit:
        try:
            if pwm_on:
                per = PERIOD if PERIOD > 0.004 else 0.075
                phase = (time.perf_counter() % per) / per
                set_hold(phase < duty)
                # Скважность в упоре (0 или 1) - это ОДНО нажатие на всю
                # серию. Если игра его упустила, дальше событий нет вовсе:
                # в бою 5 на crowbar кнопка "была зажата" 7 секунд, а блок
                # уехал влево и стоял у стенки, пока рыба не ушла. Поэтому
                # в упоре раз в HOLD_REFRESH повторяем то же состояние:
                # лишнее нажатие поверх нажатия игре безвредно, а
                # потерянное оно восстанавливает.
                now_ = time.perf_counter()
                if HOLD_REFRESH > 0 and now_ - t_hold_change > HOLD_REFRESH:
                    with HOLD_LOCK:
                        if holding:
                            pyautogui.mouseDown()
                        else:
                            pyautogui.mouseUp()
                    globals()["t_hold_change"] = now_
                    globals()["hold_refreshes"] += 1
        except Exception as e:
            # молча умерший поток ШИМ выглядел бы как "игра не принимает"
            globals()["hold_error"] = repr(e)
        time.sleep(0.002)


CSV_HEAD = ["№", "секунд", "кадров", "рыба_видна_%", "средняя_ошибка",
            "макс_ошибка", "к/с", "раскачка", "исход"]


def write_row(row):
    """Строка на КАЖДЫЙ бой, с исходом. Раньше исхода в файле не было,
    и сорвавшиеся бои выглядели в нём так же, как пойманные."""
    new = not os.path.exists(CSV_PATH)
    if not new:
        try:
            with open(CSV_PATH, encoding="utf-8-sig") as f:
                head = f.readline()
            if "исход" not in head:
                # старый файл без столбца исхода - откладываем, не смешиваем
                old = os.path.join(APP_DIR, "catches_old.csv")
                if os.path.exists(old):
                    os.remove(old)
                os.rename(CSV_PATH, old)
                new = True
        except OSError:
            pass
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CSV_HEAD)
        w.writerow(row)


listener = keyboard.Listener(on_press=on_press)
listener.start()
print("=" * 58)
print(f"  {APP_NAME} {VERSION}  -  помощник рыбалки   |   F8 чтобы начать")
print("=" * 58)
print(f"{key_label(KEY_START)} - старт/пауза | {key_label(KEY_DIAG)} - диагностика | "
      f"{key_label(KEY_SNAP)} - снимок | {key_label(KEY_CALIB)} - КАЛИБРОВКА | "
      f"{key_label(KEY_EXIT)} - выход")
print(f"Регулятор: сглаживание цели AIM_TAU "
      f"{'ВКЛ ' + str(AIM_TAU) + 'с, компенсация ' + str(AIM_LEAD) if AIM_TAU > 0 else 'ВЫКЛ'}"
      f" | GAIN {GAIN} | LEAD {LEAD} | ШИМ отдельным потоком")
print("Аварийно: мышь в левый верхний угол")
print(f"Статистика пишется в {CSV_PATH}")
print("Скан-коды:", "включены" if SCAN_OK else "НЕДОСТУПНЫ (не Windows?)")
print("Профиль удочки:", PROFILE if PROFILE else "общий (не задан)",
      f"[{len(_PROF)} своих настроек]" if _PROF else "")
print(f"Запись траектории: {'первые ' + str(TRACE_CATCHES) + ' рыб -> trace.csv'
      if TRACE_CATCHES else 'выключена'}")

state = "cast"
t_state = time.perf_counter()
t_shake = 0.0
t_shake_scan = 0.0      # когда последний раз искали кнопки shake
t_loop = 0.0            # начало прошлого кадра (для темпа по этапам)
t_bite_chk = 0.0        # когда последний раз проверяли поклёвку
t_shake_seen = 0.0      # когда последний раз видели кнопку shake
shake_armed = False     # поиск shake хоть раз сработал в этой сессии
shake_shots = 0         # сохранено снимков найденных кнопок
shake_miss_saved = False
bar_streak = 0
t_bar_lost = 0.0
t_nofish = 0.0
auto_diag = False
trim_warned = False
catches = 0
timeouts = 0
shakes = 0

prev_fish = None
prev_error = 0.0
prev_err_valid = 0
jump_block = 0
t_fish_lost = 0.0
prev_lum = None
t_flash = -99.0
blinded = False
bites_saved = 0
fails_saved = 0
prog_hist = []
prog_saved = 0
after_saved = 0
card_saved = 0
card_base = None
card_best = 0.0
card_best_t = None
card_t_last = 0.0
xp_base = None          # (доля, снимок) зоны XP во время боя
xp_best = 0.0
xp_best_t = None
caught = 0
lost = 0
unknown = 0
width_hist = deque(maxlen=200)
mid_img = None          # кадр области из середины боя
odd_saved = 0           # сколько сохранено странных боёв
session_w = []          # ширина блока в обычных боях этой сессии
meth_count = {}
speed_avg = 0.0
aim_pos = None         # сглаженная позиция рыбы
aim_vel = 0.0
sat_frames = 0
activity = 0.0
trim = 0.0
duty = 0.5
error = 0
gain_term = damp_term = 0.0
adapt = 1.0

st_frames = st_seen = st_err_sum = st_err_max = st_flips = 0
prev_err_sign = 0

frames = 0
t_report = time.perf_counter()
t_view = time.perf_counter()
t_session = time.perf_counter()
conf = 1.0
t_prev = time.perf_counter()
frames_sec = 0


def reset_control():
    global prev_fish, jump_block, t_fish_lost, speed_avg, duty, error
    global aim_pos, aim_vel
    global prev_error, prev_err_valid
    global prev_lum, t_flash, blinded
    global t_nofish, t_bar_lost
    global card_best, card_best_t, card_t_last, xp_best, xp_best_t, xp_base
    # Итог карточки - СВОЙ У КАЖДОГО БОЯ. С версии 4.8 максимум зоны и
    # момент появления не обнулялись ни разу: после первой поимки в сессии
    # максимум навсегда оставался выше порога, и все бои дальше были
    # "взята" - отсюда одинаковые "зона 68%, появилась через 202 мс" у
    # десятка боёв подряд и сорвавшаяся рыба в пойманных.
    card_best = 0.0
    card_best_t = None
    card_t_last = 0.0
    xp_best = 0.0
    xp_best_t = None
    xp_base = None
    # ГЛАВНОЕ: таймеры "рыбы не видно" и "блока не видно" ОБЯЗАНЫ обнуляться
    # между рыбами. Иначе секундомер, запущенный в конце прошлой миниигры,
    # продолжает тикать в паузе между забросами, и следующая миниигра
    # обрывается на первом же кадре без рыбы.
    t_nofish = 0.0
    t_bar_lost = 0.0
    BAR_TRACKER.reset()       # новая рыба - память о блоке учится заново
    fight_hist.clear()
    FISH_MODEL.reset()        # и фон полосы тоже: место могло смениться
    prev_error = 0.0
    prev_err_valid = 0
    prev_fish = None
    jump_block = 0
    t_fish_lost = 0.0
    speed_avg = 0.0
    aim_pos = None
    aim_vel = 0.0
    duty = BASE_NEUTRAL + trim
    error = 0


def control_loop():
    global state, t_state, t_shake, bar_streak, t_bar_lost, auto_diag
    global t_shake_scan, t_shake_seen, shake_armed, shake_shots, shake_miss_saved
    global t_loop, t_bite_chk
    global trim_warned, catches, timeouts, shakes, prev_fish, prev_error
    global prev_err_valid, jump_block, t_fish_lost, bites_saved
    global speed_avg, activity, trim, duty, error, gain_term
    global aim_pos, aim_vel, sat_frames
    global conf, t_session, fp_pending
    global damp_term, adapt, st_frames, st_seen, st_err_sum, st_err_max
    global st_flips, prev_err_sign, frames, t_report, t_view, t_prev
    global frames_sec, running, should_exit, nav_pending, diagnose
    global prev_lum, t_flash, blinded

    global t_nofish, fails_saved, meth_count, snap_pending, snaps, pwm_on
    global calib_pending, calib_t0, calib_rows, calib_ready, calib_hold0
    global burst_pending, burst, bursts, burst_t0, burst_n
    global caught, lost, unknown, prog_hist, prog_saved
    global after_saved, card_base, card_best, card_best_t, card_t_last, card_saved
    global xp_base, xp_best, xp_best_t
    global mid_img, odd_saved, session_w

    try:
        frame_errors = 0
        while not should_exit:
            # Один сбойный кадр не должен убивать сессию. Раньше любое
            # исключение внутри цикла гасило рабочий поток, окно молча
            # закрывалось, и у человека оставался только error.log.
            # Теперь ошибка записывается, мышь отпускается, всё
            # начинается с заброса - а работа продолжается.
            try:
                with Capture() as sct:
                    while not should_exit:
                        if not running:
                            pwm_on = False
                            set_hold(False)
                            reset_control()
                            state = "cast"
                            # Серия (F3) пишется и НА ПАУЗЕ. Удочки, которые макрос ещё
                            # не видит, человек ловит руками, а макрос в это время
                            # должен стоять и не мешать - только записывать экран.
                            if burst_pending:
                                burst_pending = False
                                burst, burst_n, burst_t0 = [], 0, time.perf_counter()
                                say(f">>> пишу {BURST_SEC:.0f}с кадров области (на паузе)")
                            if burst is not None:
                                _t = time.perf_counter() - burst_t0
                                try:
                                    burst.append((_t, np.array(sct.grab(REGION))[:, :, :3].copy()))
                                except Exception:
                                    pass
                                if _t > BURST_SEC:
                                    bursts += 1
                                    path = os.path.join(APP_DIR, f"burst_{bursts}.npz")
                                    try:
                                        np.savez_compressed(
                                            path, t=np.array([b[0] for b in burst], np.float32),
                                            frames=np.stack([b[1] for b in burst]))
                                        say(f">>> burst_{bursts}.npz: {len(burst)} кадров, "
                                            f"{os.path.getsize(path) / 1e6:.1f} МБ")
                                    except Exception as e:
                                        say(f">>> не смог записать серию: {e}")
                                    burst = None
                                time.sleep(0.06)
                                continue
                            time.sleep(0.1)
                            continue

                        if fp_pending:
                            fp_pending = False
                            time.sleep(0.5)
                            scroll_in()
                            say(">>> камера заведена внутрь персонажа")

                        if nav_pending:
                            nav_pending = False
                            time.sleep(0.4)
                            tap_key(NAV_KEY)
                            time.sleep(0.3)
                            say(f">>> навигация интерфейса: нажал {NAV_KEY}")

                        now = time.perf_counter()

                        # ---------- ЗАБРОС ----------
                        if state == "cast":
                            pwm_on = False
                            set_hold(False)
                            _f = focus_game()
                            if _f is False:
                                say("    НЕ СМОГ вернуть фокус Roblox - кликни по игре,"
                                    " иначе заброс не дойдёт")
                            elif _f == "switched":
                                say("    вернул фокус окну Roblox (он был у другого окна)")
                            if AUTO_ROD:
                                run_rod_select()
                            run_cast(sct)
                            say(f"[{catches}] заброс ({CAST_STYLE})")
                            with STATE_LOCK:
                                STATE.update(state="wait", left=None, right=None,
                                             fish=None, error=0, width=0)
                            state = "wait"
                            t_state = time.perf_counter()
                            t_shake = t_state
                            SHAKER.reset()
                            bar_streak = 0
                            auto_diag = False
                            t_prev = time.perf_counter()
                            continue

                        # Темп кадров по этапу: в бою - FISH_FPS, в ожидании - чаще
                        # из проверки поклёвки и поиска shake (каждый из них
                        # дальше сам себя ограничивает своей частотой).
                        _sm = SHAKE_OLD.get(SHAKE_MODE, SHAKE_MODE)
                        _cap = FISH_FPS if state == "reel" else max(
                            CAST_FPS, SHAKE_FPS if _sm != "disabled" else 0)
                        if _cap > 0:
                            _w = 1.0 / _cap - (time.perf_counter() - t_loop)
                            if _w > 0:
                                time.sleep(_w)
                        t_loop = time.perf_counter()
                        bite_due = (state != "wait" or CAST_FPS <= 0
                                    or t_loop - t_bite_chk >= 1.0 / CAST_FPS)
                        if state == "wait" and bite_due:
                            t_bite_chk = t_loop
                        img = np.array(sct.grab(REGION))
                        # min по каналам: у белого блока он высокий, у насыщенной зелёнки - нулевой.
                        # с max(R,G,B) зелёный фон считался частью блока и рвал измерения.
                        rgb = img[:, :, :3]
                        # Детектор 6.x сам работает с цветным кадром. Раньше здесь
                        # на каждом кадре считались яркость, мин-канал и
                        # насыщенность ВСЕЙ области для детекторов 1.x/2.x - их
                        # давно нет, а 2-3 мс на кадр оставались (7.4).
                        brightness = sat = lum = None
                        # Резкая вспышка (например, прок cryogenic) на миг заливает
                        # экран светом: цвет рамки и блока меняются, и детектор врёт.
                        # Пережидаем её, удерживая последнюю скважность.
                        # Яркость меряем ВНЕ строк полосы. Настоящая вспышка
                        # заливает светом весь экран, а появление полосы меняет
                        # только её строки - но белый блок так поднимал среднюю
                        # яркость (скачок 55 при пороге 26), что макрос первые
                        # 0.45с боя "пережидал вспышку" и не вёл рыбу.
                        _b = BAR_TRACKER.last_band
                        _H = rgb.shape[0]
                        if _b is not None:
                            _rows = np.r_[0:max(0, _b[0] - 6), min(_H, _b[1] + 6):_H]
                        else:
                            _rows = np.arange(0, min(18, _H))
                        # яркость только тех строк, по которым судим о вспышке,
                        # и через каждый второй столбец - для среднего хватает
                        _src = rgb[_rows, ::2] if len(_rows) else rgb[:, ::2]
                        mean_lum = float(_src.max(axis=2).mean())
                        # Вспышку учитываем ТОЛЬКО во время вываживания. В ожидании
                        # поклёвки она ни на что не влияет, но таймер от неё тикал
                        # дальше и ослеплял первые 0.45с боя: на белой удочке так
                        # было перед каждой поклёвкой - появление полосы с огромным
                        # белым блоком само по себе давало скачок яркости.
                        if (state == "reel" and prev_lum is not None
                                and abs(mean_lum - prev_lum) > FLASH_JUMP):
                            t_flash = now
                            say("    вспышка на экране - пережидаю")
                        prev_lum = mean_lum
                        blinded = now - t_flash < FLASH_HOLD

                        bar = find_bar(brightness, sat, rgb) if bite_due else None

                        # Серия кадров (F3). По одиночным снимкам нельзя научить детектор
                        # удочкам, у которых блок упирается в край полосы: по одному кадру
                        # не понять, какой из двух кусков блок. Нужна память между
                        # кадрами, а проверить её можно только на настоящей серии.
                        if burst_pending:
                            burst_pending = False
                            burst = []
                            burst_n = 0
                            burst_t0 = now
                            say(f">>> пишу {BURST_SEC:.0f}с кадров области (состояние: {state})")
                        if burst is not None:
                            burst_n += 1
                            if burst_n % BURST_EVERY == 0:
                                burst.append((now - burst_t0, img[:, :, :3].copy()))
                            if now - burst_t0 > BURST_SEC:
                                bursts += 1
                                path = os.path.join(APP_DIR, f"burst_{bursts}.npz")
                                try:
                                    np.savez_compressed(
                                        path, t=np.array([b[0] for b in burst], np.float32),
                                        frames=np.stack([b[1] for b in burst]))
                                    mb = os.path.getsize(path) / 1e6
                                    say(f">>> burst_{bursts}.npz: {len(burst)} кадров, {mb:.1f} МБ")
                                except Exception as e:
                                    say(f">>> не смог записать серию: {e}")
                                burst = None
                        if snap_pending:
                            snap_pending = False
                            snaps += 1
                            save_region(img, f"snap_{snaps}.png")
                            try:
                                with mss.mss() as shot:
                                    shot.shot(output=os.path.join(
                                        APP_DIR, f"snap_{snaps}_full.png"))
                            except Exception:
                                pass
                            say(f">>> snap_{snaps}.png и snap_{snaps}_full.png сохранены "
                                f"(состояние: {state})")
                            say(bar_report(brightness, sat, rgb))
                            shake_diagnose(sct, f"snap_{snaps}_shake.png")

                        if diagnose:
                            diagnose = False
                            print(f"\n--- ДИАГНОСТИКА ({state}) ---")
                            print(bar_report(brightness, sat, rgb))
                            print(f"активность {activity:.0f} | нейтраль {0.5 + trim:.3f}\n")

                        # ---------- ОЖИДАНИЕ ПОКЛЁВКИ ----------
                        if state == "wait":
                            if card_base is None and now - t_state > 1.2:
                                # Эталон: как выглядит зона карточки на пустой сцене.
                                try:
                                    card_base = np.array(sct.grab(CARD))[:, :, :3].astype(np.int16)
                                except Exception:
                                    card_base = None
                            if bite_due and bar is not None and find_fish(
                                    brightness, bar[2], bar[0], bar[1],
                                    bar[3], None, bar[4]) is not None:
                                bar_streak += 1
                                if bar_streak >= BAR_CONFIRM:
                                    set_hold(False)
                                    state = "reel"
                                    t_state = time.perf_counter()
                                    t_prev = time.perf_counter()
                                    t_bar_lost = 0.0
                                    st_frames = st_seen = st_err_sum = st_err_max = st_flips = 0
                                    prev_err_sign = 0
                                    meth_count = {}
                                    sat_frames = 0
                                    width_hist.clear()
                                    mid_img = None
                                    if AB_VALUES:
                                        ab_now = AB_VALUES[
                                            catches % len(AB_VALUES)]
                                        globals()["GAIN"] = ab_now
                                        say(f"    сравнение: GAIN {ab_now}")
                                    if calib_pending:
                                        calib_pending = False
                                        calib_t0 = -1.0
                                        calib_ready = 0
                                        calib_rows = []
                                        say(f"КАЛИБРОВКА: {CALIB_PLAN[-1][0]:.1f}с жму по "
                                            f"расписанию, рыбу не веду")
                                    reset_control()
                                    timeouts = 0
                                    if DEBUG_MODE and bites_saved < SAVE_BITES:
                                        bites_saved += 1
                                        if save_region(img, f"bite_{bites_saved}.png"):
                                            say(f"    сохранил bite_{bites_saved}.png")
                                    say(f"[{catches}] клюнуло (shake: {shakes})")
                                    shakes = 0
                                time.sleep(0.01)
                                continue

                            if bite_due:
                                bar_streak = 0
                            mode = SHAKE_OLD.get(SHAKE_MODE, SHAKE_MODE)
                            after_grace = now - t_state > CAST_GRACE
                            if (mode == "navigation" and not holding and after_grace
                                    and now - t_shake > SHAKE_INTERVAL):
                                t_shake = now
                                shakes += 1
                                tap_key(NAV_PRESS)
                            # поиск кнопок shake - не чаще SHAKE_FPS раз в секунду
                            if (mode in ("pixel", "circle", "navigation") and after_grace
                                    and not holding
                                    and now - t_shake_scan >= 1.0 / max(1, SHAKE_FPS)):
                                t_shake_scan = now
                                try:
                                    if mode == "pixel":
                                        pt, _nw = SHAKER.find_pixel(sct, now)
                                        hits = [pt] if pt else []
                                    elif mode == "circle":
                                        hits = [(x, y) for x, y, _w in
                                                SHAKER.find_circles(sct, now)[:1]]
                                    else:
                                        hits = []
                                        if SHAKER.any_white(sct) >= 20:
                                            t_shake_seen = now
                                            shake_armed = True
                                    for x, y in hits:
                                        if DEBUG_MODE and shake_shots < 2:
                                            shake_shots += 1
                                            save_shake_shot(f"shake_found_{shake_shots}.png",
                                                            (x, y))
                                        click_at(x, y, SHAKE_CLICKS)
                                        SHAKER.remember(now, x, y)
                                        shakes += 1
                                        t_shake_seen = now
                                        if not shake_armed:
                                            shake_armed = True
                                            say(f"    shake найден ({mode}) в {x},{y}")
                                    # долго ничего не находим - один снимок зоны
                                    # на сессию: по нему видно, что не так
                                    if (not hits and mode in ("pixel", "circle")
                                            and DEBUG_MODE and not shake_miss_saved
                                            and t_shake_seen < t_state
                                            and now - t_state > CAST_GRACE + 4.0):
                                        shake_miss_saved = True
                                        save_shake_shot("shake_miss.png", None)
                                        say("    shake не находится 4 с - сохранил "
                                            "shake_miss.png (что видит поиск)")
                                except Exception as e:
                                    say(f"    поиск shake: {e}")
                            # Заброс не удался: shake так и не появился. Считаем так,
                            # только если в этой сессии shake уже хоть раз находили -
                            # иначе при неподобранном цвете макрос перезабрасывал бы
                            # без конца, а рыба на самом деле клевала бы.
                            if (mode != "disabled" and SHAKE_FAIL > 0 and shake_armed
                                    and t_shake_seen < t_state
                                    and now - t_state > CAST_GRACE + SHAKE_FAIL):
                                say(f"[{catches}] shake не появился за {SHAKE_FAIL:.0f} с - "
                                    f"заброс не удался, закидываю снова")
                                shakes = 0
                                set_hold(False)
                                time.sleep(0.4)
                                state = "cast"
                                time.sleep(0.01)
                                continue

                            if now - t_state > BITE_TIMEOUT * 0.6 and not auto_diag:
                                auto_diag = True
                                print(f"\n--- долго нет поклёвки, проверяю детекцию ---")
                                print(bar_report(brightness, sat, rgb))
                                print()

                            if now - t_state > BITE_TIMEOUT:
                                timeouts += 1
                                say(f"[{catches}] таймаут ({timeouts}/{MAX_TIMEOUTS}), "
                                    f"shake: {shakes}")
                                shakes = 0

                                # КЛИКА ЗДЕСЬ БЫТЬ НЕ ДОЛЖНО. Короткий клик по свободной
                                # удочке - это слабый заброс, а следующий обычный заброс
                                # зажимает кнопку на 0.9с, и по уже заброшенной удочке это
                                # СМАТЫВАНИЕ. Клик закидывал поплавок у берега, удержание
                                # сматывало его обратно, удочка снова пустая - и через 35с
                                # опять таймаут. Бесконечный цикл: "закидывает, но не ловит".
                                # Без клика худший случай - один лишний заброс: если удочка
                                # была в воде, удержание её смотает, и следующий заброс
                                # уже пойдёт со свободной.
                                set_hold(False)
                                time.sleep(0.8)

                                if timeouts == 1:
                                    try:
                                        with mss.mss() as shot:
                                            shot.shot(output=os.path.join(APP_DIR,
                                                                         "stuck.png"))
                                        say("    сохранил stuck.png - видно, что на экране")
                                    except Exception:
                                        pass

                                if timeouts >= MAX_TIMEOUTS:
                                    set_hold(False)
                                    say("!!! Рыбалка не идёт. Смотри stuck.png")
                                    say("!!! Проверь: курсор на воде? удочка цела?")
                                    say("!!! Скрипт на паузе. F8 - продолжить.")
                                    running = False
                                    timeouts = 0
                                    continue
                                state = "cast"
                            time.sleep(0.01)
                            continue

                        # ---------- ВЫВАЖИВАНИЕ ----------
                        st_frames += 1
                        frames += 1
                        # Эталон пустой сцены для карточки обновляем ВНУТРИ миниигры.
                        # Снятый в ожидании поклёвки он иногда захватывал хвост
                        # карточки ПРЕДЫДУЩЕЙ рыбы: та висит 4-5 секунд, а следующая
                        # рыба нередко клюёт раньше. Потом новая карточка вставала
                        # ровно на то же место, зона "не менялась", и пойманная рыба
                        # записывалась в сорвавшиеся - так было с двумя из тринадцати.
                        # К середине миниигры старая карточка уже гарантированно ушла.
                        # Значок мыши "Click & Hold" частично заходит в зону, но это
                        # ~6% её площади - порог 35% он не пробивает.
                        # ТОЛЬКО пока полоса на экране. Раньше эталон снимался и в
                        # те 1.5 с после боя, когда полосы уже нет, а карточка УЖЕ
                        # есть: на card_1..4 верхняя половина ("во время боя")
                        # показывает ту же карточку, что и нижняя.
                        if (st_frames % 30 == 15 and now - t_state > 1.5
                                and not blinded and bar is not None):
                            try:
                                card_base = np.array(sct.grab(CARD))[:, :, :3].astype(np.int16)
                            except Exception:
                                pass
                            _xf, _xi = xp_cream(sct)
                            if _xf is not None:
                                xp_base = (_xf, _xi)
                        # Раз в 15 кадров, а не в 4: лишний захват экрана каждые 4 кадра
                        # опустил частоту с 60 до 48 к/с во всех боях. Прогресс теперь
                        # лишь запасной признак - главный карточка поимки.
                        if st_frames % 15 == 0:
                            # Полоса прогресса - отдельная область экрана.
                            # Копим ВСЮ историю: по одному последнему значению нельзя
                            # судить, потому что к моменту вывода миниигра уже кончилась
                            # и полоса могла обнулиться.
                            try:
                                _img = np.array(sct.grab(PROGRESS))
                                _p = read_progress(_img)
                            except Exception:
                                _img, _p = None, None
                            if _p is not None:
                                prog_hist.append(_p)
                            if (DEBUG_MODE and _img is not None and prog_saved < 2
                                    and st_frames % 60 == 0):
                                prog_saved += 1
                                if save_region(_img, f'progress_{prog_saved}.png'):
                                    say(f'    сохранил progress_{prog_saved}.png '
                                        f'(читаю как {"нет" if _p is None else int(_p*100)})')

                        if bar is None:
                            if t_bar_lost == 0.0:
                                t_bar_lost = now
                                pwm_on = False
                                # ОТПУСКАЕМ СРАЗУ. Раньше кнопка держалась ещё LOST_SEC
                                # секунд: если миниигра оборвалась в фазе "жмём", это
                                # полуторасекундное удержание игра принимала за ЗАБРОС.
                                # Удочка улетала в воду, а следующий заброс макроса
                                # вместо броска сматывал леску - и клёва больше не было.
                                set_hold(False)
                            # Карточку ловим ВСЁ время после исчезновения полосы, а не
                            # одним снимком: на двух рыбах из тринадцати одиночный снимок
                            # в фиксированный момент её не застал (зона изменилась на 0-2%),
                            # хотя рыба была поймана.
                            # Раньше тут стояло st_frames % 2 == 0, но номер кадра в этой
                            # ветке не меняется - замер шёл либо всегда, либо никогда.
                            _probe = now - card_t_last > 0.03
                            if _probe:
                                card_t_last = now
                                _xf, _xi = xp_cream(sct)
                                if _xf is not None:
                                    _gain = _xf - (xp_base[0] if xp_base else 0.0)
                                    xp_best = max(xp_best, _gain)
                                    if xp_best_t is None and _gain >= XP_FRAC:
                                        xp_best_t = now - t_bar_lost
                            if card_base is not None and _probe:
                                _c = card_change(sct, card_base)
                                if _c is not None:
                                    card_best = max(card_best, _c)
                                    # момент ПОЯВЛЕНИЯ - первое пересечение порога, а не пик:
                                    # пик приходится на конец анимации въезда карточки
                                    if card_best_t is None and _c >= CARD_FRAC:
                                        card_best_t = now - t_bar_lost
                            if now - t_bar_lost > LOST_SEC:
                                pwm_on = False
                                set_hold(False)
                                dur = time.perf_counter() - t_state
                                fps = st_frames / dur if dur > 0 else 0
                                seen_pct = 100 * st_seen / st_frames if st_frames else 0
                                avg_err = st_err_sum / st_seen if st_seen else 0

                                if seen_pct < 20 or (dur < MIN_CATCH_SEC
                                                 and seen_pct < MIN_CATCH_SEEN):
                                    if TRACE_ALL:
                                        flush_trace()
                                    else:
                                        trace.clear()
                                    if DEBUG_MODE and odd_saved < 4 and mid_img is not None:
                                        odd_saved += 1
                                        if save_region(mid_img, f"odd_false_{odd_saved}.png"):
                                            say(f"    сохранил odd_false_{odd_saved}.png - "
                                                f"что приняли за полосу")
                                    say(f"[{catches}] ложная поклёвка "
                                          f"({dur:.1f}с, видна {seen_pct:.0f}%) - пропускаю")
                                    time.sleep(1.0)
                                    state = "cast"
                                    continue

                                catches += 1
                                if DEBUG_MODE and catches <= TRACE_CATCHES:
                                    n = flush_trace()
                                    if n:
                                        say(f"    траектория записана ({n} кадров)")
                                else:
                                    trace.clear()
                                _row = [catches, f"{dur:.1f}", st_frames,
                                        f"{seen_pct:.0f}", f"{avg_err:.0f}",
                                        st_err_max, f"{fps:.0f}",
                                        f"{st_flips / dur if dur > 0 else 0:.2f}"]
                                # Раньше тут стояло "поймано за" - у КАЖДОГО боя, ещё
                                # до того, как известен исход. Сорвавшаяся рыба тоже
                                # выглядела пойманной.
                                say(f"[{catches}] бой окончен за {dur:.1f}с | "
                                      f"рыба видна {seen_pct:.0f}% | "
                                      f"ср.ошибка {avg_err:.0f} | {fps:.0f} к/с "
                                      f"| раскачка {st_flips / dur if dur > 0 else 0:.2f}/с")
                                if (right - left) > 200 and PROFILE == "tryhard":
                                    say(f"    !!! блок шириной {right - left} - это не "
                                        f"tryhard (там ~99). Проверь профиль в scarlet.ini")
                                if st_frames:
                                    say(f"    скважность в упоре "
                                        f"{100.0 * sat_frames / st_frames:.0f}% кадров "
                                        f"(чем меньше, тем лучше - в упоре регулятор "
                                        f"уже ничего не может добавить)")
                                # Исход миниигры виден только по полосе прогресса: на успехе
                                # она заполнена, на срыве пуста. Сама миниигра исчезает
                                # одинаково в обоих случаях, поэтому раньше счётчик
                                # "поймано" считал и сорвавшихся тоже.
                                # ГЛАВНЫЙ ПРИЗНАК - карточка с картинкой рыбы. Она появляется
                                # только при настоящей поимке. Прогресс же доходит до 80% и
                                # на рыбе, которая потом срывается, - по нему судить нельзя.
                                # 7.4: ГЛАВНЫЙ ПРИЗНАК - опыт (+xp и уровень сверху
                                # экрана): его даёт только поимка. Карточка - вторым
                                # мнением. Ждём, пока появится XP, или CARD_WAIT.
                                card_frac = None
                                card_extra = 0.0
                                _t0 = time.perf_counter()
                                while (xp_best < XP_FRAC
                                       and time.perf_counter() - _t0 < CARD_WAIT):
                                    _xf, _xi = xp_cream(sct)
                                    if _xf is not None:
                                        _gain = _xf - (xp_base[0] if xp_base else 0.0)
                                        xp_best = max(xp_best, _gain)
                                        if xp_best_t is None and _gain >= XP_FRAC:
                                            xp_best_t = time.perf_counter() - t_bar_lost
                                    if card_base is not None:
                                        _c = card_change(sct, card_base)
                                        if _c is not None:
                                            card_best = max(card_best, _c)
                                            if card_best_t is None and _c >= CARD_FRAC:
                                                card_best_t = time.perf_counter() - t_bar_lost
                                    time.sleep(0.05)
                                card_extra = time.perf_counter() - _t0
                                if card_base is not None:
                                    card_frac = card_best
                                _xp_seen = xp_best >= XP_FRAC
                                _card_base_img = card_base
                                card_base = None          # на следующий заброс снимем заново
                                # Где была рыба в последние 1.5 с боя. Поймать рыбу,
                                # которая всё это время вне блока, нельзя: прогресс в
                                # это время только убывает. В бою 5 на crowbar рыба
                                # 2 секунды до конца стояла в 250 px от блока, а
                                # карточка "показала" поимку.
                                _tail = [(f_, l_, r_) for (t_, f_, l_, r_, _d, _c) in fight_hist
                                         if t_bar_lost - 1.5 <= t_ <= t_bar_lost
                                         and f_ is not None]
                                _in = (sum(1 for f_, l_, r_ in _tail
                                           if l_ - 15 <= f_ <= r_ + 15) / len(_tail)
                                       if len(_tail) >= 20 else None)
                                _out_of_block = _in is not None and _in < 0.15
                                _card_yes = card_frac is not None and card_frac >= CARD_FRAC
                                _card_txt = ("карточка есть" if _card_yes else
                                             "карточки нет" if card_frac is not None else
                                             "карточку не проверить")
                                if _xp_seen:
                                    caught += 1
                                    verdict = (f"РЫБА ВЗЯТА - XP на экране (+{100 * xp_best:.0f}% "
                                               f"зоны, через {1000 * (xp_best_t or 0):.0f} мс; "
                                               f"{_card_txt})")
                                elif _card_yes:
                                    unknown += 1
                                    verdict = (f"исход неясен: карточка есть (зона "
                                               f"{100 * card_frac:.0f}%), а XP не видно "
                                               f"(+{100 * xp_best:.1f}%). Пришли xp_{catches}.png")
                                elif card_frac is not None or xp_base is not None:
                                    lost += 1
                                    verdict = (f"СОРВАЛАСЬ - ни XP, ни карточки за "
                                               f"{LOST_SEC + card_extra:.1f}с (XP +"
                                               f"{100 * xp_best:.1f}%, {_card_txt})")
                                elif not prog_hist:
                                    unknown += 1
                                    verdict = "исход неясен (полосы прогресса не видно)"
                                else:
                                    pmax = max(prog_hist)
                                    say(f"    прогресс за бой: начало {100*prog_hist[0]:.0f}%, "
                                        f"максимум {100*pmax:.0f}%, конец {100*prog_hist[-1]:.0f}%")
                                    if pmax >= 0.80:
                                        caught += 1
                                        verdict = f"РЫБА ВЗЯТА (прогресс доходил до {100*pmax:.0f}%)"
                                    elif pmax <= 0.35:
                                        lost += 1
                                        verdict = f"СОРВАЛАСЬ (прогресс не выше {100*pmax:.0f}%)"
                                    else:
                                        unknown += 1
                                        verdict = f"исход неясен (максимум {100*pmax:.0f}%)"
                                # Снимок ВСЕГО экрана сразу после миниигры. Карточка
                                # "You just caught a ..." появляется только при настоящей
                                # поимке, и это куда надёжнее прогресса: он доходит до 80%
                                # и на рыбе, которая потом срывается. Но чтобы её искать,
                                # нужно знать, где она на экране и чем отличается от
                                # надписей ивентов - за этим и снимки.
                                if DEBUG_MODE and after_saved < SAVE_AFTER:
                                    after_saved += 1
                                    try:
                                        mon = sct.monitors[1]
                                        shot = np.array(sct.grab(mon))
                                        from PIL import Image as _I
                                        _I.fromarray(np.ascontiguousarray(
                                            shot[:, :, :3][:, :, ::-1])).save(
                                            os.path.join(APP_DIR, f'after_{after_saved}.png'))
                                        say(f"    сохранил after_{after_saved}.png (весь экран после миниигры)")
                                    except Exception as e:
                                        say(f"    не смог снять экран: {e}")
                                say(f"    {verdict}")
                                if _in is not None and not _out_of_block:
                                    say(f"    в последние 1.5 с рыба в блоке {100 * _in:.0f}% времени")
                                # Зона карточки ДО и ПОСЛЕ - чтобы проверить вердикт
                                # глазами. Первые SAVE_AFTER боёв и каждый спорный.
                                if DEBUG_MODE and (card_saved < SAVE_AFTER
                                                   or verdict.startswith("исход неясен")):
                                    try:
                                        from PIL import Image as _I
                                        _now_img = np.array(sct.grab(CARD))[:, :, :3]
                                        _pair = [_now_img]
                                        if _card_base_img is not None:
                                            _pair.insert(0, np.clip(_card_base_img, 0, 255)
                                                         .astype(np.uint8))
                                        _I.fromarray(np.ascontiguousarray(
                                            np.vstack(_pair)[:, :, ::-1])).resize(
                                            (CARD["width"] * 2, CARD["height"] * 2 * len(_pair)),
                                            _I.NEAREST).save(
                                            os.path.join(APP_DIR, f"card_{catches}.png"))
                                        card_saved += 1
                                        say(f"    сохранил card_{catches}.png "
                                            f"(зона карточки: сверху во время боя, снизу после)")
                                    except Exception as e:
                                        say(f"    не смог сохранить зону карточки: {e}")
                                if DEBUG_MODE and (card_saved <= SAVE_AFTER
                                                   or verdict.startswith("исход неясен")):
                                    try:
                                        from PIL import Image as _I
                                        _xf, _xi = xp_cream(sct)
                                        _pair = [_xi] if _xi is not None else []
                                        if xp_base is not None:
                                            _pair.insert(0, xp_base[1])
                                        if _pair:
                                            _I.fromarray(np.ascontiguousarray(
                                                np.clip(np.vstack(_pair), 0, 255)
                                                .astype(np.uint8)[:, :, ::-1])).save(
                                                os.path.join(APP_DIR, f"xp_{catches}.png"))
                                    except Exception:
                                        pass
                                xp_base = None
                                _res = ("взята" if verdict.startswith("РЫБА ВЗЯТА") else
                                        "сорвалась" if verdict.startswith("СОРВАЛАСЬ") else
                                        "неясно")
                                write_row(_row + [_res])
                                # Бой с кнопкой в упоре, а блок ехал ПРОТИВ нажатия -
                                # значит, игра нажатие не приняла.
                                _run, _t0r, _c0 = 0, None, None
                                _against = 0.0
                                for (t_, f_, l_, r_, d_, c_) in fight_hist:
                                    if d_ >= 0.95:
                                        if _t0r is None:
                                            _t0r, _c0 = t_, c_
                                        elif c_ < _c0 - 60:
                                            _against = max(_against, t_ - _t0r)
                                    else:
                                        _t0r = None
                                if _against > 0.5:
                                    say(f"    ! кнопка зажата, а блок ехал влево ({_against:.1f} с) - "
                                        f"игра не принимала нажатие. Подтверждений "
                                        f"нажатия за бой: {hold_refreshes}")
                                globals()["hold_refreshes"] = 0
                                say(f"    итого: взято {caught}, сорвалось {lost}, неясно {unknown}")
                                _out = (1 if verdict.startswith("РЫБА ВЗЯТА") else
                                        0 if verdict.startswith("СОРВАЛАСЬ") else None)
                                AB_STATS.setdefault(round(GAIN, 5), []).append(
                                    (dur, 100.0 * sat_frames / max(st_frames, 1),
                                     st_flips / dur if dur > 0 else 0.0, avg_err, _out))
                                if AB_VALUES and catches % (2 * len(AB_VALUES)) == 0:
                                    say(ab_report())
                                if len(width_hist) > 10:
                                    ww = np.array(width_hist)
                                    stable = 100.0 * np.mean(
                                        np.abs(ww - np.median(ww)) <= 3)
                                    say(f"    ширина блока: медиана {np.median(ww):.0f}, "
                                        f"разброс {ww.min():.0f}-{ww.max():.0f}, "
                                        f"стабильных кадров {stable:.0f}%")
                                    # На crowbar бои 16-21 шли с блоком 240-295 вместо
                                    # 348, и рыба была "в блоке" лишь 17% времени. Что
                                    # тогда было на экране, по журналу не понять -
                                    # сохраняем кадр такого боя.
                                    _wm = float(np.median(ww))
                                    if len(session_w) >= 3:
                                        _typ = float(np.median(session_w))
                                        if (DEBUG_MODE and abs(_wm - _typ) > 0.12 * _typ and odd_saved < 4
                                                and mid_img is not None):
                                            odd_saved += 1
                                            if save_region(mid_img, f"odd_{catches}.png"):
                                                say(f"    ! блок {_wm:.0f} px вместо обычных "
                                                    f"{_typ:.0f} - сохранил odd_{catches}.png")
                                    session_w.append(_wm)
                                if meth_count:
                                    tot = sum(meth_count.values())
                                    parts = " ".join(
                                        f"{k} {100 * v / tot:.0f}%" for k, v in
                                        sorted(meth_count.items(), key=lambda p: -p[1]))
                                    say(f"    чем нашли: {parts}")
                                if MAX_CATCHES and catches >= MAX_CATCHES:
                                    print("Лимит достигнут.")
                                    break
                                time.sleep(max(0.0, AFTER_CATCH - card_extra))
                                state = "cast"
                            continue

                        t_bar_lost = 0.0
                        left, right, row, peak, top_row = bar
                        center = (left + right) // 2

                        # Ширину только ЗАПИСЫВАЕМ, но кадры по ней не выбрасываем.
                        # В версии 2.2-2.3 такой фильтр отбрасывал до 65% кадров:
                        # регулятор переставал обновляться, а ШИМ продолжал жать
                        # мышь по устаревшей скважности - отсюда и были случайные
                        # клики. Там, где фильтр молчал, ошибка была 3-30 пикселей;
                        # где резал четверть кадров и больше - 232-720.
                        width_hist.append(right - left)
                        if len(width_hist) % 45 == 20:
                            mid_img = img.copy()       # кадр боя - на случай разбора
                        fish = None if blinded else find_fish(
                            lum, row, left, right, peak, prev_fish, top_row, rgb)

                        # Физический предел - края полосы, а не расстояние до блока.
                        # Прежняя проверка выбрасывала рыбу дальше 200 пикселей,
                        # хотя на tryhard она туда уходит постоянно.
                        if fish is not None and _V2 is not None:
                            if not (_V2[1] <= fish <= _V2[2]):
                                fish = None

                        if fish is None:
                            if t_nofish == 0.0:
                                t_nofish = now
                            elif now - t_nofish > NOFISH_SEC:
                                pwm_on = False
                                set_hold(False)
                                say(f"[{catches}] рыбы не видно {NOFISH_SEC:.0f}с - "
                                    f"похоже, миниигры нет")
                                if prev_fish is not None:
                                    # ВАЖНО: не называть это "off" - так
                                    # называется глобальный сдвиг области
                                    # (off = REGION["left"]). Присваивание
                                    # делало имя локальным на всю функцию, и
                                    # строка статуса ниже падала с
                                    # UnboundLocalError.
                                    fish_off = prev_fish - center
                                    fish_edge = min(abs(prev_fish - left),
                                                    abs(prev_fish - right))
                                    say(f"    последний раз видел на {prev_fish}, "
                                        f"это {fish_off:+d} от центра блока и "
                                        f"{fish_edge} px от его края")
                                if meth_count:
                                    tot = sum(meth_count.values())
                                    say("    чем находили: " + " ".join(
                                        f"{k} {100 * v / tot:.0f}%" for k, v in
                                        sorted(meth_count.items(), key=lambda p: -p[1])))
                                if TRACE_ALL:
                                    flush_trace()
                                else:
                                    trace.clear()
                                t_nofish = 0.0
                                time.sleep(1.0)
                                state = "cast"
                                continue
                        else:
                            t_nofish = 0.0

                        meth = LAST_METHOD if fish is not None else "нет"
                        meth_count[meth] = meth_count.get(meth, 0) + 1

                        # Кадр, НА КОТОРОМ РЫБА ПРОПАЛА - самое ценное для разбора
                        # детекции: видно, что именно детектор перестал узнавать.
                        if (DEBUG_MODE and fish is None and fails_saved < SAVE_FAILS
                                and seen_hist and seen_hist[-1][1] == 1):
                            fails_saved += 1
                            if save_region(img, f"fail_{fails_saved}.png"):
                                say(f"    сохранил fail_{fails_saved}.png (рыба пропала)")

                        seen_hist.append((now, 1 if fish is not None else 0))
                        conf = confidence(now)

                        fight_hist.append((now, fish, left, right, duty, center))
                        if DEBUG_MODE and (catches < TRACE_CATCHES or TRACE_ALL):
                            trace.append((catches + 1, round(now - t_state, 4),
                                          fish if fish is not None else "",
                                          center, left, right,
                                          round(duty, 3), 1 if holding else 0, meth))

                        dt = max(0.008, now - t_prev)
                        t_prev = now
                        base = BASE_NEUTRAL + trim

                        if calib_t0 < 0.0:
                            # Прошлая калибровка стартовала прямо в момент поклёвки:
                            # полторы секунды блок ещё не опознавался, центр стоял на
                            # 460 (середина области), и эти кадры попали в запись как
                            # "блок не двигается". Ждём устойчивого опознания блока.
                            width_hist.append(right - left)
                            if len(width_hist) >= 15 and abs(
                                    (right - left) - float(np.median(width_hist))) <= 3:
                                calib_ready += 1
                            else:
                                calib_ready = 0
                            if calib_ready >= 12:
                                calib_t0 = now
                                say(f"КАЛИБРОВКА пошла: блок {right - left} px")
                                w = active_window()
                                if w is not None:
                                    say(f"    активное окно: {w}")
                                    if "roblox" not in w.lower():
                                        say("    ВНИМАНИЕ: это не Roblox - нажатия уйдут не туда")
                                say(f"    курсор: {pyautogui.position()}")
                                calib_hold0 = hold_changes
                                say(f"    поток ШИМ: {'жив' if pwm_alive() else 'МЁРТВ'}")
                            duty = BASE_NEUTRAL
                            pwm_on = True
                            time.sleep(0.002)
                            continue

                        if calib_t0:
                            # Разомкнутая проверка: жмём по расписанию, рыбу не ведём.
                            el = now - calib_t0
                            want = calib_duty(el)
                            if want is None:
                                try:
                                    with open(os.path.join(APP_DIR, "calib.csv"), "w",
                                              newline="", encoding="utf-8") as fh:
                                        wr = csv.writer(fh)
                                        wr.writerow(["t", "скв", "центр", "лево", "право", "рыба"])
                                        wr.writerows(calib_rows)
                                    say(f"КАЛИБРОВКА готова: {len(calib_rows)} строк "
                                        f"-> calib.csv, пришли этот файл")
                                    n_hold = hold_changes - calib_hold0
                                    say(f"    кнопка переключалась {n_hold} раз за калибровку")
                                    if hold_error:
                                        say(f"    ОШИБКА при нажатии: {hold_error}")
                                    say("    " + calib_verdict(calib_rows, n_hold))
                                except OSError as e:
                                    say(f"не смог записать calib.csv: {e}")
                                calib_t0 = 0.0
                                calib_rows = []
                            else:
                                duty = want
                                pwm_on = True
                                calib_rows.append(
                                    (round(el, 4), want, center, left, right,
                                     fish if fish is not None else ""))
                                time.sleep(0.002)
                                continue

                        if fish is not None:
                            if prev_fish is not None and abs(fish - prev_fish) > MAX_JUMP:
                                jump_block += 1
                                if jump_block * dt < JUMP_SEC:
                                    fish = prev_fish
                                else:
                                    jump_block = 0
                                    speed_avg = 0.0
                            else:
                                jump_block = 0


                            error = fish - center
                            st_seen += 1
                            st_err_sum += abs(error)
                            st_err_max = max(st_err_max, abs(error))
                            sign = 1 if error > 25 else (-1 if error < -25 else 0)
                            if sign and prev_err_sign and sign != prev_err_sign:
                                st_flips += 1
                            if sign:
                                prev_err_sign = sign

                            # Скорость СБЛИЖЕНИЯ = d(ошибки)/dt = скорость рыбы минус
                            # скорость блока. Раньше брали только скорость рыбы, и когда
                            # блок уже ехал за ней с той же скоростью, регулятор всё равно
                            # добавлял газу и проскакивал.
                            fish_speed = 0.0 if prev_fish is None else (fish - prev_fish) / dt
                            fish_speed = max(-MAX_SPEED, min(MAX_SPEED, fish_speed))
                            raw_speed = 0.0 if prev_err_valid == 0 else (error - prev_error) / dt
                            raw_speed = max(-MAX_SPEED, min(MAX_SPEED, raw_speed))
                            speed_avg += (raw_speed - speed_avg) * min(1.0, dt / SPEED_TAU)
                            prev_error, prev_err_valid = error, 1
                            prev_fish = fish
                            t_fish_lost = 0.0

                            activity += (abs(fish_speed) - activity) * min(1.0, dt / ACT_TAU)
                            adapt = min(ADAPT_MAX, 1.0 + activity / ACT_SCALE)

                            trim += (error * TRIM_GAIN - trim / TRIM_TAU) * dt
                            trim = max(-TRIM_LIMIT, min(TRIM_LIMIT, trim))

                            # Цель - не сама рыба, а её сглаженный ход, плюс
                            # компенсация задержки самого сглаживания (иначе цель
                            # систематически отстаёт на tau * скорость).
                            if AIM_TAU > 0:
                                k = min(1.0, dt / AIM_TAU)
                                if aim_pos is None:
                                    aim_pos, aim_vel = float(fish), 0.0
                                else:
                                    new_pos = aim_pos + (fish - aim_pos) * k
                                    v_now = (new_pos - aim_pos) / dt if dt > 0 else 0.0
                                    aim_vel += (v_now - aim_vel) * min(1.0, dt / AIM_TAU)
                                    aim_pos = new_pos
                                target = aim_pos + aim_vel * AIM_TAU * AIM_LEAD
                                error = int(target - center)

                            # целимся с упреждением - туда, где рыба окажется
                            aim = error + speed_avg * LEAD
                            # чем хуже видим рыбу, тем осторожнее правим
                            trust = CONF_FLOOR + (1 - CONF_FLOOR) * conf
                            gain_term = aim * GAIN * adapt * trust * (1 + abs(aim) / NONLIN)
                            damp_term = speed_avg * DAMP * adapt / 100
                            duty = base + gain_term + damp_term
                            if duty <= 0.0 or duty >= 1.0:
                                sat_frames += 1
                            duty = max(0.0, min(1.0, duty))

                        elif prev_fish is not None and (t_fish_lost == 0.0
                                                        or now - t_fish_lost < GHOST_SEC):
                            # рыбы не видно - едем по последней известной траектории
                            if t_fish_lost == 0.0:
                                t_fish_lost = now
                            gap = now - t_fish_lost
                            ghost = prev_fish + speed_avg * gap * GHOST_DECAY
                            error = int(ghost - center)
                            aim = error + speed_avg * LEAD
                            gain_term = aim * GAIN * (1 + abs(aim) / NONLIN)
                            gain_term = max(-GHOST_CLAMP, min(GHOST_CLAMP, gain_term))
                            damp_term = 0.0
                            duty = base + gain_term
                            duty = max(0.0, min(1.0, duty))

                        else:
                            duty += (base - duty) * min(1.0, dt / DUTY_TAU)

                        with STATE_LOCK:
                            STATE.update(state="reel", left=left, right=right, fish=fish,
                                         error=error, duty=duty, fps=frames_sec, conf=conf,
                                         catches=catches, peak=peak, width=right - left,
                                         caught=caught, lost=lost,
                                         runtime=now - t_session)

                        # при открытом окне схема в консоли дублирует трекер,
                        # а вывод в консоль Windows медленный - только без окна
                        if SHOW_VIEW and not USE_GUI and now - t_view > 1.0 / VIEW_HZ:
                            t_view = now
                            print(f"\r[{render_bar(left, right, fish)}] "
                                  f"ош{error:+5d} скв{duty:4.2f} {frames_sec:3d}к/с",
                                  end="", flush=True)

                        if DEBUG and now - t_report > 1.0:
                            t_report = now
                            frames_sec = frames
                            if fish is not None:
                                seen = f"{fish + REGION['left']}"
                            elif t_fish_lost:
                                seen = "инерц"
                            else:
                                seen = "нет  "
                            print(f"{frames:3d} к/с | ош {error:5d} | v {speed_avg:6.0f} "
                                  f"| P {gain_term:+.2f} | D {damp_term:+.2f} "
                                  f"| adapt {adapt:.2f} | нейтр {base:.3f} "
                                  f"| скв {duty:.2f} | рыба {seen}")
                            frames = 0

                        if abs(trim) > TRIM_LIMIT * 0.9 and not trim_warned:
                            trim_warned = True
                            print(f"    !!! нейтраль ушла к {0.5 + trim:.2f} - похоже, "
                                  f"истинное равновесие далеко от 0.5. "
                                  f"Поставь BASE_NEUTRAL = {0.5 + trim:.2f}")

                        pwm_on = True     # кнопку теперь ведёт отдельный поток
            except Exception:
                import traceback
                txt = traceback.format_exc()
                frame_errors += 1
                pwm_on = False
                set_hold(False)
                reset_control()
                state = "cast"
                try:
                    with open(os.path.join(APP_DIR, "error.log"), "a",
                              encoding="utf-8") as fh:
                        fh.write(time.strftime("%Y-%m-%d %H:%M:%S")
                                 + f"  (сбой кадра #{frame_errors})\n"
                                 + txt + "\n")
                except Exception:
                    pass
                if frame_errors <= 3:
                    say(f"сбой кадра #{frame_errors}: "
                        f"{txt.strip().splitlines()[-1]}")
                    say("    записал в error.log, продолжаю с заброса")
                if frame_errors >= 40:
                    say("слишком много сбоев подряд - останавливаюсь")
                    raise
                time.sleep(0.2)
    finally:
        pwm_on = False
        set_hold(False)
        flush_trace()
        if AB_STATS:
            say(ab_report())
        print(f"Мышь отпущена. Поймано: {catches}. Статистика в {CSV_PATH}")


# ==================================================================
#  Окно с трекером. Живёт в ГЛАВНОМ потоке (tkinter не потокобезопасен),
#  управляющий цикл крутится в рабочем и лишь публикует STATE.
# ==================================================================
# Красно-чёрная тема (7.1)
BG, FG, DIM = "#0b0506", "#f3e9ea", "#a3868a"
OK_C, WARN_C, BAD_C = "#ffc9ce", "#ffb347", "#ff3346"
ACC = "#e0243a"                       # акцент: заголовок, блок, рамки в фокусе
PANEL, LINE, BTN, SEL = "#12070a", "#5a1420", "#1c080b", "#3d0d14"
FIELD, HINT, ERR_BG = "#140709", "#7a5a5f", "#6b1020"


# ==================================================================
#  Настройки: файл scarlet.ini и окно настроек
# ==================================================================
INI_PATH = os.path.join(APP_DIR, "scarlet.ini")

# ------------------------------------------------------------------
#  Что показывает окно настроек (6.3). Раньше в окне был только
#  регулятор, а всё остальное правилось руками в scarlet.ini. Теперь
#  здесь каждая настройка, какую человеку вообще стоит трогать.
#  Вид поля: ("float"|"int", мин, макс), ("bool",), ("choice", [...]),
#  ("str",). Границы защищают от опечаток вроде 35 вместо 3.5.
# ------------------------------------------------------------------
SETTINGS_TABS = [
    ("Ловля", None, [
        ("CHARGE_TIME", "Сила заброса: держать кнопку, с", ("float", 0.05, 3.0),
         "подбери под свою удочку"),
        ("CAST_GRACE", "Пауза, пока удочка летит, с", ("float", 0.0, 10.0),
         "в это время мышь не трогаем"),
        ("BITE_TIMEOUT", "Ждать поклёвку не дольше, с", ("float", 5.0, 300.0),
         "потом перезаброс"),
        ("AFTER_CATCH", "Пауза после рыбы, с", ("float", 0.0, 10.0), None),
        ("MAX_TIMEOUTS", "Пустых забросов подряд до паузы", ("int", 1, 100), None),
        ("SHAKE_MODE", "Shake", ("choice", SHAKE_MODES),
         "enter - жать Enter до поклёвки, none - удочка без shake"),
        ("SHAKE_INTERVAL", "Как часто жать, с", ("float", 0.05, 3.0), None),
        ("SHAKE_CLICKS", "Щелчков по кнопке", ("int", 1, 5), None),
        ("CAST_FPS", "Проверка поклёвки, раз в секунду", ("int", 0, 240), None),
        ("CAST_STYLE", "Способ заброса", ("choice", ["normal", "perfect"]), None),
        ("AUTO_ROD", "Переэкипировать удочку перед забросом", ("bool",), None),
        ("ROD_SEQ", "Шаги переэкипировки", ("str",), None),
        ("ROD_KEY", "Слот удочки", ("str",), None),
        ("BAG_KEY", "Слот рюкзака", ("str",), None),
        ("FISH_LEFT", "Зона полосы: X", ("int", 0, 7680), None),
        ("FISH_TOP", "Зона полосы: Y", ("int", 0, 4320), None),
        ("FISH_WIDTH", "Зона полосы: ширина", ("int", 100, 7680), None),
        ("FISH_HEIGHT", "Зона полосы: высота", ("int", 30, 600), None),
        ("CAST_SEQ_NORMAL", "Шаги обычного заброса", ("str",), None),
        ("CAST_SEQ_PERFECT", "Шаги идеального заброса", ("str",), None),
        ("PC_STYLE", "Как отпускать", ("choice", ["green", "velocity"]), None),
        ("PC_AUTOLAT", "Подстраивать задержку", ("bool",), None),
        ("PC_GREEN_TOL", "Допуск зелёного", ("int", 0, 80), None),
        ("PC_WHITE_TOL", "Допуск белого", ("int", 0, 120), None),
        ("PC_FAIL", "Полоску не нашёл - отпустить через, с", ("float", 0.5, 10.0), None),
        ("PC_FPS", "Смотреть на полоску, раз в секунду", ("int", 10, 500), None),
        ("PC_EARLY_MS", "Отпускать раньше касания, мс", ("int", 0, 300), None),
        ("PC_B400", "< 400 px/с", ("int", 50, 100), None),
        ("PC_B600", "400-600 px/с", ("int", 50, 100), None),
        ("PC_B800", "600-800 px/с", ("int", 50, 100), None),
        ("PC_B1000", "800-1000 px/с", ("int", 50, 100), None),
        ("PC_B1200", "1000-1200 px/с", ("int", 50, 100), None),
        ("PC_BMAX", "> 1200 px/с", ("int", 50, 100), None),
        ("PC_BFALL", "Скорость неизвестна", ("int", 50, 100), None),
        ("CAST_LEFT", "Зона полоски: X", ("int", 0, 3840), None),
        ("CAST_TOP", "Зона полоски: Y", ("int", 0, 2160), None),
        ("CAST_WIDTH", "Зона полоски: ширина", ("int", 50, 3840), None),
        ("CAST_HEIGHT", "Зона полоски: высота", ("int", 50, 2160), None),
        ("FISH_FPS", "Кадров в бою (0 - максимум)", ("int", 0, 240), None),
        ("SHAKE_TOL", "Допуск белого", ("int", 0, 80), None),
        ("SHAKE_DIST", "Та же кнопка ближе, px", ("int", 0, 200), None),
        ("SHAKE_FPS", "Поиск, раз в секунду", ("int", 5, 240), None),
        ("SHAKE_DUP", "Помнить нажатую кнопку, с", ("float", 0.0, 5.0), None),
        ("SHAKE_FAIL", "Нет shake - перезаброс через, с", ("float", 0.0, 15.0), None),
        ("NAV_KEY", "Клавиша навигации", ("str",), None),
        ("NAV_PRESS", "Чем нажимать", ("choice", ["enter", "space"]), None),
        ("NAV_AUTO", "Включать навигацию на старте", ("bool",), None),
        ("SHAKE_VERIFY", "Белое только внутри кольца кнопки", ("bool",), None),
        ("SHAKE_LEFT", "Зона shake: X", ("int", 0, 3840), None),
        ("SHAKE_TOP", "Зона shake: Y", ("int", 0, 2160), None),
        ("SHAKE_WIDTH", "Зона shake: ширина", ("int", 50, 3840), None),
        ("SHAKE_HEIGHT", "Зона shake: высота", ("int", 50, 2160), None),
    ]),
    ("Камера", None, [
        ("FIRST_PERSON", "Вид от первого лица при старте (F8)", ("bool",),
         "убирает персонажа и свечение пассивок из кадра"),
        ("FP_SCROLLS", "Щелчков колеса внутрь", ("int", 0, 60),
         "если камера не доехала - увеличь"),
    ]),
    ("Управление", "PROFILE", [
        ("GAIN", "Усиление по ошибке", ("float", 0.0001, 0.05),
         "больше - резче; выше 0.005 блок начинает качать"),
        ("LEAD", "Упреждение, с", ("float", 0.0, 2.0),
         "целиться туда, где рыба будет через столько"),
        ("DAMP", "Гашение раскачки", ("float", 0.0, 1.0),
         "блок мотает туда-сюда - увеличь"),
        ("BASE_NEUTRAL", "Нажатие, при котором блок стоит", ("float", 0.2, 0.8),
         "обычно около 0.5"),
        ("PERIOD", "Период нажатий, с", ("float", 0.02, 0.5),
         "меньше - чаще щелчки"),
        ("AIM_TAU", "Сглаживание цели, с", ("float", 0.0, 3.0),
         "0 - целиться прямо в рыбу"),
        ("AIM_LEAD", "Компенсация сглаживания, доля", ("float", 0.0, 1.0), None),
        ("HOLD_REFRESH", "Подтверждать зажатую кнопку, с", ("float", 0.0, 2.0),
         "0 - выкл; спасает, если игра упустила нажатие"),
        ("AB_TEST", "Сравнить GAIN (через запятую)", ("str",),
         "напр. 0.0012, 0.0035 - чередует по рыбам; пусто - выкл"),
    ]),
    ("Запись", None, [
        ("USE_GUI", "Окно с трекером", ("bool",),
         "выключишь - останется только консоль (после перезапуска)"),
        ("SHOW_VIEW", "Схема полосы в консоли", ("bool",), None),
        ("DEBUG_MODE", "Режим отладки: снимки и trace.csv", ("bool",), None),
        ("CHECK_UPDATES", "Проверять обновления при запуске", ("bool",), None),
        ("WELCOME_DONE", "Приветствие показано", ("bool",), None),
        ("TRACE_CATCHES", "Писать trace.csv для стольких рыб", ("int", 0, 10000),
         "0 - не писать"),
        ("TRACE_ALL", "Писать trace и у сорвавшихся", ("bool",), None),
        ("SAVE_AFTER", "Снимков экрана после миниигры", ("int", 0, 50), None),
        ("BURST_SEC", "Длина серии F3, с", ("float", 1.0, 30.0), None),
        ("BURST_EVERY", "Серия: каждый какой кадр", ("int", 1, 10), None),
    ]),
    ("Детектор", "WARN", [
        ("TRACK_STYLE", "Как вести рыбу", ("choice", ["line", "color"]), None),
        ("SHOW_OVERLAY", "Треугольники поверх игры", ("bool",), None),
        ("COL_TARGET", "Цвет рыбы", ("color",), None),
        ("TOL_TARGET", "Допуск рыбы", ("int", 0, 255), None),
        ("COL_ARROW", "Цвет стрелки", ("color",), None),
        ("TOL_ARROW", "Допуск стрелки", ("int", 0, 255), None),
        ("COL_LEFT", "Цвет левого края", ("color",), None),
        ("TOL_LEFT", "Допуск левого края", ("int", 0, 255), None),
        ("COL_RIGHT", "Цвет правого края", ("color",), None),
        ("TOL_RIGHT", "Допуск правого края", ("int", 0, 255), None),
        ("CAPTURE", "Захват экрана", ("choice", ["mss", "dxcam"]),
         "dxcam - кадр прямо у видеокарты; нужен пакет dxcam"),
        ("U_EDGE_FIX", "Уточнять края блока-градиента", ("bool",),
         "crowbar: тёмные края блока - тоже блок"),
        ("FISH_MODEL_ON", "Отличать рыбу от фона по памяти", ("bool",),
         "выкл - только прежний поиск рыбы"),
        ("U_PRESENT", "Порог: полоса есть на экране", ("float", 1.0, 200.0),
         "замер: где полоса есть - от 73, где нет - до 18"),
        ("BAR_LO", "Левый край полосы в области", ("int", 0, 900), None),
        ("BAR_HI", "Правый край полосы в области", ("int", 20, 920), None),
        ("CARD_LEFT", "Карточка рыбы: X", ("int", 0, 3840), None),
        ("CARD_TOP", "Карточка рыбы: Y", ("int", 0, 2160), None),
        ("CARD_WIDTH", "Карточка рыбы: ширина", ("int", 10, 1000), None),
        ("CARD_HEIGHT", "Карточка рыбы: высота", ("int", 10, 1000), None),
        ("CARD_FRAC", "Доля зоны, что должна измениться", ("float", 0.05, 1.0), None),
        ("CARD_WAIT", "Ждать XP и карточку после боя, с", ("float", 0.0, 10.0), None),
        ("XP_LEFT", "Зона XP: X", ("int", 0, 3840), "уровень и +xp сверху экрана"),
        ("XP_TOP", "Зона XP: Y", ("int", 0, 2160), None),
        ("XP_WIDTH", "Зона XP: ширина", ("int", 10, 1000), None),
        ("XP_HEIGHT", "Зона XP: высота", ("int", 10, 600), None),
        ("XP_FRAC", "XP: доля кремового для поимки", ("float", 0.005, 0.5),
         "замер: поимка 14-16%, без поимки 0%"),
        ("PROG_LEFT", "Полоса прогресса: X", ("int", 0, 3840), None),
        ("PROG_TOP", "Полоса прогресса: Y", ("int", 0, 2160), None),
        ("PROG_WIDTH", "Полоса прогресса: ширина", ("int", 10, 2000), None),
        ("PROG_HEIGHT", "Полоса прогресса: высота", ("int", 4, 200), None),
    ]),
]
# Эти настройки у каждой удочки могут быть свои: когда выбран профиль,
# вкладка "Управление" пишет их в его секцию [профиль:имя].
PROFILE_KEYS = {"GAIN", "LEAD", "DAMP", "BASE_NEUTRAL", "PERIOD",
                "AIM_TAU", "AIM_LEAD", "TRACK_STYLE",
                "COL_TARGET", "TOL_TARGET", "COL_ARROW", "TOL_ARROW",
                "COL_LEFT", "TOL_LEFT", "COL_RIGHT", "TOL_RIGHT"}
RESTART_KEYS = {"USE_GUI"}      # читаются только при запуске
_HINT = {f[0]: f[3] for _t, _k, fl in SETTINGS_TABS for f in fl if f[3]}
_HINT["PROFILE"] = "имя секции [профиль:имя] в scarlet.ini"


def _fmt_val(v):
    """Значение -> строка для scarlet.ini."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _check_val(spec, raw):
    """Строка из поля -> значение. ValueError с понятным текстом, если нет."""
    kind = spec[0]
    raw = str(raw).strip()
    if kind == "bool":
        return bool(raw) and raw not in ("0", "False")
    if kind == "choice":
        if raw not in spec[1]:
            raise ValueError(tr("one of: ", "одно из: ") + ", ".join(spec[1]))
        return raw
    if kind == "str":
        return raw
    if kind == "color":
        low = raw.lower()
        if low in ("none", ""):
            return "none"
        if not low.startswith("#"):
            low = "#" + low
        if hex_bgr(low) is None:
            raise ValueError(tr("#rrggbb or none", "#rrggbb или none"))
        return low
    try:
        v = float(raw.replace(",", "."))
    except ValueError:
        raise ValueError(tr("number expected", "нужно число"))
    if kind == "int":
        if v != int(v):
            raise ValueError(tr("whole number expected", "нужно целое"))
        v = int(v)
    lo, hi = spec[1], spec[2]
    if not lo <= v <= hi:
        raise ValueError(tr(f"{lo} to {hi}", f"от {lo} до {hi}"))
    return v


def _ini_text():
    if not os.path.exists(INI_PATH):
        return ""
    try:
        with open(INI_PATH, encoding="utf-8-sig") as fh:
            return fh.read()
    except OSError:
        return ""


def _key_lines(text, name):
    """ВСЕ строки, где задан ключ, вне секций профилей.

    Именно все, а не первая. Автодопись настроек могла завести второй
    [общие-2] с теми же ключами, а при чтении побеждает последняя
    секция. Правка только первой давала молчаливый откат: человек
    сохранял значение, видел "сохранено", а после перезапуска
    возвращалось старое."""
    out = []
    prof = False
    for i, line in enumerate(text.split("\n")):
        st = line.strip()
        if st.startswith("["):
            low = st.lower()
            prof = low.startswith("[профиль:") or low.startswith("[profile:")
            continue
        if prof or not st or st.startswith(("#", ";")):
            continue
        if "=" in st and st.split("=")[0].strip().upper() == name:
            out.append(i)
    return out


def _key_line(text, name):
    got = _key_lines(text, name)
    return got[0] if got else -1


def ini_write(values):
    """Записать настройки в scarlet.ini, НЕ трогая остальной файл.

    Пишем построчно, а не через configparser: тот при сохранении
    выбрасывает все комментарии, а файл читают люди."""
    text = _ini_text()
    lines = text.split("\n") if text else []
    added = []
    for name, val in values.items():
        got = _key_lines("\n".join(lines), name)
        for i in got:                 # правим ВСЕ вхождения
            lines[i] = f"{name} = {val}"
        if not got:
            added.append((name, val))
    if added:
        tag, n = "общие", 1
        while f"[{tag}]" in "\n".join(lines):
            n += 1
            tag = f"общие-{n}"
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{tag}]")
        for name, val in added:
            hint = _HINT.get(name)
            if hint:
                lines.append(f"# {hint}")
            lines.append(f"{name} = {val}")
    try:
        with open(INI_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines).rstrip() + "\n")
        return True
    except OSError as e:
        say(f"не смог записать scarlet.ini: {e}")
        return False


def ini_sync():
    """Дописать в файл настройки, которых там ещё нет."""
    text = _ini_text()
    # Значением из КОДА, а не текущим: при включённом профиле текущее -
    # это значение профиля, и оно утекало бы в общую секцию.
    miss = {n: _fmt_val(d) for n, d in sorted(_REG.items())
            if _key_line(text, n) < 0}
    if not miss:
        return 0
    ini_write(miss)
    return len(miss)


# Удочки, которые мы уже измеряли. Живут в КОДЕ, а не только в твоём
# scarlet.ini: у друга файла не было, программа создала пустой - и выбрать
# удочку было не из чего.
# Удочки: имя, как в игре, русское название и цвет значка. Ключ - имя
# секции [профиль:...] в scarlet.ini. Своим удочкам имена пишутся прямо в
# их секцию (NAME_EN / NAME_RU), когда их добавляют в пульте.
ROD_INFO = {
    "": ("Any rod", "Любая удочка", "#9b8f8f"),
    "crowbar": ("Crowbar", "Лом", "#8e8e96"),
    "tryhard": ("Tryhard Rod", "Удочка старателя", "#c21518"),
    "duskwire": ("Duskwire", "Сумеречная струна", "#4a3f73"),
    "ruinous": ("Ruinous Oath", "Губительная клятва", "#7a1fa2"),
    "обычная": ("Standard Rod", "Обычная удочка", "#b08a57"),
}


def rod_names(pid):
    """(англ. имя как в игре, русское) для удочки."""
    pid = (pid or "").lower()
    if pid in ROD_INFO:
        return ROD_INFO[pid][0], ROD_INFO[pid][1]
    lines = _ini_text().split("\n")
    sec = _prof_sect(lines, pid)
    en = ru = None
    if sec is not None:
        _n, i, end = sec
        for l in lines[i + 1:end]:
            if "=" in l and not l.strip().startswith((";", "#")):
                k, v = l.split("=", 1)
                if k.strip().upper() == "NAME_EN":
                    en = v.strip()
                elif k.strip().upper() == "NAME_RU":
                    ru = v.strip()
    en = en or pid
    return en, ru or en


def rod_color(pid):
    info = ROD_INFO.get((pid or "").lower())
    if info:
        return info[2]
    import hashlib
    h = hashlib.md5(pid.encode("utf-8")).digest()
    return "#%02x%02x%02x" % (120 + h[0] % 120, 30 + h[1] % 90, 30 + h[2] % 90)


BUILTIN_PROFILES = [
    ("crowbar", "Блок ~348 px, серый градиент. Нейтраль 0.475, лаг 345 мс.",
     {"DAMP": "0.030", "LEAD": "0.30"}),
    ("tryhard", "Нейтраль 0.519, лаг 345 мс, блок ~100 px. Рыба бывает "
     "быстрее блока - часть потерь не лечится настройкой.",
     {"DAMP": "0.030", "LEAD": "0.42"}),
    ("duskwire", "Блок около 77 px, почти чёрный с градиентом.",
     {"DAMP": "0.030", "LEAD": "0.30"}),
    ("обычная", "Нейтраль 0.475, запаздывание 345 мс.",
     {"DAMP": "0.030", "LEAD": "0.30"}),
]


OBSOLETE_KEYS = {"UNI_DETECTOR", "OLD_DETECTOR", "USE_ABOVE", "FISH_MODEL", "PC_LEAD_MS"}


def ini_migrate():
    """Значения, у которых сменились имена: SHAKE_MODE enter -> navigation,
    none -> disabled (7.9)."""
    lines = _ini_text().split("\n")
    changed = False
    pre83 = not any(l.strip().upper().startswith("PC_AUTOLAT") for l in lines)
    for i, l in enumerate(lines):
        st = l.strip()
        if "=" in st and not st.startswith((";", "#")):
            k, v = st.split("=", 1)
            if k.strip().upper() == "SHAKE_MODE" and v.strip().lower() in SHAKE_OLD:
                lines[i] = f"SHAKE_MODE = {SHAKE_OLD[v.strip().lower()]}"
                changed = True
            if k.strip().upper() == "PC_STYLE" and (v.strip().lower() == "prediction"
                                                    or (pre83 and v.strip().lower() == "velocity")):
                lines[i] = "PC_STYLE = green"
                changed = True
    # зона shake из 7.9 была слишком узкой: если в файле ровно она -
    # ставим новую (свою, изменённую руками, не трогаем)
    old_zone = {"SHAKE_LEFT": "384", "SHAKE_TOP": "216", "SHAKE_WIDTH": "1152",
                "SHAKE_HEIGHT": "620"}
    new_zone = {"SHAKE_LEFT": "40", "SHAKE_TOP": "60", "SHAKE_WIDTH": "1840",
                "SHAKE_HEIGHT": "940"}
    zone_80 = {"SHAKE_LEFT": "200", "SHAKE_TOP": "80", "SHAKE_WIDTH": "1520",
               "SHAKE_HEIGHT": "880"}
    found = {}
    for i, l in enumerate(lines):
        st = l.strip()
        if "=" in st and not st.startswith((";", "#")):
            k, v = st.split("=", 1)
            if k.strip().upper() in old_zone:
                found[k.strip().upper()] = (i, v.strip())
    zone_791 = {"SHAKE_LEFT": "240", "SHAKE_TOP": "110", "SHAKE_WIDTH": "1440",
                "SHAKE_HEIGHT": "740"}
    if len(found) == 4 and any(all(found[k][1] == v for k, v in z.items())
                               for z in (old_zone, zone_791, zone_80)):
        for k, (i, _v) in found.items():
            lines[i] = f"{k} = {new_zone[k]}"
        changed = True
    if changed and _write_lines(lines):
        reload_config()
        return True
    return False


def ini_drop_obsolete():
    """Убрать из scarlet.ini настройки, которых в программе больше нет
    (детекторы 1.x/2.x удалены в 7.0). Их строки просто ничего не
    делали бы, но путали бы при чтении файла."""
    lines = _ini_text().split("\n")
    keep, gone = [], []
    for l in lines:
        st = l.strip()
        if "=" in st and not st.startswith((";", "#")) \
                and st.split("=")[0].strip().upper() in OBSOLETE_KEYS:
            gone.append(st.split("=")[0].strip())
            continue
        keep.append(l)
    if gone and _write_lines(keep):
        return gone
    return []


def ini_seed_profiles():
    """Если в scarlet.ini нет НИ ОДНОЙ удочки - дописать известные.

    Только когда нет ни одной: если ты какую-то удочку удалил сам,
    она не должна воскресать при каждом запуске."""
    if list_profiles():
        return 0
    lines = _ini_text().rstrip().split("\n") if _ini_text().strip() else []
    lines += ["", "; ---------------------------------------------------------------",
              ";  УДОЧКИ. Выбираются в окне Настройки -> Удочка.",
              "; ---------------------------------------------------------------"]
    for name, note, vals in BUILTIN_PROFILES:
        lines += ["", f"[профиль:{name}]", f"; {note}"]
        lines += [f"{k} = {v}" for k, v in vals.items()]
    return len(BUILTIN_PROFILES) if _write_lines(lines) else 0


def _ini_sections(lines):
    """[(имя секции, строка заголовка, конец секции не включая)]."""
    heads = [(i, l.strip()[1:-1].strip()) for i, l in enumerate(lines)
             if l.strip().startswith("[") and l.strip().endswith("]")]
    out = []
    for k, (i, name) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        out.append((name, i, end))
    return out


def _is_prof_sect(name):
    low = name.lower()
    return low.startswith("профиль:") or low.startswith("profile:")


def list_profiles():
    lines = _ini_text().split("\n")
    return [n.split(":", 1)[1].strip() for n, _i, _e in _ini_sections(lines)
            if _is_prof_sect(n)]


def _prof_sect(lines, prof):
    for name, i, end in _ini_sections(lines):
        if _is_prof_sect(name) and name.split(":", 1)[1].strip().lower() == prof:
            return name, i, end
    return None


def _write_lines(lines):
    try:
        with open(INI_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines).rstrip() + "\n")
        return True
    except OSError as e:
        say(f"не смог записать scarlet.ini: {e}")
        return False


def ini_write_profile(prof, values):
    """Записать значения в секцию [профиль:prof], не трогая остальное.
    Нет секции - заводим в конце файла."""
    lines = _ini_text().split("\n")
    sec = _prof_sect(lines, prof)
    if sec is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[профиль:{prof}]")
        sec = _prof_sect(lines, prof)
    for name, val in values.items():
        _n, i, end = sec
        hit = [j for j in range(i + 1, end)
               if "=" in lines[j] and not lines[j].strip().startswith((";", "#"))
               and lines[j].split("=")[0].strip().upper() == name]
        for j in hit:
            lines[j] = f"{name} = {val}"
        if not hit:
            # вставляем после последней непустой строки секции
            j = end
            while j - 1 > i and not lines[j - 1].strip():
                j -= 1
            lines.insert(j, f"{name} = {val}")
            sec = _prof_sect(lines, prof)
    return _write_lines(lines)


def ini_delete_profile(prof):
    lines = _ini_text().split("\n")
    sec = _prof_sect(lines, prof)
    if sec is None:
        return False
    _n, i, end = sec
    del lines[i:end]
    return _write_lines(lines)


def profile_keys_set(prof):
    """Какие ключи заданы в секции профиля (остальные он берёт общими)."""
    if not prof:
        return set()
    lines = _ini_text().split("\n")
    sec = _prof_sect(lines, prof)
    if sec is None:
        return set()
    _n, i, end = sec
    return {l.split("=")[0].strip().upper() for l in lines[i + 1:end]
            if "=" in l and not l.strip().startswith((";", "#"))}


def _apply_derived():
    """Величины, которые при запуске СОБИРАЮТСЯ из настроек. Словари
    правим на месте: на них уже ссылаются места захвата экрана."""
    global AB_VALUES
    CARD.update(left=CARD_LEFT, top=CARD_TOP, width=CARD_WIDTH, height=CARD_HEIGHT)
    XP_ZONE.update(left=XP_LEFT, top=XP_TOP, width=XP_WIDTH, height=XP_HEIGHT)
    SHAKE_ZONE.update(left=SHAKE_LEFT, top=SHAKE_TOP, width=SHAKE_WIDTH,
                      height=SHAKE_HEIGHT)
    CAST_ZONE.update(left=CAST_LEFT, top=CAST_TOP, width=CAST_WIDTH, height=CAST_HEIGHT)
    REGION.update(left=FISH_LEFT, top=FISH_TOP, width=FISH_WIDTH, height=FISH_HEIGHT)
    PC_BANDS[:] = [(400, PC_B400), (600, PC_B600), (800, PC_B800), (1000, PC_B1000),
                   (1200, PC_B1200), (10 ** 9, PC_BMAX)]
    PROGRESS.update(left=PROG_LEFT, top=PROG_TOP, width=PROG_WIDTH,
                    height=PROG_HEIGHT)
    try:
        AB_VALUES = [float(x) for x in AB_TEST.replace(";", ",").split(",")
                     if x.strip()] if AB_TEST else []
    except ValueError:
        AB_VALUES = []
        say(f"AB_TEST не разобрал: {AB_TEST} - сравнение выключено")


def reload_config():
    """Перечитать scarlet.ini (общие + выбранный профиль) и применить на лету.

    После любого сохранения работает ровно то, что лежит в файле: так
    невозможно положение "в окне одно, в файле другое, после перезапуска
    третье", с которым мы уже сталкивались."""
    global _INI, _PROF, PROFILE
    _base, _INI = _load_ini()
    PROFILE = (_INI.get("PROFILE") or "").split("#")[0].strip().lower()
    _PROF = _load_profile()
    changed = []
    for name, d in _REG.items():
        if name in RESTART_KEYS:
            continue
        # Перезаписываем ТОЛЬКО простые значения. В 6.4 настройка
        # FISH_MODEL совпала по имени с объектом модели рыбы, и смена
        # удочки подменяла объект на True - макрос падал на первом же
        # броске. Теперь такое совпадение не ломает работу, а видно.
        if not isinstance(globals().get(name, d), (bool, int, float, str)):
            say(f"!!! настройка {name} совпала по имени с частью программы - пропускаю")
            continue
        val = _parse_val(name, _PROF.get(name, _INI.get(name)), d)
        if globals().get(name) != val:
            changed.append(name)
            globals()[name] = val
    _apply_derived()
    return changed


import math

def _cl(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x

def _ease(x):
    x = _cl(x)
    return 1 - (1 - x) ** 3

def _strap(x0, y0, ang, length, curl, width, n=16, wave=0.0, bend=None):
    """Лента вдоль изогнутой линии: лепесток (width>0) или тычинка.
    bend(s) - доп. поворот, например, чтобы тычинки тянулись вверх."""
    pts_c, x, y = [], x0, y0
    for i in range(n + 1):
        s = i / n
        a = ang + curl * s ** 2.2
        if bend is not None:
            a = bend(a, s)
        pts_c.append((x, y, a, s))
        x += math.cos(a) * length / n
        y += math.sin(a) * length / n
    if width <= 0:
        return None, [(p[0], p[1]) for p in pts_c]
    left, right = [], []
    for x, y, a, s in pts_c:
        w = width * math.sin(math.pi * min(s, 0.96)) ** 0.6
        w *= 1 + wave * math.sin(s * 17)
        nx, ny = -math.sin(a), math.cos(a)
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))
    return left + right[::-1], [(p[0], p[1]) for p in pts_c]

def _toward_up(k):
    """Поворачивать направление к "вверх" (-pi/2) с силой k*s."""
    def f(a, s):
        d = (-math.pi / 2 - a + math.pi) % (2 * math.pi) - math.pi
        return a + d * k * s * s
    return f

LILY_COLORS = dict(stem="#3d0a12", stem_hi="#5e111b", petal_dark="#7d0a19",
                   petal="#c8182e", petal_hi="#ff4557", stamen="#e8283c",
                   anther="#ffb3ba", heart="#4a0610")

def lily_shapes(cx, cy, scale, bloom, sway=0.0, stem_len=260, seed=0, dim=1.0):
    """Паучья лилия (зонтик из нескольких цветков) как набор фигур:
    ("poly"|"line"|"oval", точки, цвет, толщина). bloom 0..1."""
    # dim < 1 - цветок "вдали": цвета уходят в фон, текст поверх читается
    C = (LILY_COLORS if dim >= 1 else
         {k: _mix(BG, v, dim) for k, v in LILY_COLORS.items()})
    out = []
    stem_p = _ease(bloom / 0.28)
    pet_p = _ease((bloom - 0.18) / 0.55)
    sta_p = _ease((bloom - 0.42) / 0.58)
    pts = []
    for i in range(25):
        s = i / 24
        if s > stem_p + 1e-9:
            break
        yy = cy + stem_len * (1 - s)
        xx = cx + 12 * scale * math.sin(math.pi * (1 - s) * 0.9) + sway * 30 * s * s
        pts.append((xx, yy))
    if len(pts) > 1:
        out.append(("line", pts, C["stem"], 3.4 * scale))
        out.append(("line", pts, C["stem_hi"], 1.0 * scale))
    if stem_p < 1:
        return out
    hx, hy = cx + sway * 30, cy
    # цветки зонтика: направление взгляда каждого
    florets = [(-2.55, 0.85), (-0.6, 0.85), (-1.57, 1.0), (2.75, 0.7), (0.4, 0.7)]
    petals, stamens = [], []
    for fi, (phi, sz) in enumerate(florets):
        phi = phi + sway * 0.8
        # в бутоне всё смотрит вверх и прижато
        ph = -math.pi / 2 + (phi + math.pi / 2) * (0.25 + 0.75 * pet_p)
        ox = hx + math.cos(ph) * 4 * scale * pet_p
        oy = hy + math.sin(ph) * 4 * scale * pet_p
        for j in range(6):
            spread = (j - 2.5) / 2.5                       # -1..1
            a = ph + spread * (0.25 + 0.95 * pet_p)
            L = scale * sz * (10 + 30 * pet_p)
            curl = (0.3 + 1.9 * pet_p) * (1 if spread >= 0 else -1)
            wid = scale * sz * (1.9 + 1.5 * pet_p)
            poly, c = _strap(ox, oy, a, L, curl, wid, wave=0.22 * pet_p)
            inner, _ = _strap(ox, oy, a, L * 0.92, curl, wid * 0.5)
            petals.append((math.sin(a), poly, inner, c))
        if sta_p > 0:
            for j in range(5):
                spread = (j - 2) / 2
                a = ph + spread * 0.6
                L = scale * sz * (8 + 58 * sta_p)
                _n, c = _strap(ox, oy, a, L, 0.0, 0,
                               bend=_toward_up(0.65 * sta_p))
                stamens.append(c)
    # задние лепестки (смотрят вверх) рисуем раньше передних
    petals.sort(key=lambda p: p[0])
    for _k, poly, inner, c in petals:
        out.append(("poly", poly, C["petal_dark"], 0))
        out.append(("poly", inner, C["petal"], 0))
        out.append(("line", c[2:-4], C["petal_hi"], 0.7 * scale))
    for c in stamens:
        out.append(("line", c, C["stamen"], max(1.0, 0.9 * scale)))
        tx, ty = c[-1]
        r = 1.5 * scale * sta_p
        out.append(("oval", (tx - r, ty - r * 0.7, tx + r, ty + r * 0.7), C["anther"], 0))
    r = 3.0 * scale * max(pet_p, 0.3)
    out.append(("oval", (hx - r, hy - r, hx + r, hy + r), C["heart"], 0))
    return out


# ------------------------------------------------------------------
#  ПРОВЕРКА ПРИ ЗАПУСКЕ (7.1)
#  Макрос "молча не ловил" у друга по причинам, которые видно сразу:
#  не то разрешение, игра в окне, игра свёрнута. Теперь он говорит это
#  сам. Каждая строка: (уровень, текст). ok / warn / bad / info.
# ------------------------------------------------------------------
NEED_W, NEED_H = 1920, 1080


def env_check():
    """[(уровень, по-русски, по-английски)]; уровень ok / bad / info."""
    res = []
    if os.name != "nt":
        return [("info", "не Windows - проверка окна пропущена",
                 "Not Windows - window check skipped")]
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # настоящие пиксели
    except Exception:
        pass
    try:
        with mss.mss() as s_:
            mon = s_.monitors[1]
            sw, sh = mon["width"], mon["height"]
            nmon = len(s_.monitors) - 1
            shot = np.array(s_.grab(mon))[:, :, :3]
        if (sw, sh) == (NEED_W, NEED_H):
            res.append(("ok", f"экран {sw}x{sh}", f"Screen {sw}x{sh}"))
        else:
            res.append(("bad", f"экран {sw}x{sh} - нужен {NEED_W}x{NEED_H}",
                        f"Screen {sw}x{sh} - {NEED_W}x{NEED_H} required"))
        if int(shot.max()) < 8:
            res.append(("bad", "снимок экрана чёрный", "Screen capture is black"))
    except Exception as e:
        sw, sh, nmon = NEED_W, NEED_H, 1
        res.append(("bad", f"не удалось снять экран: {e}", f"Screen capture failed: {e}"))
    try:
        pct = round(u.GetDpiForSystem() * 100 / 96)
        res.append(("ok" if pct == 100 else "info", f"масштаб Windows {pct}%",
                    f"Windows scale {pct}%"))
    except Exception:
        pass
    h = u.FindWindowW(None, "Roblox")
    if not h:
        res.append(("bad", "Roblox не найден", "Roblox not found"))
        return res
    if u.IsIconic(h):
        res.append(("bad", "Roblox свёрнут", "Roblox is minimized"))
        return res
    rc = wintypes.RECT()
    u.GetClientRect(h, ctypes.byref(rc))
    pt = wintypes.POINT(0, 0)
    u.ClientToScreen(h, ctypes.byref(pt))
    cw, ch = rc.right - rc.left, rc.bottom - rc.top
    if (pt.x, pt.y, cw, ch) == (0, 0, sw, sh):
        res.append(("ok", "Roblox на весь экран", "Roblox fullscreen"))
    elif (cw, ch) == (sw, sh):
        res.append(("bad", "Roblox не на основном мониторе",
                    "Roblox is not on the primary monitor"))
    else:
        res.append(("bad", f"Roblox в окне {cw}x{ch} - нажми F11",
                    f"Roblox is windowed ({cw}x{ch}) - press F11"))
    if nmon > 1:
        res.append(("info", f"мониторов: {nmon}", f"{nmon} monitors"))
    return res


ENV_SYM = {"ok": "\u2713", "warn": "!", "bad": "\u2717", "info": "\u00b7"}
last_env = []


def log_env_check(title="проверка"):
    """Проверить и записать в журнал. True - всё в порядке."""
    try:
        res = env_check()
    except Exception as e:
        res = [("bad", f"проверка упала: {e}", f"Check failed: {e}")]
    last_env[:] = res
    bad = [r for r in res if r[0] == "bad"]
    say(f"{title}: " + ("всё в порядке" if not bad else f"проблем: {len(bad)}"))
    for lvl, ru, _en in res:
        if lvl != "ok" or bad:
            say(f"    {ENV_SYM[lvl]} {ru}")
    return not bad


# Иконка Scarlet hub (паучьи лилии) и круглый логотип для шапки - PNG,
# встроены в код, чтобы программа оставалась одним файлом.
ICON64_B64 = "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"
ICON32_B64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAHhElEQVR4nCXW26um512H8ev7u+/n3azdu2ZW1qxMhsxkpnHaARvjpsW2GXogBSsKkhZEoSfqkQSh9URPelQ88KDQIgqCKFZFSCsYq1g3SJCCpLUbmyakjZlMk5msdGay9utd7/s89+/rQf6B6/DDpWtdXZgze5ATChEIISlJA6V2zgEqmjpFPE15keF5XG2hlvlk+jH4T3wsIEJMFdulbJaoLemdAnB7N2vCSjGVFmbIBkxEKvelLttfKZvpsHHDSO+VXsB7ZmQGLPtMeWDbpc6dimitDTZoEIKKZTakPbIpVqVIn4kSzO1Mh2gQMNjb4k24bVfbsCpVlOlUntk6LzUJ5wZx3xZEqNhjqUMDDjGLWKSP8JkdCjtHit5ukBL2PPNSxFpot+UIEp05x4otRT2DcRrpTEpnASGB7RDYEzPPNsAaGKqzF13mRCrEfjagi1ii02QBCQ8FNyhh7kTUxxWT0CvZVvFaxN3MYgOJ0pzaq1G24H7mjgI8x12yKq1anQRa4BLlrLU5XLN/CS6mvo7fwn26Htq3M4ETPFUESruAxaR2p21YizhJFmIOttekdTGghPtu2All6NfgqdrdbDpwe97+N/xoqeuSHpKCOHErks1E2nf2MEWPlHIEhifNr0S8WMr3h2EKc7ObbSZtRnec/XZosO9ne6p0nf1CDscwR8cRh5nagNUog33sHKPHpZ+TDuE5O+CRUh5t7dmIHh2V7nniL4Zl7/yJ0IvpnVI38VHmb0qvZ/teqLP3syXcg6NSdlvGACfODemc4lH4jP1Z+y/tv4vYgkPzG6XczzzM9vet/46zyL9T6h86ZuJWtqGMTsyueUZlJjqpQ2PFrJSZouJIaOauc6eUj9TuhuIiLPAHMr8krdv/Yj8H31bcxd8azn5vtPKr3cpJdJ/u1m+W9SONr0823lbpzPXoFCXxEk8jmg2EoGHBrWH4B/yLpXxCukN8S/qh/YR4A70csQPXzUcVP0vslelpHT9SV75Qxjfqymz1/FJOmJW6UMykBRg9aC1RRcLGPoVuGG5KKT0tPi419Kq9BTsRW8Fls52Nfv52TG9p9I+13Mzh+uLkG8PhBxT/FHmnDb/V2gule9XezzwWZ3bFBi3EFD6HPgin9nelZ82qtC1l+kMtNyI+IkWM/gQutPm5Mlks5/899Fse3pu6bQt+fRgCno04tt9pDVhKtZMWMENfhptwGzakXThvvyZdQJK/D9fsS5n/G3nZ+lgbvsn8JzO/3pYvu12kLuCW/R/yA5V37H2xCC3tgrQuncAXFVP7T3GHLkbZgiPptXSHd+1N6eP4U8QMHOVvsn1NmqkMsNv6UZT3hTazfdXeE8Ve6bqToQ1ObI1KuYyetL+cbQ01nHAuyjlFZgM/rbInvprtPPGM1EU9Vb2B/pn+bvMdciZdV3wyeDPzD8TUjNGd1ndSs6PLXMK/w0PSBjwsXYli50m2wS7Sr0lH9kXiZ+DPoIZec+vgo1HvRWQUwU/jvyZeUlwhGu4zt0ttkiOqFAfOFfNU1HW3l6J2RT+1HHalffLIvIAnUS5kewy+6XyuDZvojzzc0Hin1KM23IYFrLh9o9RLw3BoduXzaKXrTjO1EtFnFmlDGswvRPld+2vwr/YhuYKuKQ7Qefmc+Y4J+bGo38u2Zj5Wp69kf0feKnGYuVG7+dC/1YYjqTkv1NHb2WKCZhEVzTMtfcL5BL6H75EV/b7020jyAv2fPcIjxX3n49J9/Ibb+nhU8KrzSmbgE6mPYsWZqZldZh3BZZV35N5xzt7A6+hQLKGiS9KH0x9UeZ38c/SmZOkQZorL9nnlg75fczxhrik+n15Fg3M92xZ60PoK9cz5qr0mjaHHX4Ifil2zYZr8Rev90rnWf1tajbgYcWT69GH4B6I5VzN/fjTezvzjHPYHK2KM36fo8C3rFLQVpcKaPYIlNBjDNryFp2gB71c8Lb6Cztnqur1MzIH47tA/gq7W0ZO1/NcwvJzDCG+oTPAscwffNQdCDys6SFzRCAQ34JfhK/CmQfQwoA53EIDiPRHH6ZfcLipGij23iLK0F25X6nizljcW80ftA7MPMUEbsEI023aDY3RVegZ9OGKkCNhBm9LlKA+pHNj3Wx7bIzTAXg4u6px2rhFv94tX5qdN8U6pg22sqyoruMDCltTePTBThdFMOrMXeFVlR1HEW62dhFYRmWdiQJMaymzppWQosESd86J9DLqqGOFOwq4oYYDAE8vSXAjCOpQF7yn1KPOB07ClMNxzXlWMIn7c2ileUwz2IC2x7FO72hjJRhKMYQU1tCEMKAyJN6M054/sPXxF5QG5m7khPay4rHIEE2kpAq2hM3IF9RCh2okRgIx700Q1S3wGLaLiKXHqHDs/FKNJqX+7OHpdHsOBvMDFMSp17HZB0QUtc4UYqRRnRuyRFRFmgCUsoZrEA55GTGEd7eWQoRV0z+3h1LUy+p/sJ/aqQvBGttoWO6iDS46lynHQMicK5HVFXVMUGNsFiozVi1VrJM2IiT2PMpJGUm/fzmXCBOq7FEMn/bi1acSKOXZu124TJAWOiA3p/wH1mLLdlJdrkQAAAABJRU5ErkJggg=="
LOGO_B64 = "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"


def _mix(c1, c2, k):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * k) for x, y in zip(a, b))


# Лилии по всей панели. Дальние - тусклые, чтобы текст поверх них
# читался; ближние - яркие, по краям. Распускаются волной: сначала
# дальние, потом ближние.
#          x    y   масштаб  яркость  задержка
FLOWERS = [(64,  34, 0.70,    0.30,    0.0),
           (214, 150, 0.78,   0.28,    0.3),
           (128, 262, 0.66,   0.30,    0.5),
           (292, 300, 0.62,   0.32,    0.7),
           (22,  158, 0.58,   0.50,    1.0),
           (232, 30,  0.66,   0.48,    1.2),
           (330, 72,  1.05,   1.00,    1.5),
           (370, 212, 0.70,   1.00,    1.9),
           (18,  304, 0.56,   0.90,    2.2)]
BLOOM_SEC = 2.6


class SceneRenderer:
    """Фон окна - ОДНА картинка вместо ~1300 фигур на холсте (7.4).

    В 7.3 каждый лепесток и тычинка были отдельной сглаженной фигурой
    Tk. Любое обновление трекера или текста заставляло Windows заново
    рисовать все фигуры под ним, и частота детектора падала с 48 до
    30 кадров в секунду. Теперь сцена рисуется Pillow в картинку (с
    двойным разрешением и сглаживанием - так даже красивее), а холст
    просто показывает её. Во время распускания картинка обновляется
    ~12 раз в секунду, потом замирает и больше не стоит ничего."""

    SS = 2                       # сглаживание: рисуем вдвое крупнее

    def __init__(self, W, H, flowers=None):
        from PIL import Image, ImageDraw
        self.flowers = flowers or FLOWERS
        self.bloom_end = max(f[4] for f in self.flowers) + BLOOM_SEC
        self.Image, self.ImageDraw = Image, ImageDraw
        self.W, self.H = W, H
        S = self.SS
        # градиент и свечения - один раз
        top = np.array([int(BG[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
        bot = np.array([0x1c, 0x06, 0x09], np.float32)
        k = (np.linspace(0, 1, H * S) ** 1.6)[:, None, None]
        img = np.broadcast_to(top + (bot - top) * k, (H * S, W * S, 3)).copy()
        yy, xx = np.mgrid[0:H * S, 0:W * S].astype(np.float32)
        glow = np.array([0x2e, 0x07, 0x0d], np.float32)
        for fx, fy, sc, dim, _d in self.flowers:
            if dim < 0.8:
                continue
            r = np.hypot(xx - fx * S, yy - fy * S) / (S * 72 * sc)
            a = (np.clip(1 - r, 0, 1) ** 1.3 * 0.9)[:, :, None]
            img = img + (glow - img) * a
        self.base = Image.fromarray(img.astype(np.uint8))

    def render(self, t):
        S = self.SS
        im = self.base.copy()
        d = self.ImageDraw.Draw(im)
        for fx, fy, sc, dim, delay in self.flowers:
            bloom = max(0.0, min(1.0, (t - delay) / BLOOM_SEC))
            if bloom <= 0:
                continue
            for kind, pts, col, w in lily_shapes(fx, fy, sc, bloom, 0.0,
                                                 stem_len=self.H - fy + 30, dim=dim):
                if kind == "poly":
                    d.polygon([(x * S, y * S) for x, y in pts], fill=col)
                elif kind == "line" and len(pts) > 1:
                    d.line([(x * S, y * S) for x, y in pts], fill=col,
                           width=max(1, round(w * S)), joint="curve")
                elif kind == "oval":
                    d.ellipse([v * S for v in pts], fill=col)
        return im.resize((self.W, self.H), self.Image.LANCZOS)


def style_titlebar(win, caption=None):
    """Полоса заголовка Windows в цвет панели.

    Сама полоса рисуется системой, а не tkinter. Windows 11 позволяет
    задать ей цвет (атрибуты DWM 34-36). Windows 10 умеет только тёмный
    режим - там полоса станет чёрной. На других системах ничего не
    делаем. Своя полоса вместо системной отняла бы у окна перетаскивание
    к краю экрана, значок на панели задач и Alt+Tab - не стоит того."""
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        dwm = ctypes.windll.dwmapi

        def put(attr, value):
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v),
                                             ctypes.sizeof(v))

        def cref(hexcol):
            r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
            return r | (g << 8) | (b << 16)

        if put(20, 1) != 0:          # тёмный режим (Windows 10 20H1+)
            put(19, 1)               # ...и старый номер атрибута
        put(35, cref(caption or BG))  # цвет полосы (Windows 11)
        put(36, cref(FG))             # цвет текста
        put(34, cref(LINE))           # рамка окна
    except Exception:
        pass


def hover_red(b):
    """Обычная кнопка tkinter (окна настроек и проверки) под мышью
    наливается тем же тёмно-красным, что и кнопки главного окна."""
    base = b.cget("bg")
    b.bind("<Enter>", lambda e: b.config(bg=HOVER_RED, fg="#ffffff"))
    b.bind("<Leave>", lambda e: b.config(bg=base, fg=FG))
    b.config(activebackground=PRESS_RED, activeforeground="#ffffff")
    return b


def rrect(x1, y1, x2, y2, r):
    """Точки скруглённого прямоугольника для create_polygon(smooth=True)."""
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


HOVER_RED = "#8B0000"      # кнопка под мышью
PRESS_RED = "#4a0000"      # нажатая - ещё темнее


class CanvasButton:
    """Скруглённая кнопка с объёмом прямо на холсте.

    Наводишь - кнопка ПЛАВНО (за ~0.12 с) вырастает на 8% и наливается
    тёмно-красным #8B0000, вокруг проступает свечение, бортик снизу
    становится глубже. Нажимаешь - чуть сжимается и темнеет до #4a0000,
    как вдавленная клавиша. Команда срабатывает при отпускании над
    кнопкой, как у обычных кнопок Windows."""

    GROW = 0.08
    SHRINK = 0.05
    SPEED = 0.16          # доля пути к цели за кадр анимации (15 мс)

    def __init__(self, cv, x, y, w, text, cmd, h=26, r=10, tag_extra=()):
        self.cv, self.cmd, self.text = cv, cmd, text
        self.cx, self.cy, self.w, self.h, self.r = x + w / 2, y + h / 2, w, h, r
        self.tag = f"cb_{id(self)}"
        tags = (self.tag,) + tuple(tag_extra)
        mk = lambda **k: cv.create_polygon(0, 0, 0, 0, 0, 0, smooth=True,
                                           tags=tags, **k)
        self.glow = mk(fill=BG, outline="", state="hidden")
        self.side = mk(fill="#3a0b12", outline="")
        self.body = mk(fill=BTN, outline=LINE, width=1)
        self.gloss = mk(fill=BTN, outline="")
        self.label = cv.create_text(self.cx, self.cy, text=text, fill=FG,
                                    font=("Segoe UI", 9), tags=tags)
        self.s = 0.0          # наведение 0..1
        self.p = 0.0          # нажатие 0..1
        self.ts = self.tp = 0.0
        self.anim = False
        self.shown = True
        self.hover = False
        self._render()
        cv.tag_bind(self.tag, "<Enter>", self._enter)
        cv.tag_bind(self.tag, "<Leave>", self._leave)
        cv.tag_bind(self.tag, "<ButtonPress-1>", self._press)
        cv.tag_bind(self.tag, "<ButtonRelease-1>", self._release)

    def _render(self):
        cv, s, p = self.cv, self.s, self.p
        k = 1 + self.GROW * s - self.SHRINK * p
        w, h = self.w * k, self.h * k
        x1, y1 = self.cx - w / 2, self.cy - h / 2 - 2 * s + 2 * p
        x2, y2 = x1 + w, y1 + h
        r = self.r * k
        depth = 4 + 2 * s - 3 * p
        body = _mix(_mix(BTN, HOVER_RED, s), PRESS_RED, p)
        cv.coords(self.glow, *rrect(x1 - 5, y1 - 4, x2 + 5, y2 + depth + 4, r + 5))
        cv.coords(self.side, *rrect(x1, y1 + depth, x2, y2 + depth, r))
        cv.coords(self.body, *rrect(x1, y1, x2, y2, r))
        cv.coords(self.gloss, *rrect(x1 + 4, y1 + 2, x2 - 4, y1 + h * 0.46, r * 0.6))
        cv.coords(self.label, self.cx, (y1 + y2) / 2)
        cv.itemconfig(self.glow, fill=_mix(BG, HOVER_RED, 0.45 * s),
                      state="normal" if (s > 0.02 and self.shown) else "hidden")
        cv.itemconfig(self.side, fill=_mix("#3a0b12", "#1f0000", s))
        cv.itemconfig(self.body, fill=body,
                      outline=_mix(LINE, "#c1121f", s))
        cv.itemconfig(self.gloss, fill=_mix(body, "#ffffff", 0.05 + 0.07 * s))
        cv.itemconfig(self.label, fill=_mix(FG, "#ffffff", s),
                      font=("Segoe UI", 10 if s > 0.5 else 9,
                            "bold" if s > 0.5 else "normal"))

    def _animate(self):
        ds, dp = self.ts - self.s, self.tp - self.p
        if abs(ds) < 0.01 and abs(dp) < 0.01:
            self.s, self.p = self.ts, self.tp
            self._render()
            self.anim = False
            return
        # не быстрее SPEED за кадр, но и не медленнее 0.05 - без "хвоста"
        self.s += max(-self.SPEED * 1.6, min(self.SPEED * 1.6,
                      ds * 0.35 + (0.05 if ds > 0 else -0.05)))
        self.p += dp * 0.5 + (0.08 if dp > 0 else -0.08 if dp < 0 else 0)
        self.s = min(1.0, max(0.0, self.s))
        self.p = min(1.0, max(0.0, self.p))
        self._render()
        self.cv.after(15, self._animate)

    def _go(self, s=None, p=None):
        if s is not None:
            self.ts = s
        if p is not None:
            self.tp = p
        if not self.anim:
            self.anim = True
            self._animate()

    def _enter(self, _e=None):
        self.hover = True
        self._go(s=1.0)

    def _leave(self, _e=None):
        self.hover = False
        self._go(s=0.0, p=0.0)

    def _press(self, _e=None):
        self._go(p=1.0)

    def _release(self, _e=None):
        self._go(p=0.0)
        if self.hover:
            try:
                self.cmd()
            except Exception as e:
                say(f"кнопка: {e}")

    def show(self, on):
        self.shown = on
        self.cv.itemconfig(self.tag, state="normal" if on else "hidden")
        if on:
            self._render()


# ------------------------------------------------------------------
#  ОКНА (7.6): пульт Scarlet hub с выезжающим меню слева и окно статуса.
#  Меню выезжает по кнопке с тремя полосками или когда мышь подходит
#  к левому краю пульта. Все надписи - на выбранном языке (en / ru).
# ------------------------------------------------------------------
HUB_FLOWERS = [(150, 96, 0.55, 0.26, 0.4), (300, 14, 0.58, 0.28, 0.7),
               (430, 100, 0.60, 0.30, 1.0), (505, 44, 0.95, 1.00, 1.4),
               (575, 92, 0.62, 0.90, 1.8)]
STATUS_FLOWERS = [(60, 40, 0.62, 0.28, 0.0), (220, 150, 0.66, 0.26, 0.0),
                  (120, 206, 0.58, 0.30, 0.0), (330, 64, 0.95, 1.00, 0.0),
                  (372, 190, 0.62, 0.95, 0.0)]
DRAWER_FLOWERS = [(176, 352, 0.9, 0.9, 0.0), (70, 410, 0.6, 0.45, 0.0)]

# (англ., рус.) для каждой настройки
LABELS = {
    "CHARGE_TIME": ("Cast power (hold), s", "Сила заброса, с"),
    "CAST_GRACE": ("Wait after cast, s", "Пауза после заброса, с"),
    "BITE_TIMEOUT": ("Bite timeout, s", "Ждать поклёвку, с"),
    "AFTER_CATCH": ("Pause after catch, s", "Пауза после рыбы, с"),
    "MAX_TIMEOUTS": ("Empty casts before pause", "Пустых забросов до паузы"),
    "SHAKE_MODE": ("Shake style", "Способ shake"),
    "SHAKE_INTERVAL": ("Press interval", "Интервал нажатий"),
    "SHAKE_CLICKS": ("Click count", "Щелчков по кнопке"),
    "CAST_FPS": ("Bite check FPS", "Проверка поклёвки"),
    "CAST_STYLE": ("Cast style", "Способ заброса"),
    "PC_STYLE": ("Release style", "Как отпускать"),
    "PC_GREEN_TOL": ("Green color tolerance", "Допуск зелёного"),
    "PC_WHITE_TOL": ("White color tolerance", "Допуск белого"),
    "PC_FAIL": ("Fail scan timeout", "Не нашёл полоску - отпустить"),
    "PC_FPS": ("Scan FPS", "Смотреть на полоску"),
    "PC_EARLY_MS": ("Release early by", "Отпускать раньше на"),
    "PC_AUTOLAT": ("Auto-tune latency", "Подстраивать задержку"),
    "PC_B400": ("<400 px/s", "<400 px/с"),
    "PC_B600": ("400-600 px/s", "400-600 px/с"),
    "PC_B800": ("600-800 px/s", "600-800 px/с"),
    "PC_B1000": ("800-1000 px/s", "800-1000 px/с"),
    "PC_B1200": ("1000-1200 px/s", "1000-1200 px/с"),
    "PC_BMAX": (">1200 px/s", ">1200 px/с"),
    "PC_BFALL": ("Fallback (unknown)", "Скорость неизвестна"),
    "CAST_LEFT": ("Bar zone X", "Зона полоски: X"),
    "CAST_TOP": ("Bar zone Y", "Зона полоски: Y"),
    "CAST_WIDTH": ("Bar zone width", "Зона полоски: ширина"),
    "CAST_HEIGHT": ("Bar zone height", "Зона полоски: высота"),
    "FISH_FPS": ("Fish detection FPS", "Слежение в бою"),
    "SHAKE_TOL": ("Color tolerance", "Допуск цвета"),
    "SHAKE_DIST": ("Pixel distance tolerance", "Та же кнопка ближе"),
    "SHAKE_FPS": ("Scan FPS", "Поиск в секунду"),
    "SHAKE_DUP": ("Duplicate button timeout", "Помнить нажатую кнопку"),
    "SHAKE_FAIL": ("Fail cast timeout", "Перезаброс без shake"),
    "NAV_KEY": ("Nav key", "Клавиша навигации"),
    "AUTO_ROD": ("Auto select rod", "Переэкипировка удочки"),
    "ROD_KEY": ("Rod slot key", "Слот удочки"),
    "BAG_KEY": ("Bag slot key", "Слот рюкзака"),
    "NAV_PRESS": ("Press with", "Нажимать клавишей"),
    "NAV_AUTO": ("Enable navigation on start", "Включать навигацию на старте"),
    "SHAKE_VERIFY": ("Only inside button ring", "Только внутри кольца кнопки"),
    "SHAKE_LEFT": ("Zone X", "Зона: X"),
    "SHAKE_TOP": ("Zone Y", "Зона: Y"),
    "SHAKE_WIDTH": ("Zone width", "Зона: ширина"),
    "SHAKE_HEIGHT": ("Zone height", "Зона: высота"),
    "FIRST_PERSON": ("First-person camera", "Вид от первого лица"),
    "FP_SCROLLS": ("Zoom-in scrolls", "Щелчков колеса"),
    "GAIN": ("Gain", "Усиление"),
    "LEAD": ("Lead, s", "Упреждение, с"),
    "DAMP": ("Damping", "Гашение"),
    "BASE_NEUTRAL": ("Neutral duty", "Нейтраль"),
    "PERIOD": ("Click period, s", "Период нажатий, с"),
    "AIM_TAU": ("Aim smoothing, s", "Сглаживание цели, с"),
    "AIM_LEAD": ("Smoothing compensation", "Компенсация сглаживания"),
    "HOLD_REFRESH": ("Hold refresh, s", "Подтверждение нажатия, с"),
    "AB_TEST": ("A/B gains", "Сравнение GAIN"),
    "CAPTURE": ("Screen capture", "Захват экрана"),
    "TRACK_STYLE": ("Track style", "Способ слежения"),
    "SHOW_OVERLAY": ("On-screen markers", "Маркеры поверх игры"),
    "COL_TARGET": ("Target line", "Рыба (линия)"),
    "COL_ARROW": ("Arrow", "Стрелка"),
    "COL_LEFT": ("Left bar", "Левый край блока"),
    "COL_RIGHT": ("Right bar", "Правый край блока"),
    "TOL_TARGET": ("Target tolerance", "Допуск рыбы"),
    "TOL_ARROW": ("Arrow tolerance", "Допуск стрелки"),
    "TOL_LEFT": ("Left bar tolerance", "Допуск левого края"),
    "TOL_RIGHT": ("Right bar tolerance", "Допуск правого края"),
    "U_EDGE_FIX": ("Gradient edge fix", "Края блока-градиента"),
    "FISH_MODEL_ON": ("Background memory", "Память фона"),
    "U_PRESENT": ("Bar presence threshold", "Порог наличия полосы"),
    "BAR_LO": ("Bar left edge", "Левый край полосы"),
    "BAR_HI": ("Bar right edge", "Правый край полосы"),
    "XP_LEFT": ("XP zone X", "Зона XP: X"),
    "XP_TOP": ("XP zone Y", "Зона XP: Y"),
    "XP_WIDTH": ("XP zone width", "Зона XP: ширина"),
    "XP_HEIGHT": ("XP zone height", "Зона XP: высота"),
    "XP_FRAC": ("XP threshold", "Порог XP"),
    "CARD_LEFT": ("Card zone X", "Карточка: X"),
    "CARD_TOP": ("Card zone Y", "Карточка: Y"),
    "CARD_WIDTH": ("Card zone width", "Карточка: ширина"),
    "CARD_HEIGHT": ("Card zone height", "Карточка: высота"),
    "CARD_FRAC": ("Card threshold", "Порог карточки"),
    "CARD_WAIT": ("Result wait, s", "Ждать итог, с"),
    "PROG_LEFT": ("Progress X", "Прогресс: X"),
    "PROG_TOP": ("Progress Y", "Прогресс: Y"),
    "PROG_WIDTH": ("Progress width", "Прогресс: ширина"),
    "PROG_HEIGHT": ("Progress height", "Прогресс: высота"),
    "USE_GUI": ("Show window", "Показывать окно"),
    "SHOW_VIEW": ("Console view", "Схема в консоли"),
    "TRACE_CATCHES": ("Trace first N fish", "Trace для N рыб"),
    "DEBUG_MODE": ("Debug mode (snapshots, trace)", "Режим отладки (снимки, trace)"),
    "CHECK_UPDATES": ("Check for updates", "Проверять обновления"),
    "TRACE_ALL": ("Trace lost fish too", "Trace и для сорвавшихся"),
    "SAVE_AFTER": ("Result snapshots", "Снимков итога"),
    "BURST_SEC": ("Burst length, s", "Длина серии, с"),
    "BURST_EVERY": ("Burst frame step", "Шаг серии"),
}

# Что на какой вкладке. Сами настройки (границы, тип) - в SETTINGS_TABS.
HUB_TABS = [
    ("main", "\u2302", "Main", [
        ("start",), ("fields", "\u2693", ("Auto select rod", "Переэкипировка удочки"),
                     ["AUTO_ROD", "ROD_KEY", "BAG_KEY"]),
        ("rod_seq",), ("perf",), ("check",), ("keys",)]),
    ("cast", "\u27a4", "Cast", [
        ("cast_style",), ("cast_seq",), ("cast_perfect",),
        ("fields", "\u25d4", ("Timing", "Время"), ["CAST_GRACE", "BITE_TIMEOUT",
                                                    "AFTER_CATCH", "MAX_TIMEOUTS"]),
        ("fields", "\u25c9", ("Camera", "Камера"), ["FIRST_PERSON", "FP_SCROLLS"]),
        ("fields", "\u25f7", ("Detection", "Детекция"), ["CAST_FPS"])]),
    ("shake", "\u2248", "Shake", [("shake_style",), ("shake_opts",), ("shake_zone",)]),
    ("fish", "\u25c8", "Fish", [
        ("rod",),
        ("fields", "\u25ce", ("Tracking", "Слежение"), ["TRACK_STYLE", "FISH_FPS",
                                                          "SHOW_OVERLAY"]),
        ("colors",),
        ("fields", "\u25c8", ("Block control", "Управление блоком"),
         ["GAIN", "LEAD", "DAMP", "BASE_NEUTRAL", "PERIOD", "AIM_TAU", "AIM_LEAD",
          "HOLD_REFRESH", "AB_TEST"]),
        ("fields", "\u25ce", ("Detector", "Детектор"),
         ["CAPTURE", "U_EDGE_FIX", "FISH_MODEL_ON", "U_PRESENT", "BAR_LO", "BAR_HI"])]),
    ("extra", "\u2726", "Extra", [
        ("community",),
        ("lang",),
        ("log",),
        ("fields", "\u2605", ("Catch result", "Итог боя"),
         ["XP_LEFT", "XP_TOP", "XP_WIDTH", "XP_HEIGHT", "XP_FRAC", "CARD_LEFT",
          "CARD_TOP", "CARD_WIDTH", "CARD_HEIGHT", "CARD_FRAC", "CARD_WAIT"]),
        ("fields", "\u25ad", ("Progress bar", "Полоса прогресса"),
         ["PROG_LEFT", "PROG_TOP", "PROG_WIDTH", "PROG_HEIGHT"]),
        ("fields", "\u270e", ("Debug & window", "Отладка и окно"),
         ["DEBUG_MODE", "CHECK_UPDATES", "USE_GUI", "SHOW_VIEW", "TRACE_CATCHES",
          "TRACE_ALL", "SAVE_AFTER", "BURST_SEC", "BURST_EVERY"]),
        ("about",)]),
]
SPEC = {f[0]: f for _t, _k, fl in SETTINGS_TABS for f in fl}
TAB_CARDS = {k: cs for k, _i, _t, cs in HUB_TABS}
LANGS = [("en", "English"), ("ru", "Русский")]
# Ссылки сообщества. Пока пустые - кнопки скажут, что ссылка скоро будет.
DISCORD_URL = "https://discord.gg/hrCgzrE2DH"
# Репозиторий на GitHub в виде "владелец/имя" - по нему и ссылка на
# GitHub, и проверка обновлений. Пока пусто - кнопка скажет "скоро".
GITHUB_REPO = "HovDor/Scarlet-Hub"
GITHUB_URL = f"https://github.com/{GITHUB_REPO}" if GITHUB_REPO else ""
# подписи пунктов меню
TAB_SUB = {"main": ("start \u00b7 check \u00b7 keys", "старт \u00b7 проверка \u00b7 клавиши"),
           "cast": ("cast \u00b7 camera", "заброс \u00b7 камера"),
           "shake": ("shake mode", "режим shake"),
           "fish": ("rod \u00b7 control \u00b7 detector", "удочка \u00b7 управление \u00b7 детектор"),
           "extra": ("community \u00b7 log \u00b7 more", "сообщество \u00b7 журнал \u00b7 прочее")}
ON_RED = "#8B0000"
CHOICE_LABELS = {"line": "Line", "color": "Color", "mss": "MSS", "dxcam": "DXCAM",
                 "enter": "Enter", "space": "Space", "velocity": "Velocity %",
                 "green": "Green zone"}
STEP_UI = {"delay": ("\u25f7", "Delay", "Пауза", "s"),
           "bag": ("\u25a3", "Equipment bag", "Рюкзак", None),
           "rod": ("\u2693", "Fishing rod", "Удочка", None),
           "hold": ("\u25cf", "Hold left click", "Зажать ЛКМ", None),
           "release": ("\u25cb", "Release left click", "Отпустить ЛКМ", None),
           "zoom_out": ("\u2296", "Zoom out", "Отдалить камеру", ""),
           "zoom_in": ("\u2295", "Zoom in", "Приблизить камеру", ""),
           "look_down": ("\u2193", "Look down", "Взгляд вниз", "px"),
           "look_up": ("\u2191", "Look up", "Взгляд вверх", "px"),
           "perfect": ("\u2605", "Perfect cast release", "Отпустить в зелёном", None)}
# какие настройки показывать у каждого способа shake
SHAKE_OPTS = {
    "pixel": ["SHAKE_CLICKS", "SHAKE_TOL", "SHAKE_VERIFY", "SHAKE_DIST", "SHAKE_FPS",
              "SHAKE_DUP", "SHAKE_FAIL"],
    "navigation": ["NAV_KEY", "NAV_PRESS", "NAV_AUTO", "SHAKE_INTERVAL", "SHAKE_TOL",
                   "SHAKE_FPS", "SHAKE_FAIL"],
    "circle": ["SHAKE_CLICKS", "SHAKE_DIST", "SHAKE_FPS", "SHAKE_DUP", "SHAKE_FAIL"],
    "disabled": [],
}
SHAKE_NAMES = {"pixel": ("Pixel", "Пиксель"), "navigation": ("Navigation", "Навигация"),
               "circle": ("Circle", "Круг"), "disabled": ("Disabled", "Выключено")}


def _fmt_s(v):
    return f"{v:g}s"


# ползунки: (от, до, шаг, как показать значение)
SLIDERS = {
    "SHAKE_CLICKS": (1, 5, 1, lambda v: f"{int(v)}"),
    "SHAKE_TOL": (0, 80, 1, lambda v: f"{int(v)}"),
    "SHAKE_DIST": (0, 100, 1, lambda v: f"{int(v)} px"),
    "SHAKE_FPS": (10, 240, 10, lambda v: f"{int(v)} ({1000 / max(1, v):.1f}ms)"),
    "SHAKE_DUP": (0, 5, 0.1, _fmt_s),
    "SHAKE_FAIL": (0, 15, 0.5, lambda v: _fmt_s(v) if v > 0 else "off"),
    "SHAKE_INTERVAL": (0.05, 2, 0.05, _fmt_s),
    "CAST_FPS": (0, 120, 5, lambda v: f"{int(v)} fps" if v > 0 else "max"),
    "PC_GREEN_TOL": (0, 80, 1, lambda v: f"{int(v)}"),
    "PC_WHITE_TOL": (0, 120, 1, lambda v: f"{int(v)}"),
    "PC_FAIL": (0.5, 10, 0.5, _fmt_s),
    "PC_FPS": (10, 500, 10, lambda v: f"{int(v)} ({1000 / max(1, v):.1f}ms)"),
    "PC_EARLY_MS": (0, 300, 5, lambda v: f"{int(v)} ms"),
    **{k: (50, 100, 1, lambda v: f"{int(v)}%") for k in
       ("PC_B400", "PC_B600", "PC_B800", "PC_B1000", "PC_B1200", "PC_BMAX", "PC_BFALL")},
    "FISH_FPS": (0, 240, 10, lambda v: f"{int(v)} fps" if v > 0 else "max"),
}


class Slider:
    """Ползунок: дорожка, заливка тёмно-красным до кружка, число справа.
    Щёлкнуть по дорожке или тащить кружок."""

    def __init__(self, cv, x1, x2, cy, lo, hi, step, fmt, on_change, is_int):
        self.cv, self.x1, self.x2, self.cy = cv, x1, x2, cy
        self.lo, self.hi, self.step, self.fmt = lo, hi, step, fmt
        self.on_change, self.is_int = on_change, is_int
        self.v = lo
        self.tag = f"sl_{id(self)}"
        cv.create_polygon(*rrect(x1, cy - 3, x2, cy + 3, 3), smooth=True,
                          fill="#2a0d12", outline="", tags=self.tag)
        self.fill_ = cv.create_polygon(*rrect(x1, cy - 3, x1 + 6, cy + 3, 3), smooth=True,
                                       fill=ON_RED, outline="", tags=self.tag)
        self.knob = cv.create_oval(0, 0, 0, 0, fill="#ffffff", outline="#c1121f",
                                   width=2, tags=self.tag)
        self.text = cv.create_text(x2 + 96, cy, anchor="e", text="", fill=FG,
                                   font=("Consolas", 9, "bold"))
        cv.tag_bind(self.tag, "<Button-1>", self._drag)
        cv.tag_bind(self.tag, "<B1-Motion>", self._drag)
        cv.tag_bind(self.tag, "<Enter>", lambda e: cv.itemconfig(self.knob, fill=OK_C))
        cv.tag_bind(self.tag, "<Leave>", lambda e: cv.itemconfig(self.knob, fill="#ffffff"))
        self._draw()

    def _draw(self):
        k = (self.v - self.lo) / (self.hi - self.lo) if self.hi > self.lo else 0
        x = self.x1 + (self.x2 - self.x1) * k
        self.cv.coords(self.fill_, *rrect(self.x1, self.cy - 3, max(self.x1 + 6, x),
                                          self.cy + 3, 3))
        self.cv.coords(self.knob, x - 8, self.cy - 8, x + 8, self.cy + 8)
        self.cv.itemconfig(self.text, text=self.fmt(self.v))

    def _drag(self, e):
        x = self.cv.canvasx(e.x) if hasattr(self.cv, "canvasx") else e.x
        k = min(1.0, max(0.0, (x - self.x1) / (self.x2 - self.x1)))
        self.set(self.lo + k * (self.hi - self.lo), user=True)

    def set(self, v, user=False):
        v = float(v)
        v = round(round((v - self.lo) / self.step) * self.step + self.lo, 6)
        v = min(self.hi, max(self.lo, v))
        if v != self.v:
            self.v = v
            self._draw()
            if user:
                self.on_change()
        else:
            self._draw()

    def get(self):
        return str(int(self.v)) if self.is_int else repr(round(self.v, 4))
COLOR_ROWS = ["TARGET", "ARROW", "LEFT", "RIGHT"]


def rod_icon(pid, size=38):
    """Значок удочки. Если рядом с программой лежит rods/<удочка>.png
    (например, свой скриншот из игры), берём его. Иначе рисуем свой
    значок в цвет удочки: удилище, катушка, леска с крючком."""
    from PIL import Image, ImageDraw
    p = os.path.join(APP_DIR, "rods", f"{pid or 'any'}.png")
    if os.path.exists(p):
        try:
            return Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            pass
    S_ = 4
    n = size * S_
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = rod_color(pid)
    rgb = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
    dark = tuple(max(0, c // 3) for c in rgb)
    # фон-медальон
    d.ellipse((2 * S_, 2 * S_, n - 2 * S_, n - 2 * S_), fill=(20, 7, 9, 255),
              outline=rgb + (255,), width=2 * S_)
    # удилище по диагонали
    x0, y0, x1, y1 = n * 0.26, n * 0.76, n * 0.76, n * 0.22
    d.line((x0, y0, x1, y1), fill=dark + (255,), width=int(4.2 * S_))
    d.line((x0, y0, x1, y1), fill=rgb + (255,), width=int(2.6 * S_))
    # рукоять и катушка
    d.line((x0, y0, x0 + n * 0.1, y0 - n * 0.1), fill=(40, 20, 20, 255), width=int(4.6 * S_))
    cx, cy, r = n * 0.39, n * 0.62, n * 0.075
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(230, 230, 230, 255),
              outline=dark + (255,), width=S_)
    # леска и крючок
    d.line((x1, y1, x1, n * 0.62), fill=(235, 235, 235, 220), width=S_)
    d.arc((x1 - n * 0.06, n * 0.58, x1 + n * 0.02, n * 0.7), 0, 200,
          fill=(235, 235, 235, 255), width=S_)
    return im.resize((size, size), Image.LANCZOS)


def load_images(master):
    """Иконка окна и логотип из встроенных PNG. None, если нет Pillow."""
    try:
        import base64
        import io as _io
        from PIL import Image, ImageTk
        mk = lambda b: ImageTk.PhotoImage(Image.open(_io.BytesIO(base64.b64decode(b))),
                                          master=master)
        return {"i64": mk(ICON64_B64), "i32": mk(ICON32_B64), "logo": mk(LOGO_B64)}
    except Exception:
        return {}


class Toggle:
    """Переключатель: капсула, кружок плавно едет, включённый - #8B0000."""

    def __init__(self, cv, x2, cy, on_change, tags=()):
        self.cv, self.on_change = cv, on_change
        self.x1, self.y1 = x2 - 44, cy - 11
        self.v, self.k = False, 0.0
        self.tag = f"tg_{id(self)}"
        t = (self.tag,) + tuple(tags)
        self.body = cv.create_polygon(*rrect(self.x1, self.y1, self.x1 + 44,
                                             self.y1 + 22, 11), smooth=True,
                                      fill="#2a0d12", outline=LINE, tags=t)
        self.knob = cv.create_oval(0, 0, 0, 0, fill=DIM, outline="", tags=t)
        cv.tag_bind(self.tag, "<Button-1>", lambda e: self.set(not self.v, True))
        self._draw()

    def _draw(self):
        x = self.x1 + 11 + 22 * self.k
        self.cv.coords(self.knob, x - 8, self.y1 + 3, x + 8, self.y1 + 19)
        self.cv.itemconfig(self.body, fill=_mix("#2a0d12", ON_RED, self.k),
                           outline=_mix(LINE, "#c1121f", self.k))
        self.cv.itemconfig(self.knob, fill=_mix(DIM, "#ffffff", self.k))

    def _anim(self):
        goal = 1.0 if self.v else 0.0
        self.k += max(-0.25, min(0.25, goal - self.k))
        self._draw()
        if abs(self.k - goal) > 1e-3:
            self.cv.after(15, self._anim)

    def set(self, v, user=False):
        self.v = bool(v)
        if user:
            self._anim()
            self.on_change()
        else:
            self.k = 1.0 if self.v else 0.0
            self._draw()

    def get(self):
        return "1" if self.v else "0"


class Segmented:
    """Ряд капсул, выбранная - тёмно-красная."""

    def __init__(self, cv, x2, cy, options, on_change, labels=None, w=70, tags=()):
        self.cv, self.on_change, self.options = cv, on_change, list(options)
        self.v = self.options[0]
        x1 = x2 - w * len(self.options)
        self.cells = []
        for i, o in enumerate(self.options):
            tag = f"sg_{id(self)}_{i}"
            t = (tag,) + tuple(tags)
            b = cv.create_polygon(*rrect(x1 + i * w + 2, cy - 12, x1 + (i + 1) * w - 2,
                                         cy + 12, 10), smooth=True, fill=BTN,
                                  outline=LINE, tags=t)
            l_ = cv.create_text(x1 + i * w + w / 2, cy, text=(labels or {}).get(o, o),
                                fill=DIM, font=("Segoe UI", 9, "bold"), tags=t)
            cv.tag_bind(tag, "<Button-1>", lambda e, o=o: self.set(o, True))
            self.cells.append((o, b, l_))
        self._draw()

    def _draw(self):
        for o, b, l_ in self.cells:
            on = o == self.v
            self.cv.itemconfig(b, fill=ON_RED if on else BTN,
                               outline="#c1121f" if on else LINE)
            self.cv.itemconfig(l_, fill="#ffffff" if on else DIM)

    def set(self, v, user=False):
        if v in self.options:
            self.v = v
            self._draw()
            if user:
                self.on_change()
        elif not user:
            self.v = None          # значение не из списка - ничего не выделяем
            self._draw()

    def get(self):
        return self.v


def raise_widget(w):
    """Поднять виджет над соседями. НЕ w.lift(): у Canvas в tkinter lift -
    это tag_raise (поднять ФИГУРЫ по тегу), без тега он падает. Именно
    так 7.6 закрывалась при запуске."""
    import tkinter as tk
    tk.Misc.tkraise(w)


def log_crash(where, txt):
    """Ошибка -> error.log и журнал. Раньше ошибка окна не писалась
    никуда: программа просто закрывалась."""
    try:
        with open(os.path.join(APP_DIR, "error.log"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + where + "\n" + txt + "\n")
    except Exception:
        pass
    try:
        say(f"!!! ошибка ({where}) - записал в error.log")
    except Exception:
        pass


# ------------------------------------------------------------------
#  Приветствие: рукописное "hello", которое плавно выписывается одной
#  линией. Линия своя (точки ниже), не чей-то шрифт: сплайн Катмулла-Рома
#  через опорные точки, база строки y=190.
# ------------------------------------------------------------------
HELLO_PTS = [
 (40,196),(62,186),(88,160),(112,120),(128,82),(134,56),(126,40),(112,46),(104,78),
 (100,120),(96,160),(92,194),                     # h: петля и стойка вниз
 (98,170),(112,140),(132,128),(148,138),(152,162),(154,188),   # h: горб
 (166,192),(186,178),(210,160),(224,144),(222,130),(208,128),(194,140),(190,162),(198,184),(216,194),(240,190),  # e
 (262,170),(284,128),(298,80),(302,52),(294,40),(282,48),(276,84),(274,130),(276,170),(286,192),(304,192),  # l
 (326,170),(348,128),(362,80),(366,52),(358,40),(346,48),(340,84),(338,130),(340,170),(350,192),(368,192),  # l
 (390,176),(406,152),(426,136),(446,138),(458,156),(456,180),(440,194),(420,194),(408,180),(410,158),(426,140),  # o
 (448,136),(472,140),(500,138),
]


def hello_curve(per=14):
    """Точки линии "hello" (сплайн через HELLO_PTS), шаг ~1-2 px."""
    P = np.array(HELLO_PTS, float)
    P = np.vstack([P[0], P, P[-1]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0, 1, per, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P[-2])
    return np.array(out)


def run_gui():
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()

    def _cb_error(exc, val, tb):
        import traceback
        log_crash("окно", "".join(traceback.format_exception(exc, val, tb)))
    root.report_callback_exception = _cb_error
    root.title(APP_NAME)
    root.configure(bg=BG)
    HW, HH = 600, 640
    root.geometry(f"{HW}x{HH}")
    root.minsize(HW, 460)
    style_titlebar(root)
    imgs = load_images(root)
    if imgs:
        try:
            root.iconphoto(True, imgs["i64"], imgs["i32"])
        except Exception:
            pass
    t_start = time.perf_counter()
    HEAD, FOOT, DW = 104, 56, 232
    ui = {}                            # всё, что строит build_ui
    S = dict(cur="main", dirty=False, filling=False, dk=0.0, dgoal=0.0,
             danim=False, st_visible=False, bloomed=False, t_frame=0.0,
             last_seq=-1)
    fields = {}
    cache = {}

    # ---------------- мелочи ----------------
    def setc(cv, item, **kw):
        """Трогаем холст, только если что-то правда поменялось."""
        key = (id(cv), item, "cfg")
        val = tuple(sorted(kw.items()))
        if cache.get(key) != val:
            cache[key] = val
            cv.itemconfig(item, **kw)

    def setxy(cv, item, *xy):
        xy = tuple(round(v) for v in xy)
        key = (id(cv), item, "xy")
        if cache.get(key) != xy:
            cache[key] = xy
            cv.coords(item, *xy)

    def note(text, col=DIM):
        if "foot" in ui:
            ui["foot"].itemconfig(ui["note"], text=text, fill=col)

    def mark_dirty(*_a):
        if S["filling"]:
            return
        if not S["dirty"]:
            S["dirty"] = True
            note(tr("Unsaved changes", "Есть несохранённые изменения"), WARN_C)

    def rod_name():
        return PROFILE or tr("general", "общая")

    # ---------------- карточки ----------------
    def card(cv, y, title, icon, rows_h):
        x1, x2 = 16, HW - 16
        h = 40 + rows_h + 8
        cv.create_polygon(*rrect(x1, y + 3, x2, y + h + 3, 12), smooth=True,
                          fill="#060203", outline="")
        cv.create_polygon(*rrect(x1, y, x2, y + h, 12), smooth=True,
                          fill=PANEL, outline=LINE)
        cv.create_text(x1 + 18, y + 20, text=icon, fill=ACC,
                       font=("Segoe UI", 12, "bold"))
        cv.create_text(x1 + 36, y + 20, anchor="w", text=title.upper(), fill=FG,
                       font=("Segoe UI", 9, "bold"))
        cv.create_line(x1 + 14, y + 38, x2 - 14, y + 38, fill=_mix(PANEL, LINE, 0.7))
        return y + h + 14, y + 40

    def field_row(cv, y, name):
        spec = SPEC[name][2]
        x2 = HW - 34
        cy = y + 20
        lab = cv.create_text(34, cy, anchor="w", fill=FG, font=("Segoe UI", 10),
                             text=LABELS[name][1 if LANG == "ru" else 0])
        # метка справа от подписи: своё значение удочки / нужен перезапуск
        mark = cv.create_text(0, cy, anchor="w", text="", fill=ACC,
                              font=("Segoe UI", 9, "bold"))
        kind = spec[0]
        if name in SLIDERS:
            lo_, hi_, st_, fm_ = SLIDERS[name]
            w = Slider(cv, x2 - 290, x2 - 110, cy, lo_, hi_, st_, fm_, mark_dirty,
                       kind == "int")
            get, put = w.get, (lambda v, w=w: w.set(v))
        elif name in ("NAV_KEY", "ROD_KEY", "BAG_KEY"):
            w = KeyChip(cv, x2, cy, mark_dirty)
            get, put = w.get, (lambda v, w=w: w.set(str(v)))
        elif kind == "bool":
            w = Toggle(cv, x2, cy, mark_dirty)
            get, put = w.get, (lambda v, w=w: w.set(v))
        elif kind == "choice":
            w = Segmented(cv, x2, cy, spec[1], mark_dirty, labels=CHOICE_LABELS, w=70)
            get, put = w.get, (lambda v, w=w: w.set(str(v)))
        else:
            w = tk.Entry(cv, width=14 if kind == "str" else 9, bg=FIELD, fg=FG,
                         insertbackground=FG, relief="flat", justify="center",
                         font=("Consolas", 10), highlightthickness=1,
                         highlightbackground=LINE, highlightcolor=ACC)
            cv.create_window(x2, cy, window=w, anchor="e", height=24)
            w.bind("<KeyRelease>", mark_dirty)
            get = w.get

            def put(v, e=w):
                e.delete(0, "end")
                e.insert(0, ("1" if v else "0") if isinstance(v, bool) else _fmt_val(v))
        fields[name] = dict(spec=spec, get=get, put=put, w=w, cv=cv, lab=lab, mark=mark)
        return y + 40

    class KeyChip:
        """Клавиша, которую назначают нажатием: щёлкнул - нажал клавишу."""

        def __init__(self, cv, x2, cy, on_change):
            self.cv, self.on_change, self.v = cv, on_change, ""
            self.tag = f"kc_{id(self)}"
            self.box = cv.create_polygon(*rrect(x2 - 90, cy - 13, x2, cy + 13, 8),
                                         smooth=True, fill=BTN, outline=LINE, tags=self.tag)
            self.txt = cv.create_text(x2 - 45, cy, text="", fill=FG,
                                      font=("Consolas", 11, "bold"), tags=self.tag)
            cv.tag_bind(self.tag, "<Button-1>", lambda e: self.capture())
            cv.tag_bind(self.tag, "<Enter>", lambda e: cv.itemconfig(self.box, outline=ACC))
            cv.tag_bind(self.tag, "<Leave>", lambda e: cv.itemconfig(self.box, outline=LINE))

        def set(self, v):
            self.v = v
            self.cv.itemconfig(self.txt, text=key_label(v) if v else "\u2014")

        def get(self):
            return self.v

        def capture(self):
            global hotkey_capture
            hotkey_capture = True
            self.cv.itemconfig(self.box, fill=ON_RED, outline="#c1121f")
            self.cv.itemconfig(self.txt, text="\u2026")
            note(tr("Press a key (Esc - cancel)", "Нажми клавишу (Esc - отмена)"), WARN_C)
            root.focus_force()

            def done(e):
                global hotkey_capture
                root.unbind("<Key>")
                hotkey_capture = False
                self.cv.itemconfig(self.box, fill=BTN, outline=LINE)
                if e.keysym != "Escape":
                    k = (e.char if e.char and e.char.isprintable() and e.char != " "
                         else TK_KEYS.get(e.keysym, e.keysym.lower()))
                    self.set(k.lower())
                    self.on_change()
                    note(tr("Press Save to apply", "Нажми Сохранить"), WARN_C)
                else:
                    self.set(self.v)
                    note("")
            root.bind("<Key>", done)

    # ---------------- Cast ----------------
    def build_cast_style(cv, y):
        y_next, yy = card(cv, y, tr("Cast configuration", "Настройка заброса"), "\u25ce", 48)
        cv.create_text(34, yy + 24, anchor="w", fill=FG, font=("Segoe UI", 10),
                       text=LABELS["CAST_STYLE"][1 if LANG == "ru" else 0])
        seg = Segmented(cv, HW - 34, yy + 24, ["normal", "perfect"],
                        lambda: set_cast_style(seg.get()),
                        labels={"normal": tr("Normal", "Обычный"),
                                "perfect": tr("Perfect", "Идеальный")}, w=110)
        seg.set(CAST_STYLE)
        return y_next

    def set_cast_style(m):
        if m == CAST_STYLE:
            return
        if not confirm_discard():
            rebuild_page("cast")
            return
        ini_write({"CAST_STYLE": m})
        reload_config()
        say(f"заброс: {m}")
        S["dirty"] = False
        S.pop("seq_work", None)
        rebuild_page("cast")

    def seq_key():
        return "CAST_SEQ_PERFECT" if CAST_STYLE == "perfect" else "CAST_SEQ_NORMAL"

    # key настройки -> (вкладка, какие шаги можно добавлять)
    def seq_meta(key):
        if key == "ROD_SEQ":
            return "main", ROD_STEPS
        allowed = [k for k in CAST_STEPS if k != "perfect" or CAST_STYLE == "perfect"]
        return "cast", allowed

    def build_cast_seq(cv, y):
        title = tr("Perfect cast sequence", "Шаги идеального заброса") \
            if CAST_STYLE == "perfect" else tr("Normal cast sequence", "Шаги обычного заброса")
        return seq_card(cv, y, seq_key(), title)

    def build_rod_seq(cv, y):
        return seq_card(cv, y, "ROD_SEQ", tr("Auto select rod sequence",
                                             "Шаги переэкипировки"))

    def seq_card(cv, y, key, title):
        """Список шагов: значок, название, число (если есть), ↑ ↓ ✕;
        внизу - добавить шаг и вернуть шаги по умолчанию."""
        work = S.setdefault("seq_work", {})
        if key not in work:
            work[key] = parse_seq(globals()[key])
        seq = work[key]
        RH = 44
        y_next, yy = card(cv, y, title, "\u2630", RH * len(seq) + 48)
        x1, x2 = 30, HW - 30
        if seq:
            cv.create_line(HW / 2, yy + 20, HW / 2, yy + RH * len(seq) - 8,
                           fill=_mix(PANEL, LINE, 0.9), width=2)
        for i, (kind, val) in enumerate(seq):
            ry = yy + 6 + i * RH
            ic, en, ru, unit = STEP_UI[kind]
            cv.create_polygon(*rrect(x1, ry, x2, ry + RH - 8, 10), smooth=True,
                              fill=_mix(PANEL, "#000000", 0.35), outline=_mix(PANEL, LINE, 0.9))
            cv.create_polygon(*rrect(x1 + 8, ry + 6, x1 + 34, ry + RH - 14, 6), smooth=True,
                              fill=BTN, outline=LINE)
            cv.create_text(x1 + 21, ry + (RH - 8) / 2, text=ic, fill=ACC,
                           font=("Segoe UI", 11, "bold"))
            name = ru if LANG == "ru" else en
            if kind == "rod":
                name += f"  [{key_label(ROD_KEY)}]"
            elif kind == "bag":
                name += f"  [{key_label(BAG_KEY)}]"
            cv.create_text(x1 + 48, ry + (RH - 8) / 2, anchor="w", fill=FG,
                           font=("Segoe UI", 10, "bold"), text=name)
            for j, (sym, act) in enumerate((("\u2191", -1), ("\u2193", +1), ("\u2715", 0))):
                tg = f"sq_{key}_{i}_{j}"
                cv.create_text(x2 - 16 - (2 - j) * 22, ry + (RH - 8) / 2, text=sym, tags=tg,
                               fill=BAD_C if act == 0 else DIM, font=("Segoe UI", 10, "bold"))
                cv.tag_bind(tg, "<Button-1>", lambda e, i=i, act=act: seq_edit(key, i, act))
            if unit is not None:
                e = tk.Entry(cv, width=7, bg=FIELD, fg=FG, insertbackground=FG,
                             relief="flat", justify="center", font=("Consolas", 10),
                             highlightthickness=1, highlightbackground=LINE,
                             highlightcolor=ACC)
                e.insert(0, f"{val:g}")
                cv.create_window(x2 - 86, ry + (RH - 8) / 2, window=e, anchor="e", height=24)

                def upd(_e=None, i=i, e=e):
                    try:
                        v = float(e.get().replace(",", "."))
                    except ValueError:
                        return
                    k_, _v = work[key][i]
                    work[key][i] = (k_, v)
                    mark_dirty()
                e.bind("<KeyRelease>", upd)
        by = yy + 6 + len(seq) * RH + 4
        CanvasButton(cv, x1, by, 150, tr("+  Add step", "+  Добавить шаг"),
                     lambda: add_step_menu(key), h=28)
        CanvasButton(cv, x1 + 160, by, 130, tr("Default steps", "Шаги по умолч."),
                     lambda: seq_reset(key), h=28)
        fields[key] = dict(spec=("str",), get=lambda: seq_text(work[key]),
                           put=lambda v: None, w=None, cv=cv, lab=None, mark=None)
        return y_next

    def seq_edit(key, i, act):
        seq = S["seq_work"][key]
        if act == 0:
            seq.pop(i)
        else:
            j = i + act
            if 0 <= j < len(seq):
                seq[i], seq[j] = seq[j], seq[i]
        mark_dirty()
        rebuild_page(seq_meta(key)[0], keep=True)

    def add_step_menu(key):
        m = tk.Menu(root, tearoff=0, bg=PANEL, fg=FG, activebackground=ON_RED,
                    activeforeground="#ffffff", bd=0)
        for k in seq_meta(key)[1]:
            ic, en, ru, _u = STEP_UI[k]
            m.add_command(label=f"{ic}  {ru if LANG == 'ru' else en}",
                          command=lambda k=k: add_step(key, k))
        try:
            m.tk_popup(root.winfo_pointerx(), root.winfo_pointery())
        finally:
            m.grab_release()

    def add_step(key, k):
        S["seq_work"][key].append((k, CAST_STEP_VALUE.get(k)))
        mark_dirty()
        rebuild_page(seq_meta(key)[0], keep=True)

    def seq_reset(key):
        S["seq_work"][key] = parse_seq(_REG[key])
        mark_dirty()
        rebuild_page(seq_meta(key)[0], keep=True)

    def build_cast_perfect(cv, y):
        if CAST_STYLE != "perfect":
            return y
        names = ["PC_STYLE"]
        if PC_STYLE != "velocity":
            names += ["PC_EARLY_MS", "PC_AUTOLAT"]
        names += ["PC_GREEN_TOL", "PC_WHITE_TOL", "PC_FAIL", "PC_FPS"]
        y_next, yy = card(cv, y, tr("Perfect cast release", "Отпускание в зелёном"),
                          "\u2605", 40 * len(names))
        for n in names:
            yy = field_row(cv, yy, n)
        y = y_next
        if PC_STYLE == "velocity":
            bands = ["PC_B400", "PC_B600", "PC_B800", "PC_B1000", "PC_B1200", "PC_BMAX",
                     "PC_BFALL"]
            y_next, yy = card(cv, y, tr("Velocity-percentage bands", "Скорость и процент"),
                              "\u2197", 40 * len(bands) + 26)
            cv.create_text(34, yy + 12, anchor="w", fill=HINT, font=("Segoe UI", 8),
                           text=tr("Release at % of the way to green, by fill speed. "
                                   "Higher = later",
                                   "На каком % пути до зелёного отпускать - по скорости "
                                   "заливки. Больше - позже"))
            yy += 26
            for n in bands:
                yy = field_row(cv, yy, n)
            y = y_next
        names = ["CAST_LEFT", "CAST_TOP", "CAST_WIDTH", "CAST_HEIGHT"]
        y_next, yy = card(cv, y, tr("Cast bar zone", "Зона полоски"), "\u25a2", 40 * len(names))
        for n in names:
            yy = field_row(cv, yy, n)
        return y_next

    # ---------------- Main: производительность ----------------
    PERF_NAMES = {"low": ("Low", "Слабый ПК"), "medium": ("Medium", "Средний"),
                  "high": ("High", "Мощный")}

    def cur_perf():
        now_ = (CAST_FPS, SHAKE_FPS, FISH_FPS)
        return next((k for k, v in PERF_PRESETS.items() if v == now_), "custom")

    def build_perf(cv, y):
        y_next, yy = card(cv, y, tr("Performance", "Производительность"), "\u26a1", 150)
        cv.create_text(34, yy + 22, anchor="w", fill=FG, font=("Segoe UI", 10),
                       text=tr("Detection preset", "Набор частот"))
        seg = Segmented(cv, HW - 34, yy + 22, list(PERF_PRESETS),
                        lambda: set_perf(seg.get()),
                        labels={k: n[1 if LANG == "ru" else 0]
                                for k, n in PERF_NAMES.items()}, w=100)
        seg.set(cur_perf())
        c_, s_, f_ = CAST_FPS, SHAKE_FPS, FISH_FPS
        cv.create_text(34, yy + 50, anchor="w", fill=DIM, font=("Consolas", 9),
                       text=tr(f"now: cast {c_ or 'max'} \u00b7 shake {s_} \u00b7 "
                               f"fish {f_ or 'max'}",
                               f"сейчас: заброс {c_ or 'max'} \u00b7 shake {s_} \u00b7 "
                               f"бой {f_ or 'max'}") +
                       ("" if cur_perf() != "custom" else tr("  (custom)", "  (свои)")))
        tips = [tr("Weak PC / laptop - Low: cast 20, shake 30, fish 30",
                   "Слабый ПК / ноутбук - 20 / 30 / 30"),
                tr("Average PC - Medium: cast 30, shake 60, fish 60",
                   "Средний ПК - 30 / 60 / 60"),
                tr("Gaming PC - High: cast 60, shake 120, fish max",
                   "Мощный ПК - 60 / 120 / максимум"),
                tr("Keep fish at 30+ - lower and the bar lags behind the fish",
                   "Бой держи не ниже 30 - иначе блок опаздывает за рыбой")]
        for i, t in enumerate(tips):
            cv.create_text(34, yy + 76 + i * 18, anchor="w", font=("Segoe UI", 8),
                           fill=HINT if i < 3 else WARN_C, text="\u2022 " + t)
        ui["perf_fps"] = cv.create_text(HW - 34, yy + 50, anchor="e", fill=DIM,
                                        font=("Consolas", 9), text="")
        return y_next

    def set_perf(k):
        if k not in PERF_PRESETS:
            return
        c_, s_, f_ = PERF_PRESETS[k]
        ini_write({"CAST_FPS": str(c_), "SHAKE_FPS": str(s_), "FISH_FPS": str(f_)})
        reload_config()
        say(f"частоты: заброс {c_}, shake {s_}, бой {f_ or 'max'}")
        note(tr("Preset: ", "Набор: ") + PERF_NAMES[k][1 if LANG == "ru" else 0], OK_C)
        for p in ("main", "cast", "shake", "fish"):
            rebuild_page(p)

    # ---------------- Shake ----------------
    def cur_shake():
        return SHAKE_OLD.get(SHAKE_MODE, SHAKE_MODE)

    def build_shake_style(cv, y):
        y_next, yy = card(cv, y, tr("Shake configuration", "Настройка shake"), "\u2248", 48)
        cv.create_text(34, yy + 24, anchor="w", fill=FG, font=("Segoe UI", 10),
                       text=LABELS["SHAKE_MODE"][1 if LANG == "ru" else 0])
        seg = Segmented(cv, HW - 34, yy + 24, SHAKE_MODES,
                        lambda: set_shake_mode(seg.get()),
                        labels={m: n[1 if LANG == "ru" else 0]
                                for m, n in SHAKE_NAMES.items()}, w=94)
        seg.set(cur_shake())
        return y_next

    def set_shake_mode(m):
        if m == cur_shake():
            return
        if not confirm_discard():
            rebuild_page("shake")
            return
        ini_write({"SHAKE_MODE": m})
        reload_config()
        say(f"shake: {m}")
        S["dirty"] = False
        note(tr("Shake: ", "Shake: ") + SHAKE_NAMES[m][1 if LANG == "ru" else 0], OK_C)
        rebuild_page("shake")

    def build_shake_opts(cv, y):
        m = cur_shake()
        names = SHAKE_OPTS.get(m, [])
        title = SHAKE_NAMES.get(m, (m, m))[1 if LANG == "ru" else 0]
        if not names:
            y_next, yy = card(cv, y, tr(f"{title}", f"{title}"), "\u25cc", 40)
            cv.create_text(34, yy + 20, anchor="w", fill=DIM, font=("Segoe UI", 9),
                           text=tr("Shake is skipped - the macro waits for the bite",
                                   "Shake пропускается - макрос ждёт поклёвку"))
            return y_next
        warn = m in ("pixel", "circle") and FIRST_PERSON
        y_next, yy = card(cv, y, tr(f"{title} options", f"{title}: параметры"), "\u2699",
                          40 * len(names) + (34 if warn else 0))
        if warn:
            cv.create_text(34, yy + 16, anchor="w", fill=WARN_C, font=("Segoe UI", 9),
                           text=tr("\u26a0 First-person camera locks the cursor - clicks "
                                   "may not reach Shake (Cast \u2192 Camera)",
                                   "\u26a0 Вид от первого лица держит курсор - щелчки "
                                   "могут не доходить (Cast \u2192 Камера)"))
            yy += 34
        for n in names:
            yy = field_row(cv, yy, n)
        return y_next

    def shake_test():
        note(tr("Switch to the game - checking in 3 s", "Переключись в игру - проверка "
                "через 3 с"), WARN_C)

        def work():
            time.sleep(3.0)
            try:
                with Capture() as c_:
                    shake_diagnose(c_, "shake_test.png")
            except Exception as e:
                say(f"проверка shake: {e}")
        threading.Thread(target=work, daemon=True).start()

    def build_shake_zone(cv, y):
        if cur_shake() in ("pixel", "circle"):
            y_next, yy = card(cv, y, tr("Test", "Проверка"), "☉", 44)
            CanvasButton(cv, 34, yy + 8, 190, tr("Test shake now", "Проверить shake"),
                         shake_test, h=28)
            cv.create_text(236, yy + 22, anchor="w", fill=HINT, font=("Segoe UI", 8),
                           text=tr("result in the log + shake_test.png",
                                   "итог в журнале + shake_test.png"))
            y = y_next
        if cur_shake() == "disabled":
            return y
        names = ["SHAKE_LEFT", "SHAKE_TOP", "SHAKE_WIDTH", "SHAKE_HEIGHT"]
        y_next, yy = card(cv, y, tr("Scan zone", "Зона поиска"), "\u25a2", 40 * len(names))
        for n in names:
            yy = field_row(cv, yy, n)
        return y_next

    # ---------------- Main ----------------
    def start_click():
        was = running
        toggle_running()
        if not was and running:
            # мышь над пультом: сворачиваем его и отдаём фокус игре,
            # иначе первый заброс кликнул бы по пульту
            root.iconify()
            threading.Thread(target=focus_game, daemon=True).start()

    def build_start(cv, y):
        y_next, yy = card(cv, y, tr("Fishing", "Ловля"), "\u25b6", 104)
        ui["start_btn"] = CanvasButton(cv, 34, yy + 10, 230, "", start_click, h=40, r=14)
        cv.create_polygon(*rrect(280, yy + 20, 330, yy + 40, 6), smooth=True,
                          fill=BTN, outline=LINE)
        cv.create_text(305, yy + 30, text=key_label(KEY_START), fill=FG,
                       font=("Consolas", 9, "bold"))
        ui["stats"] = cv.create_text(34, yy + 72, anchor="w", fill=FG,
                                     font=("Consolas", 11), text="")
        ui["main_rod"] = cv.create_text(34, yy + 94, anchor="w", fill=DIM,
                                        font=("Segoe UI", 9), text="")
        return y_next

    def build_check(cv, y):
        import textwrap
        lines = [(r[0], textwrap.wrap(r[2 if LANG != "ru" else 1], 60) or [""])
                 for r in last_env]
        rows = sum(len(w) for _l, w in lines) * 17 + 6 * len(lines) + 46
        y_next, yy = card(cv, y, tr("Check", "Проверка"), "\u2713", rows)
        for lvl, wrapped in lines:
            col = {"ok": OK_C, "bad": BAD_C, "warn": WARN_C}.get(lvl, DIM)
            cv.create_text(34, yy + 8, anchor="nw", text=ENV_SYM[lvl], fill=col,
                           font=("Segoe UI", 11, "bold"))
            cv.create_text(56, yy + 9, anchor="nw", text="\n".join(wrapped),
                           fill=FG if lvl != "info" else DIM, font=("Segoe UI", 9))
            yy += len(wrapped) * 17 + 6
        ok = not any(r[0] == "bad" for r in last_env)
        cv.create_text(34, yy + 18, anchor="w", fill=OK_C if ok else WARN_C,
                       font=("Segoe UI", 9, "bold"),
                       text=tr("Ready", "Готово") if ok else tr("Fix \u2717", "Исправь \u2717"))
        CanvasButton(cv, HW - 186, yy + 6, 150, tr("Re-check", "Проверить снова"),
                     recheck)
        return y_next

    def recheck():
        log_env_check("проверка")
        rebuild_page("main")

    def build_keys(cv, y):
        n = len(HOTKEYS)
        y_next, yy = card(cv, y, tr("Hotkeys", "Клавиши"), "\u2328", 30 * n + 40)
        for i, (name, _d, lab) in enumerate(HOTKEYS):
            ky = yy + 6 + i * 30
            tg = f"hk_{name}"
            chip = cv.create_polygon(*rrect(34, ky, 104, ky + 24, 7), smooth=True,
                                     fill=BTN, outline=LINE, tags=tg)
            txt = cv.create_text(69, ky + 12, text=key_label(globals()[name]), fill=FG,
                                 font=("Consolas", 9, "bold"), tags=tg)
            cv.create_text(118, ky + 12, anchor="w", text=lab[1 if LANG == "ru" else 0],
                           fill=DIM, font=("Segoe UI", 9))
            cv.tag_bind(tg, "<Button-1>", lambda e, n_=name, c=chip, t=txt:
                        start_key_capture(cv, n_, c, t))
            cv.tag_bind(tg, "<Enter>", lambda e, c=chip: cv.itemconfig(c, outline=ACC))
            cv.tag_bind(tg, "<Leave>", lambda e, c=chip: cv.itemconfig(c, outline=LINE))
        CanvasButton(cv, 34, yy + 30 * n + 10, 130, tr("Reset keys", "Сбросить"),
                     reset_keys)
        CanvasButton(cv, HW - 214, yy + 30 * n + 10, 180,
                     "\u25a2  " + tr("Change area", "Изменить зоны") + f"  [{key_label(KEY_AREA)}]",
                     lambda: open_area_editor())
        cv.create_text(178, yy + 30 * n + 22, anchor="w", fill=HINT,
                       font=("Segoe UI", 8), text=tr("click a key to change",
                                                     "щёлкни по клавише"))
        return y_next

    # Tk-имя клавиши -> имя, как его видит pynput (так они и хранятся)
    TK_KEYS = {"Prior": "page_up", "Next": "page_down", "Insert": "insert",
               "Delete": "delete", "Home": "home", "End": "end", "Pause": "pause",
               "Scroll_Lock": "scroll_lock", "Num_Lock": "num_lock", "Up": "up",
               "Down": "down", "Left": "left", "Right": "right", "BackSpace": "backspace",
               "Return": "enter", "space": "space", "Tab": "tab", "Escape": "esc",
               "Caps_Lock": "caps_lock"}

    def start_key_capture(cv, name, chip, txt):
        global hotkey_capture
        hotkey_capture = True
        cv.itemconfig(chip, fill=ON_RED, outline="#c1121f")
        cv.itemconfig(txt, text="\u2026", fill="#ffffff")
        note(tr("Press a key (Esc - cancel)", "Нажми клавишу (Esc - отмена)"), WARN_C)
        root.focus_force()

        def done(e):
            global hotkey_capture
            root.unbind("<Key>")
            hotkey_capture = False
            ks = e.keysym
            if ks == "Escape":
                note("")
                rebuild_page("main")
                return
            k = TK_KEYS.get(ks)
            if k is None:
                k = ks.lower() if ks[:1] in "Ff" and ks[1:].isdigit() else \
                    (e.char.lower() if e.char and e.char.isprintable() else ks.lower())
            other = [n for n, _d, _l in HOTKEYS if n != name and globals()[n] == k]
            if k in HOTKEY_BANNED:
                note(tr(f"{key_label(k)} can't be used", f"{key_label(k)} занята игрой"),
                     BAD_C)
            elif other:
                note(tr(f"{key_label(k)} is already used", f"{key_label(k)} уже занята"),
                     BAD_C)
            else:
                ini_write({name: k})
                reload_config()
                say(f"клавиша {name}: {k}")
                note(tr("Key saved", "Клавиша сохранена"), OK_C)
            rebuild_page("main")
        root.bind("<Key>", done)

    def reset_keys():
        ini_write({n: d for n, d, _l in HOTKEYS})
        reload_config()
        say("клавиши сброшены")
        note(tr("Keys reset", "Клавиши сброшены"), OK_C)
        rebuild_page("main")

    # ---------------- Fish: удочка ----------------
    def rod_title(pid):
        en, ru = rod_names(pid)
        return (ru if LANG == "ru" else en), en

    def icon_img(pid, size=38):
        key = ("icon", pid, size)
        if key not in ui:
            try:
                from PIL import ImageTk
                ui[key] = ImageTk.PhotoImage(rod_icon(pid, size), master=root)
            except Exception:
                ui[key] = None
        return ui[key]

    def build_rod(cv, y):
        y_next, yy = card(cv, y, tr("Rod", "Удочка"), "\u2693", 64)
        img = icon_img(PROFILE, 44)
        if img is not None:
            cv.create_image(34, yy + 32, anchor="w", image=img)
        title, en = rod_title(PROFILE)
        cv.create_text(90, yy + 22, anchor="w", text=title, fill=FG,
                       font=("Segoe UI", 13, "bold"))
        cv.create_text(90, yy + 44, anchor="w", fill=HINT, font=("Segoe UI", 8),
                       text=(en if LANG == "ru" and en != title else
                             tr("in-game name", "как в игре") if PROFILE else
                             tr("no rod profile", "без профиля")))
        CanvasButton(cv, HW - 190, yy + 18, 156, tr("Change rod  \u25b8",
                                                   "Сменить  \u25b8"),
                     lambda: rod_panel_open(True), h=30, r=12)
        return y_next

    def fill_rods():
        pass                                  # всё рисует build_rod / панель

    # ---------- выезжающая панель выбора удочки ----------
    RW = 340

    def build_rod_panel():
        ph = max(200, root.winfo_height() - HEAD)
        pc = tk.Canvas(root, width=RW, height=ph, bg="#0d0506", highlightthickness=0)
        ui["rod_panel"] = pc
        pc.create_rectangle(0, 0, 2, 4000, fill=ACC, outline="")
        pc.create_text(22, 26, anchor="w", text=tr("CHOOSE ROD", "ВЫБОР УДОЧКИ"),
                       fill=FG, font=("Segoe UI", 10, "bold"))
        pc.create_text(RW - 24, 26, text="\u2715", fill=DIM, font=("Segoe UI", 12, "bold"),
                       tags="rp_close")
        pc.tag_bind("rp_close", "<Button-1>", lambda e: rod_panel_open(False))
        pc.create_line(20, 46, RW - 20, 46, fill=_mix("#0d0506", LINE, 0.8))
        rods = [""] + list_profiles()
        for i, pid in enumerate(rods):
            y = 58 + i * 62
            tg = f"rp_{i}"
            on = (pid or "").lower() == PROFILE
            box = pc.create_polygon(*rrect(14, y, RW - 16, y + 54, 14), smooth=True,
                                    fill=ON_RED if on else "#0d0506",
                                    outline="#c1121f" if on else _mix("#0d0506", LINE, 0.9),
                                    tags=tg)
            img = icon_img(pid, 38)
            if img is not None:
                pc.create_image(24, y + 27, anchor="w", image=img, tags=tg)
            title, en = rod_title(pid)
            pc.create_text(74, y + 19, anchor="w", text=title, tags=tg,
                           fill="#ffffff" if on else FG, font=("Segoe UI", 11, "bold"))
            sub = en if (LANG == "ru" and en != title) else (
                tr("any rod without profile", "любая удочка без профиля") if not pid
                else tr("in-game name", "как в игре"))
            pc.create_text(74, y + 38, anchor="w", text=sub, tags=tg,
                           fill=_mix(ON_RED, "#ffffff", 0.65) if on else HINT,
                           font=("Segoe UI", 8))
            if on:
                pc.create_text(RW - 34, y + 27, text="\u2713", fill="#ffffff",
                               font=("Segoe UI", 13, "bold"), tags=tg)
            pc.tag_bind(tg, "<Button-1>", lambda e, p=pid: choose_rod(p))
            pc.tag_bind(tg, "<Enter>", lambda e, b_=box, o=on: not o and
                        pc.itemconfig(b_, fill=BTN, outline=ACC))
            pc.tag_bind(tg, "<Leave>", lambda e, b_=box, o=on: not o and
                        pc.itemconfig(b_, fill="#0d0506",
                                      outline=_mix("#0d0506", LINE, 0.9)))
        yb = 58 + len(rods) * 62 + 8
        CanvasButton(pc, 16, yb, 150, tr("+  Add rod", "+  Добавить"), new_profile)
        CanvasButton(pc, 176, yb, 146, tr("Delete", "Удалить"), del_profile)
        pc.configure(scrollregion=(0, 0, RW, yb + 50))
        pc.place(x=HW + 10, y=HEAD)
        S["rk"], S["rgoal"], S["ranim"] = 0.0, 0.0, False

    def rod_panel_open(on):
        if on and not confirm_discard():
            return
        if on:
            old = ui.get("rod_panel")
            if old is not None:
                old.destroy()
            build_rod_panel()
            raise_widget(ui["rod_panel"])
        S["rgoal"] = 1.0 if on else 0.0
        if not S.get("ranim"):
            S["ranim"] = True
            rod_panel_step()

    def rod_panel_step():
        pc = ui.get("rod_panel")
        if pc is None:
            S["ranim"] = False
            return
        d = S["rgoal"] - S["rk"]
        # плавный выезд: быстро в начале, мягко в конце
        S["rk"] += d * 0.24 if abs(d) > 0.01 else d
        W_ = root.winfo_width() if root.winfo_width() > 50 else HW
        pc.place(x=round(W_ - RW * S["rk"]), y=HEAD)
        if S["rk"] != S["rgoal"]:
            root.after(15, rod_panel_step)
        else:
            S["ranim"] = False

    def choose_rod(pid):
        rod_panel_open(False)
        if (pid or "").lower() != PROFILE:
            S["dirty"] = False
            pick_profile(pid)
            rebuild_page("fish")

    # ---------- цвета деталей миниигры ----------
    def build_colors(cv, y):
        y_next, yy = card(cv, y, tr("Color options", "Цвета"), "\u25a3",
                          40 * len(COLOR_ROWS) + 8)
        if TRACK_STYLE != "color":
            cv.create_text(HW - 34, y + 20, anchor="e", fill=HINT, font=("Segoe UI", 8),
                           text=tr("used in Color mode", "работает в режиме Color"))
        for i, key in enumerate(COLOR_ROWS):
            color_row(cv, yy + 4 + i * 40, key)
        return y_next

    def color_row(cv, y, key):
        cname, tname = "COL_" + key, "TOL_" + key
        cy = y + 18
        x2 = HW - 34
        cv.create_text(34, cy, anchor="w", fill=FG, font=("Segoe UI", 10),
                       text=LABELS[cname][1 if LANG == "ru" else 0])
        st = {"hex": "#000000", "none": False, "tol": 0}
        # сброс
        rs = cv.create_text(x2 - 8, cy, text="\u21ba", fill=DIM,
                            font=("Segoe UI", 12, "bold"), tags=f"cr_{key}")
        # допуск: − число +
        cv.create_polygon(*rrect(x2 - 106, cy - 12, x2 - 26, cy + 12, 9), smooth=True,
                          fill=FIELD, outline=LINE)
        tm = cv.create_text(x2 - 94, cy, text="\u2212", fill=FG,
                            font=("Segoe UI", 11, "bold"), tags=f"tm_{key}")
        tv = cv.create_text(x2 - 66, cy, text="0", fill=FG, font=("Consolas", 10, "bold"))
        tp = cv.create_text(x2 - 38, cy, text="+", fill=FG,
                            font=("Segoe UI", 11, "bold"), tags=f"tp_{key}")
        cv.create_text(x2 - 66, cy + 17, text=tr("tol", "допуск"), fill=HINT,
                       font=("Segoe UI", 7))
        # "нет"
        nb = cv.create_polygon(*rrect(x2 - 168, cy - 11, x2 - 116, cy + 11, 9),
                               smooth=True, fill=BTN, outline=LINE, tags=f"nn_{key}")
        nt = cv.create_text(x2 - 142, cy, text=tr("None", "Нет"), fill=DIM,
                            font=("Segoe UI", 8, "bold"), tags=f"nn_{key}")
        # код цвета и образец
        e = tk.Entry(cv, width=9, bg=FIELD, fg=FG, insertbackground=FG, relief="flat",
                     justify="center", font=("Consolas", 10), highlightthickness=1,
                     highlightbackground=LINE, highlightcolor=ACC)
        cv.create_window(x2 - 178, cy, window=e, anchor="e", height=24)
        sw = cv.create_polygon(*rrect(x2 - 290, cy - 11, x2 - 262, cy + 11, 6),
                               smooth=True, fill="#000000", outline=LINE,
                               tags=f"sw_{key}")
        sx = cv.create_text(x2 - 276, cy, text="", fill=BAD_C,
                            font=("Segoe UI", 11, "bold"), tags=f"sw_{key}")

        def show():
            h = st["hex"] if hex_bgr(st["hex"]) else "#000000"
            cv.itemconfig(sw, fill="#1a0a0c" if st["none"] else h)
            cv.itemconfig(sx, text="\u2205" if st["none"] else "")
            cv.itemconfig(nb, fill=ON_RED if st["none"] else BTN,
                          outline="#c1121f" if st["none"] else LINE)
            cv.itemconfig(nt, fill="#ffffff" if st["none"] else DIM)
            e.config(fg=HINT if st["none"] else FG)
            cv.itemconfig(tv, text=str(st["tol"]))

        def put_col(v):
            v = str(v).lower()
            if v in ("none", ""):
                st["none"] = True
            else:
                st["none"] = False
                st["hex"] = v
            e.delete(0, "end")
            e.insert(0, st["hex"])
            show()

        def get_col():
            if st["none"]:
                return "none"
            return e.get().strip()

        def put_tol(v):
            st["tol"] = max(0, min(255, int(v)))
            show()

        def on_hex(_e=None):
            v = e.get().strip().lower()
            if not v.startswith("#"):
                v = "#" + v
            if hex_bgr(v):
                st["hex"] = v
                st["none"] = False
                show()
            mark_dirty()

        def pick():
            try:
                from tkinter import colorchooser
                res = colorchooser.askcolor(color=st["hex"], parent=root,
                                            title=LABELS[cname][1 if LANG == "ru" else 0])
            except Exception:
                return
            if res and res[1]:
                put_col(res[1])
                mark_dirty()

        def step(dv):
            put_tol(st["tol"] + dv)
            mark_dirty()

        def toggle_none():
            st["none"] = not st["none"]
            show()
            mark_dirty()

        def reset():
            c, t = COLOR_DEFAULTS[key]
            put_col(c)
            put_tol(t)
            mark_dirty()
        e.bind("<KeyRelease>", on_hex)
        cv.tag_bind(f"sw_{key}", "<Button-1>", lambda ev: pick())
        cv.tag_bind(f"nn_{key}", "<Button-1>", lambda ev: toggle_none())
        cv.tag_bind(f"tm_{key}", "<Button-1>", lambda ev: step(-1))
        cv.tag_bind(f"tp_{key}", "<Button-1>", lambda ev: step(+1))
        cv.tag_bind(f"cr_{key}", "<Button-1>", lambda ev: reset())
        for t_ in (f"tm_{key}", f"tp_{key}", f"cr_{key}"):
            cv.tag_bind(t_, "<Enter>", lambda ev, t_=t_: cv.itemconfig(t_, fill=ACC))
            cv.tag_bind(t_, "<Leave>", lambda ev, t_=t_: cv.itemconfig(
                t_, fill=DIM if t_.startswith("cr_") else FG))
        fields[cname] = dict(spec=SPEC[cname][2], get=get_col, put=put_col, w=e, cv=cv,
                             lab=None, mark=None)
        fields[tname] = dict(spec=SPEC[tname][2], get=lambda: str(st["tol"]), put=put_tol,
                             w=None, cv=cv, lab=None, mark=None)

    # ---------------- Extra ----------------
    def build_community(cv, y):
        y_next, yy = card(cv, y, tr("Community", "Сообщество"), "\u2665", 72)
        cv.create_text(34, yy + 16, anchor="w", fill=DIM, font=("Segoe UI", 9),
                       text=tr("News, help and updates", "Новости, помощь и обновления"))

        def open_url(url, name):
            if not url:
                note(tr(f"{name} link is coming soon", f"Ссылка на {name} скоро будет"))
                return
            import webbrowser
            webbrowser.open(url)
        CanvasButton(cv, 34, yy + 36, 150, "\u25c6  Discord",
                     lambda: open_url(DISCORD_URL, "Discord"), h=28)
        CanvasButton(cv, 194, yy + 36, 150, "\u2387  GitHub",
                     lambda: open_url(GITHUB_URL, "GitHub"), h=28)
        return y_next

    def build_lang(cv, y):
        y_next, yy = card(cv, y, tr("Language", "Язык"), "\u2691", 44)
        cv.create_text(34, yy + 22, anchor="w", fill=FG, font=("Segoe UI", 10),
                       text=tr("Interface", "Интерфейс"))
        seg = Segmented(cv, HW - 34, yy + 22, [c for c, _n in LANGS],
                        lambda: set_lang(seg.get()), labels=dict(LANGS), w=92)
        seg.set(LANG)
        return y_next

    def set_lang(code):
        if code == LANG:
            return
        ini_write({"LANG": code})
        reload_config()
        say(f"язык окна: {code}")
        build_ui()

    def build_log(cv, y):
        y_next, yy = card(cv, y, tr("Log", "Журнал"), "\u2261", 244)
        log = tk.Text(cv, bg="#0d0405", fg=DIM, font=("Consolas", 8), relief="flat",
                      wrap="none", highlightthickness=1, highlightbackground=LINE,
                      highlightcolor=ACC, insertbackground=FG, selectbackground=SEL,
                      selectforeground=FG, padx=6, pady=4)
        log.bind("<Key>", lambda e: "break" if e.state & 4 == 0 else None)
        cv.create_window(34, yy + 8, window=log, anchor="nw", width=HW - 68, height=192)
        CanvasButton(cv, 34, yy + 210, 150, tr("Copy log", "Копировать"), copy_log)
        ui["log"] = log
        S["last_seq"] = -1
        return y_next

    def build_about(cv, y):
        y_next, yy = card(cv, y, tr("About", "О программе"), "\u2740", 76)
        cv.create_text(34, yy + 18, anchor="w", fill=FG, font=("Segoe UI", 10, "bold"),
                       text=f"{APP_NAME} {VERSION}")
        cv.create_text(HW - 34, yy + 18, anchor="e", fill=HINT, font=("Segoe UI", 8),
                       text="scarlet.ini \u00b7 scarlet.log")
        CanvasButton(cv, 34, yy + 40, 170, tr("Say hello", "Приветствие"),
                     lambda: show_welcome(), h=26)
        if UPDATE_INFO:
            CanvasButton(cv, 214, yy + 40, 210,
                         "\u2b06  " + tr(f"Download {UPDATE_INFO[0]}", f"Скачать {UPDATE_INFO[0]}"),
                         lambda: open_update(), h=26)
        return y_next

    def open_update():
        if UPDATE_INFO:
            import webbrowser
            webbrowser.open(UPDATE_INFO[1])

    # ---------------- приветствие при первом запуске ----------------
    def show_welcome():
        """Как "hello" на новом iPhone: тёмное окно, и по нему рукописное
        hello плавно выписывается одной линией (от малинового к розовому, со
        свечением). Потом проявляется "Scarlet hub". Щелчок или клавиша -
        окно гаснет. Показывается один раз (WELCOME_DONE в scarlet.ini);
        повторить - Extra -> О программе."""
        if ui.get("welcome") is not None:
            return
        W_, H_ = 640, 380
        d = tk.Toplevel(root)
        ui["welcome"] = d
        d.overrideredirect(True)
        d.attributes("-topmost", True)
        try:
            d.attributes("-alpha", 0.0)
        except Exception:
            pass
        try:
            sw, sh = int(root.winfo_screenwidth()), int(root.winfo_screenheight())
        except Exception:
            sw, sh = 1920, 1080
        d.geometry(f"{W_}x{H_}+{(sw - W_) // 2}+{(sh - H_) // 2}")
        cv = tk.Canvas(d, width=W_, height=H_, bg="#070304", highlightthickness=0)
        cv.place(x=0, y=0)
        try:
            from PIL import ImageTk
            img = ImageTk.PhotoImage(SceneRenderer(W_, H_, [
                (40, 330, 0.75, 0.22, 0.0), (604, 300, 0.85, 0.30, 0.0),
                (560, 40, 0.55, 0.16, 0.0)]).render(99.0), master=root)
            cv.create_image(0, 0, anchor="nw", image=img)
            cv.img = img
        except Exception:
            pass
        cv.create_rectangle(0, 0, W_ - 1, H_ - 1, outline=LINE)
        # линия: по центру, чуть выше середины
        c = hello_curve()
        c = c - [(c[:, 0].min() + c[:, 0].max()) / 2, 0] + [W_ / 2, 30]
        n = len(c)
        lens = np.r_[0, np.cumsum(np.hypot(*np.diff(c, axis=0).T))]
        total = lens[-1]
        st = {"i": 0, "t0": time.perf_counter(), "phase": "write", "k": 0.0}
        DUR = 2.6                                     # секунд на всю линию

        def ease(x):                                  # мягкий старт и финиш
            return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2

        def ink(k):
            """Цвет по ходу линии: малиновый -> розово-алый."""
            return _mix("#e0243a", "#ff8fa3", 0.75 * k)

        title = cv.create_text(W_ / 2, 286, text="Scarlet hub", fill="#070304",
                               font=("Segoe UI Light", 22), state="hidden")
        sub = cv.create_text(W_ / 2, 318, fill="#070304", font=("Segoe UI", 9),
                             state="hidden", text=tr("click anywhere to continue",
                                                     "щёлкни, чтобы продолжить"))

        def frame():
            if ui.get("welcome") is not d:
                return
            t = time.perf_counter() - st["t0"]
            # плавное появление окна
            try:
                d.attributes("-alpha", min(1.0, t / 0.35) if st["phase"] != "out" else
                             max(0.0, 1 - st["k"]))
            except Exception:
                pass
            if st["phase"] == "write":
                p = ease(min(1.0, max(0.0, (t - 0.3) / DUR)))
                j = int(np.searchsorted(lens, p * total))
                j = min(n - 1, j)
                for i in range(st["i"], j):
                    k = lens[i] / total
                    w = 4.5 + 3.5 * np.sin(np.pi * k)          # толще в середине
                    x0, y0 = c[i]
                    x1, y1 = c[i + 1]
                    cv.create_line(x0, y0, x1, y1, width=w + 9, capstyle="round",
                                   fill=_mix("#070304", "#e0243a", 0.22), tags="glow")
                    cv.create_line(x0, y0, x1, y1, width=w, capstyle="round",
                                   fill=ink(k), tags="ink")
                if j > st["i"]:
                    cv.tag_raise("ink")
                st["i"] = max(st["i"], j)
                if p >= 1.0:
                    st["phase"], st["t1"] = "title", time.perf_counter()
                    cv.itemconfig(title, state="normal")
                    cv.itemconfig(sub, state="normal")
            elif st["phase"] == "title":
                k = min(1.0, (time.perf_counter() - st["t1"]) / 0.9)
                cv.itemconfig(title, fill=_mix("#070304", FG, k))
                cv.itemconfig(sub, fill=_mix("#070304", HINT, max(0.0, k * 1.4 - 0.4)))
                if k >= 1.0:
                    st["phase"] = "wait"
            elif st["phase"] == "out":
                st["k"] = min(1.0, (time.perf_counter() - st["t2"]) / 0.4)
                if st["k"] >= 1.0:
                    ui["welcome"] = None
                    d.destroy()
                    return
            d.after(15, frame)

        def close(_e=None):
            if st["phase"] == "out":
                return
            if not WELCOME_DONE:
                ini_write({"WELCOME_DONE": "1"})
                reload_config()
            if st["phase"] == "write":
                # щёлкнул раньше времени - дописываем сразу и гасим
                st["t0"] -= DUR
            st["phase"], st["t2"] = "out", time.perf_counter()
        cv.bind("<Button-1>", close)
        d.bind("<Key>", close)
        d.focus_force()
        frame()

    def pump_log():
        log = ui.get("log")
        if log is None or LOG_SEQ == S["last_seq"]:
            return
        S["last_seq"] = LOG_SEQ
        log.delete("1.0", "end")
        log.insert("end", "\n".join(list(LOG_LINES)[-300:]))
        log.see("end")

    def copy_log():
        try:
            root.clipboard_clear()
            root.clipboard_append("\n".join(LOG_LINES))
            root.update()
            say("журнал скопирован в буфер обмена")
            note(tr("Log copied", "Журнал скопирован"), OK_C)
        except Exception as e:
            say(f"не смог скопировать: {e}")

    BUILDERS = {"start": build_start, "check": build_check, "keys": build_keys,
                "rod": build_rod, "lang": build_lang, "log": build_log,
                "about": build_about, "community": build_community,
                "colors": build_colors, "shake_style": build_shake_style,
                "shake_opts": build_shake_opts, "shake_zone": build_shake_zone,
                "perf": build_perf, "cast_style": build_cast_style,
                "cast_seq": build_cast_seq, "cast_perfect": build_cast_perfect,
                "rod_seq": build_rod_seq}

    def build_page(key):
        cv = tk.Canvas(root, bg=BG, highlightthickness=0)
        y = 16
        for c in TAB_CARDS[key]:
            if c[0] == "fields":
                _k, icon, title, names = c
                y_next, yy = card(cv, y, title[1 if LANG == "ru" else 0], icon,
                                  40 * len(names))
                for n in names:
                    yy = field_row(cv, yy, n)
                y = y_next
            else:
                y = BUILDERS[c[0]](cv, y)
        cv.configure(scrollregion=(0, 0, HW, y + 6))
        cv.page_h = y + 6
        ui["pages"][key] = cv
        return cv

    def rebuild_page(key, keep=False):
        if not keep:
            for k_ in [k_ for k_ in S.get("seq_work", {}) if seq_meta(k_)[0] == key]:
                S["seq_work"].pop(k_, None)
        old = ui["pages"].pop(key, None)
        if old is not None:
            old.destroy()
        for n in [n for n, f in fields.items() if f["cv"] is old]:
            del fields[n]
        cv = build_page(key)
        # новые поля вкладки - сразу со значениями (остальные вкладки не трогаем)
        fill(only={n for n, f in fields.items() if f["cv"] is cv})
        if S["cur"] == key:
            show_tab(key)
        raise_widget(ui["drawer"])

    # ---------------- логика настроек ----------------
    def shown(name):
        if name in RESTART_KEYS:
            return _parse_val(name, _PROF.get(name, _INI.get(name)), _REG[name])
        return globals().get(name, _REG.get(name))

    def fill(only=None):
        S["filling"] = True
        own = profile_keys_set(PROFILE) if PROFILE else set()
        for name, f in fields.items():
            if only is not None and name not in only:
                continue
            f["put"](shown(name))
            if isinstance(f["w"], tk.Entry):
                f["w"].config(bg=FIELD)
            m = ""
            if name in RESTART_KEYS and shown(name) != globals()[name]:
                m = "\u21bb"                      # нужен перезапуск
            elif PROFILE and name in own:
                m = "\u25cf"                      # своё у удочки
            if f["mark"] is None:
                continue
            bb = f["cv"].bbox(f["lab"])
            if bb:
                f["cv"].coords(f["mark"], bb[2] + 8, (bb[1] + bb[3]) / 2)
            f["cv"].itemconfig(f["mark"], text=m, fill=WARN_C if m == "\u21bb" else ACC)
        fill_rods()
        S["dirty"] = False
        S["filling"] = False

    def confirm_discard():
        return (not S["dirty"]) or messagebox.askyesno(
            APP_NAME, tr("Discard unsaved changes?", "Отменить несохранённые изменения?"),
            parent=root)

    def pick_profile(name):
        name = name.lower()
        if name == PROFILE or not confirm_discard():
            return
        ini_write({"PROFILE": name})
        changed = reload_config()
        fill()
        say(f"удочка: {PROFILE or 'общая'}"
            + (f" (поменялось: {', '.join(sorted(changed))})" if changed else ""))
        note(tr("Rod: ", "Удочка: ") + rod_name(), OK_C)

    def new_profile():
        d = tk.Toplevel(root)
        d.title(tr("New rod", "Новая удочка"))
        d.configure(bg=BG)
        d.geometry("340x196")
        d.transient(root)
        style_titlebar(d)
        ents = []
        for lab in (tr("Name in game (English)", "Название в игре (англ.)"),
                    tr("Russian name (optional)", "Русское название (можно пусто)")):
            tk.Label(d, text=lab, bg=BG, fg=DIM, font=("Segoe UI", 8)).pack(
                anchor="w", padx=14, pady=(10, 2))
            e = tk.Entry(d, bg=FIELD, fg=FG, insertbackground=FG, relief="flat",
                         font=("Segoe UI", 10), highlightthickness=1,
                         highlightbackground=LINE, highlightcolor=ACC)
            e.pack(fill="x", padx=14)
            ents.append(e)
        ents[0].focus_set()
        msg = tk.Label(d, text="", bg=BG, fg=BAD_C, font=("Segoe UI", 8))
        msg.pack(anchor="w", padx=14)

        def ok(_e=None):
            import re
            en = ents[0].get().strip()
            ru = ents[1].get().strip()
            pid = re.sub(r"[^\w\-]+", "_", en.lower()).strip("_")[:24]
            if not pid:
                msg.config(text=tr("Enter the name", "Впиши название"))
                return
            if pid in [p.lower() for p in list_profiles()] or pid in ROD_INFO:
                msg.config(text=tr("Already exists", "Такая уже есть"))
                return
            vals = {k: _fmt_val(globals()[k]) for k in sorted(PROFILE_KEYS)}
            vals["NAME_EN"] = en
            if ru:
                vals["NAME_RU"] = ru
            ini_write_profile(pid, vals)
            ini_write({"PROFILE": pid})
            reload_config()
            d.destroy()
            fill()
            say(f"заведена удочка [{pid}] - {en}")
            note(tr("Rod added: ", "Удочка добавлена: ") + (ru if LANG == "ru" and ru else en),
                 OK_C)
            rebuild_page("fish")
            rod_panel_open(False)
        ents[0].bind("<Return>", ok)
        ents[1].bind("<Return>", ok)
        hover_red(tk.Button(d, text=tr("Add", "Добавить"), command=ok, bg=SEL, fg=FG,
                            relief="flat", font=("Segoe UI", 9, "bold"),
                            padx=14, pady=3)).pack(anchor="e", padx=14, pady=4)

    def del_profile():
        if not PROFILE:
            return
        if not messagebox.askyesno(APP_NAME, tr(f"Delete rod \"{rod_title(PROFILE)[0]}\"?",
                                                f"Удалить удочку \"{rod_title(PROFILE)[0]}\"?"),
                                   parent=root):
            return
        gone = PROFILE
        gone_title = rod_title(gone)[0]
        ini_delete_profile(gone)
        ini_write({"PROFILE": ""})
        reload_config()
        fill()
        say(f"удочка [{gone}] удалена")
        note(tr("Rod deleted: ", "Удочка удалена: ") + gone_title, OK_C)
        rebuild_page("fish")
        rod_panel_open(False)

    def save():
        vals, bad = {}, []
        for name, f in fields.items():
            try:
                vals[name] = _check_val(f["spec"], f["get"]())
                if isinstance(f["w"], tk.Entry):
                    f["w"].config(bg=FIELD)
            except ValueError as err:
                bad.append(f"{LABELS[name][1 if LANG == 'ru' else 0]}: {err}")
                if isinstance(f["w"], tk.Entry):
                    f["w"].config(bg=ERR_BG)
        if bad:
            note(tr("Not saved - ", "Не сохранено - ") + bad[0], BAD_C)
            return
        changed = {n: v for n, v in vals.items() if shown(n) != v}
        if not changed:
            note(tr("Nothing changed", "Ничего не поменялось"))
            S["dirty"] = False
            return
        to_prof = {n: _fmt_val(v) for n, v in changed.items()
                   if PROFILE and n in PROFILE_KEYS}
        to_gen = {n: _fmt_val(v) for n, v in changed.items() if n not in to_prof}
        if to_gen:
            ini_write(to_gen)
        if to_prof:
            ini_write_profile(PROFILE, to_prof)
        reload_config()
        wrong = [n for n, v in changed.items() if shown(n) != v]
        say("настройки сохранены: " + ", ".join(
            f"{k}={_fmt_val(v)}" for k, v in sorted(changed.items()))
            + (f" (удочка {PROFILE})" if to_prof else ""))
        fill()
        late = [n for n, v in changed.items() if n in RESTART_KEYS and globals()[n] != v]
        if changed.get("CAPTURE") == "dxcam":
            try:
                import dxcam  # noqa: F401 - только проверить, что пакет есть
            except Exception:
                note(tr("dxcam is not installed - staying on mss",
                        "dxcam не установлен - остаюсь на mss"), WARN_C)
                return
        if "TRACK_STYLE" in changed:
            root.after(10, lambda: rebuild_page("fish"))
        if "PC_STYLE" in changed or any(k.startswith("CAST_SEQ") for k in changed):
            root.after(10, lambda: rebuild_page("cast"))
        if changed.keys() & {"ROD_SEQ", "ROD_KEY", "BAG_KEY"}:
            root.after(10, lambda: rebuild_page("main"))
        if wrong:
            note(tr("Not applied: ", "Не применилось: ") + ", ".join(wrong), BAD_C)
        elif late:
            note(tr("Saved \u00b7 restart needed", "Сохранено \u00b7 нужен перезапуск"),
                 WARN_C)
        else:
            note(tr("Saved", "Сохранено"), OK_C)

    def recommended():
        names = [n for c in TAB_CARDS[S["cur"]] if c[0] == "fields" for n in c[3]]
        if S["cur"] == "shake":
            names = [n for n in fields if n.startswith(("SHAKE_", "NAV_"))
                     and n != "SHAKE_MODE"]
        for n in names:
            fields[n]["put"](_REG[n])
        if names:
            S["dirty"] = True
            note(tr("Defaults filled in - press Save",
                    "Значения по умолчанию - нажми Сохранить"), WARN_C)

    # ---------------- меню слева ----------------
    def drawer_open(on):
        S["dgoal"] = 1.0 if on else 0.0
        draw_burger()
        if not S["danim"]:
            S["danim"] = True
            drawer_step()

    def drawer_step():
        d = S["dgoal"] - S["dk"]
        S["dk"] += d * 0.3 if abs(d) > 0.02 else d
        ui["drawer"].place(x=round(-DW + DW * S["dk"]), y=HEAD)
        if S["dk"] > 0:
            raise_widget(ui["drawer"])
        if S["dk"] != S["dgoal"]:
            root.after(15, drawer_step)
        else:
            S["danim"] = False

    def draw_burger():
        h = ui["head"]
        h.delete("burger_lines")
        x, y = 22, 40
        if S["dgoal"] > 0:
            h.create_line(x, y - 8, x + 18, y + 8, fill=FG, width=2,
                          tags=("burger", "burger_lines"))
            h.create_line(x, y + 8, x + 18, y - 8, fill=FG, width=2,
                          tags=("burger", "burger_lines"))
        else:
            for dy in (-7, 0, 7):
                h.create_line(x, y + dy, x + 18, y + dy, fill=FG, width=2,
                              capstyle="round", tags=("burger", "burger_lines"))

    def on_motion(e):
        try:
            x = e.x_root - root.winfo_rootx()
            y = e.y_root - root.winfo_rooty()
        except Exception:
            return
        if S["dgoal"] == 0 and x <= 6 and y > HEAD:
            drawer_open(True)            # мышь у левого края - меню выезжает
        elif S["dgoal"] == 1 and x > DW + 40:
            drawer_open(False)           # увели мышь от меню - прячется

    DBG = "#0d0506"

    def build_drawer():
        dh = max(200, root.winfo_height() - HEAD)
        dv = tk.Canvas(root, width=DW, height=dh, bg=DBG, highlightthickness=0)
        try:
            from PIL import ImageTk
            img = ImageTk.PhotoImage(SceneRenderer(DW, 460, DRAWER_FLOWERS).render(99.0),
                                     master=root)
            ui["drawer_img"] = dv.create_image(0, dh - 460, anchor="nw", image=img)
            dv.img = img
        except Exception:
            pass
        dv.create_rectangle(DW - 2, 0, DW, 2000, fill=LINE, outline="")
        dv.create_rectangle(DW - 2, 0, DW, 120, fill=ACC, outline="")
        dv.create_text(22, 20, anchor="w", text=tr("NAVIGATION", "НАВИГАЦИЯ"), fill=HINT,
                       font=("Segoe UI", 8, "bold"))
        dv.create_line(22, 32, DW - 22, 32, fill=_mix(DBG, LINE, 0.8))
        items = {}
        for i, (key, icon, title, _c) in enumerate(HUB_TABS):
            y = 42 + i * 58
            tg = f"dr_{key}"
            # обводка у каждого пункта: без неё соседние сливались в одно
            pill = dv.create_polygon(*rrect(12, y, DW - 16, y + 50, 14), smooth=True,
                                     fill=DBG, outline=_mix(DBG, LINE, 0.9), tags=tg)
            bar = dv.create_rectangle(12, y + 12, 15, y + 38, fill=DBG, outline="",
                                      tags=tg)
            ic = dv.create_text(36, y + 25, text=icon, fill=DIM,
                                font=("Segoe UI", 14, "bold"), tags=tg)
            tx = dv.create_text(58, y + 17, anchor="w", text=title, fill=FG,
                                font=("Segoe UI", 11, "bold"), tags=tg)
            sub = dv.create_text(58, y + 35, anchor="w", fill=HINT,
                                 font=("Segoe UI", 7),
                                 text=TAB_SUB[key][1 if LANG == "ru" else 0], tags=tg)
            items[key] = (pill, ic, tx, sub, bar)
            dv.tag_bind(tg, "<Button-1>", lambda e, k=key: (show_tab(k),
                                                           drawer_open(False)))
            dv.tag_bind(tg, "<Enter>", lambda e, k=key: S["cur"] != k and
                        dv.itemconfig(items[k][0], fill=BTN, outline=ACC))
            dv.tag_bind(tg, "<Leave>", lambda e, k=key: S["cur"] != k and
                        dv.itemconfig(items[k][0], fill=DBG,
                                      outline=_mix(DBG, LINE, 0.9)))
        # подвал меню: состояние ловли и клавиша старта
        yb = 42 + len(HUB_TABS) * 58 + 8
        dv.create_line(22, yb, DW - 22, yb, fill=_mix(DBG, LINE, 0.8))
        ui["dr_state"] = dv.create_text(22, yb + 20, anchor="w", text="", fill=DIM,
                                        font=("Segoe UI", 9, "bold"))
        dv.create_polygon(*rrect(DW - 70, yb + 9, DW - 22, yb + 31, 7), smooth=True,
                          fill=BTN, outline=LINE)
        dv.create_text(DW - 46, yb + 20, text=key_label(KEY_START), fill=FG,
                       font=("Consolas", 9, "bold"))
        ui["dr_ver"] = dv.create_text(22, yb + 42, anchor="w", fill=HINT,
                                      font=("Segoe UI", 8), text=f"{APP_NAME} v{VERSION}")
        ui["drawer"], ui["drawer_items"] = dv, items
        dv.place(x=-DW, y=HEAD)

    # ---------------- вкладки ----------------
    def place_page(cv):
        cv.place(x=0, y=HEAD, relwidth=1,
                 height=max(100, root.winfo_height() - HEAD - FOOT))

    def show_tab(key):
        S["cur"] = key
        for k, cv in ui["pages"].items():
            if k != key:
                cv.place_forget()
        place_page(ui["pages"][key])
        ui["pages"][key].yview_moveto(0)
        for k, (pill, ic, tx, sub, bar) in ui["drawer_items"].items():
            on = k == key
            dv = ui["drawer"]
            dv.itemconfig(pill, fill=ON_RED if on else DBG,
                          outline="#c1121f" if on else _mix(DBG, LINE, 0.9))
            dv.itemconfig(bar, fill="#ffffff" if on else DBG)
            dv.itemconfig(ic, fill="#ffffff" if on else DIM)
            dv.itemconfig(tx, fill="#ffffff" if on else FG)
            dv.itemconfig(sub, fill=_mix(ON_RED, "#ffffff", 0.6) if on else HINT)
        icon, title = next((i, t) for k, i, t, _c in HUB_TABS if k == key)
        ui["head"].itemconfig(ui["crumb"], text=f"{icon}  {title}")
        on_settings = any(c[0] in ("fields", "shake_opts", "colors", "cast_seq", "rod_seq")
                          for c in TAB_CARDS[key])
        for b in (ui["btn_save"], ui["btn_reset"]):
            b.show(on_settings)
        raise_widget(ui["drawer"])

    # ---------------- сборка всего окна ----------------
    def build_ui():
        for w in list(root.winfo_children()):
            w.destroy()
        ui.clear()
        fields.clear()
        cache.clear()
        ui["pages"] = {}
        S["st_visible"] = False
        root.update_idletasks()

        head = tk.Canvas(root, width=HW, height=HEAD, bg=BG, highlightthickness=0)
        head.place(x=0, y=0, relwidth=1)
        ui["head"] = head
        try:
            from PIL import ImageTk
            ui["scene"] = SceneRenderer(HW, HEAD, HUB_FLOWERS)
            t = time.perf_counter() - t_start
            ui["scene_img"] = ImageTk.PhotoImage(
                ui["scene"].render(99.0 if S["bloomed"] else t), master=root)
            head.create_image(0, 0, anchor="nw", image=ui["scene_img"])
        except Exception:
            S["bloomed"] = True
        head.create_rectangle(0, HEAD - 1, 4000, HEAD, fill=LINE, outline="")
        head.create_rectangle(10, 26, 50, 56, fill="", outline="", tags="burger")
        draw_burger()
        head.tag_bind("burger", "<Button-1>", lambda e: drawer_open(S["dgoal"] == 0))
        x_title = 62
        if imgs.get("logo") is not None:
            head.create_image(62, 40, anchor="w", image=imgs["logo"])
            x_title = 124
        head.create_text(x_title, 26, anchor="w", text=APP_NAME, fill=FG,
                         font=("Segoe UI", 20, "bold"))
        ui["crumb"] = head.create_text(x_title + 2, 58, anchor="w", text="", fill=DIM,
                                       font=("Segoe UI", 9, "bold"))
        head.create_polygon(*rrect(HW - 212, 26, HW - 150, 52, 13), smooth=True,
                            fill=PANEL, outline=LINE)
        head.create_text(HW - 181, 39, text=f"v{VERSION}", fill=DIM,
                         font=("Segoe UI", 9, "bold"))
        ui["pill"] = head.create_polygon(*rrect(HW - 140, 26, HW - 22, 52, 13),
                                         smooth=True, fill=PANEL, outline=LINE,
                                         tags="pill")
        ui["pill_t"] = head.create_text(HW - 81, 39, text="", fill=DIM,
                                        font=("Segoe UI", 9, "bold"), tags="pill")
        head.tag_bind("pill", "<Button-1>", lambda e: start_click())
        ui["upd_shown"] = False

        foot = tk.Canvas(root, width=HW, height=FOOT, bg="#0e0507", highlightthickness=0)
        foot.place(x=0, rely=1, y=-FOOT, relwidth=1)
        foot.create_rectangle(0, 0, 4000, 1, fill=LINE, outline="")
        ui["foot"] = foot
        ui["note"] = foot.create_text(HW - 20, FOOT / 2, anchor="e", text="", fill=DIM,
                                      font=("Segoe UI", 9), width=300)
        ui["btn_save"] = CanvasButton(foot, 16, 14, 118, tr("Save", "Сохранить"),
                                      lambda: save())
        ui["btn_reset"] = CanvasButton(foot, 144, 14, 124, tr("Defaults", "По умолчанию"),
                                       lambda: recommended())
        for key, _i, _t, _c in HUB_TABS:
            build_page(key)
        build_drawer()
        build_status()
        build_overlay()
        fill()
        show_tab(S["cur"])

    # ---------------- редактор зон: Fish box и Shake box ----------------
    def open_area_editor():
        """Полупрозрачный слой на весь экран с двумя прямоугольниками:
        Shake box (где искать кнопки shake) и Fish box (полоса миниигры).
        Тянуть за угол - менять размер, за середину - двигать.
        Enter - сохранить, Esc - отменить."""
        if ui.get("area") is not None:
            return
        if running:
            toggle_running()                  # ловля на паузу, пока правим зоны
        W_, H_ = root.winfo_screenwidth(), root.winfo_screenheight()
        ed = tk.Toplevel(root)
        ui["area"] = ed
        ed.overrideredirect(True)
        ed.attributes("-topmost", True)
        try:
            ed.attributes("-alpha", 0.62)
        except Exception:
            pass
        ed.geometry(f"{W_}x{H_}+0+0")
        cv = tk.Canvas(ed, width=W_, height=H_, bg="#050203", highlightthickness=0)
        cv.place(x=0, y=0)
        boxes = {
            "shake": [SHAKE_ZONE["left"], SHAKE_ZONE["top"],
                      SHAKE_ZONE["left"] + SHAKE_ZONE["width"],
                      SHAKE_ZONE["top"] + SHAKE_ZONE["height"]],
            "fish": [REGION["left"], REGION["top"], REGION["left"] + REGION["width"],
                     REGION["top"] + REGION["height"]],
            "cast": [CAST_ZONE["left"], CAST_ZONE["top"],
                     CAST_ZONE["left"] + CAST_ZONE["width"],
                     CAST_ZONE["top"] + CAST_ZONE["height"]]}
        COL = {"shake": ("#e0243a", "Shake box"), "fish": ("#2f7bff", "Fish box"),
               "cast": ("#2fbf71", "Cast bar box")}
        ORDER = ("fish", "cast", "shake")         # кто сверху при щелчке
        drag = {}
        HS = 9

        def draw():
            cv.delete("box")
            for k in ORDER[::-1]:
                x1, y1, x2, y2 = boxes[k]
                col, name = COL[k]
                cv.create_rectangle(x1, y1, x2, y2, fill=col, outline=col, width=2,
                                    stipple="gray50", tags="box")
                cv.create_rectangle(x1, y1, x2, y2, outline=col, width=2, tags="box")
                cv.create_text(x1 + 16, y1 - 14, anchor="w", text=name, fill=col,
                               font=("Segoe UI", 13, "bold"), tags="box")
                cv.create_text(x2 - 16, y1 - 14, anchor="e", fill="#ffffff",
                               font=("Consolas", 9), tags="box",
                               text=f"{x1},{y1}  {x2 - x1}x{y2 - y1}")
                for cx, cy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
                    cv.create_rectangle(cx - HS, cy - HS, cx + HS, cy + HS, fill="#ffffff",
                                        outline=col, width=2, tags="box")
                    cv.create_rectangle(cx - 2, cy - 2, cx + 2, cy + 2, fill=col,
                                        outline="", tags="box")
            # подсказка и кнопки сверху
            cv.delete("hud")
            cx = W_ // 2
            cv.create_rectangle(cx - 330, 14, cx + 330, 74, fill=PANEL, outline=ACC,
                                width=2, tags="hud")
            cv.create_text(cx, 32, fill=FG, font=("Segoe UI", 11, "bold"), tags="hud",
                           text=tr("Drag corners to resize, the middle to move",
                                   "Угол - размер, середина - сдвиг"))
            cv.create_text(cx, 56, fill=DIM, font=("Segoe UI", 9), tags="hud",
                           text=tr("Fish box: a bit larger than the fishing bar  \u00b7  "
                                   "Enter - save  \u00b7  Esc - cancel  \u00b7  R - defaults",
                                   "Fish box - чуть больше полосы рыбы  \u00b7  "
                                   "Enter - сохранить  \u00b7  Esc - отмена  \u00b7  R - по умолч."))

        def hit(x, y):
            for k in ORDER:
                x1, y1, x2, y2 = boxes[k]
                for ci, (cx, cy) in enumerate(((x1, y1), (x2, y1), (x1, y2), (x2, y2))):
                    if abs(x - cx) <= HS + 3 and abs(y - cy) <= HS + 3:
                        return k, "corner", ci
            for k in ORDER:
                x1, y1, x2, y2 = boxes[k]
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return k, "move", None
            return None

        def press(e):
            h = hit(e.x, e.y)
            drag.clear()
            if h:
                drag.update(k=h[0], mode=h[1], ci=h[2], x=e.x, y=e.y,
                            orig=list(boxes[h[0]]))

        def motion(e):
            if not drag:
                return
            k, o = drag["k"], drag["orig"]
            dx, dy = e.x - drag["x"], e.y - drag["y"]
            if drag["mode"] == "move":
                b = [o[0] + dx, o[1] + dy, o[2] + dx, o[3] + dy]
            else:
                b = list(o)
                ci = drag["ci"]
                b[0 if ci in (0, 2) else 2] = (o[0] if ci in (0, 2) else o[2]) + dx
                b[1 if ci in (0, 1) else 3] = (o[1] if ci in (0, 1) else o[3]) + dy
            x1, x2 = sorted((b[0], b[2]))
            y1, y2 = sorted((b[1], b[3]))
            minw, minh = {"fish": (100, 30), "cast": (60, 200)}.get(k, (80, 80))
            if x2 - x1 >= minw and y2 - y1 >= minh:
                boxes[k] = [max(0, x1), max(0, y1), min(W_, x2), min(H_, y2)]
                draw()

        def close():
            ui["area"] = None
            ed.destroy()

        def save(_e=None):
            sx1, sy1, sx2, sy2 = [int(v) for v in boxes["shake"]]
            cx1, cy1, cx2, cy2 = [int(v) for v in boxes["cast"]]
            fx1, fy1, fx2, fy2 = [int(v) for v in boxes["fish"]]
            fw = fx2 - fx1
            vals = {"SHAKE_LEFT": sx1, "SHAKE_TOP": sy1, "SHAKE_WIDTH": sx2 - sx1,
                    "SHAKE_HEIGHT": sy2 - sy1, "FISH_LEFT": fx1, "FISH_TOP": fy1,
                    "FISH_WIDTH": fw, "FISH_HEIGHT": fy2 - fy1,
                    "CAST_LEFT": cx1, "CAST_TOP": cy1, "CAST_WIDTH": cx2 - cx1,
                    "CAST_HEIGHT": cy2 - cy1}
            # края полосы внутри зоны: как у зоны по умолчанию (72..848 из 920),
            # то есть с запасом ~8% по бокам
            if (fx1, fy1, fw, fy2 - fy1) != (500, 872, 920, 88):
                vals["BAR_LO"] = max(2, round(fw * 72 / 920))
                vals["BAR_HI"] = min(fw - 2, round(fw * 848 / 920))
            else:
                vals["BAR_LO"], vals["BAR_HI"] = 72, 848
            ini_write({k: str(v) for k, v in vals.items()})
            reload_config()
            BAR_TRACKER.reset()
            say(f"зоны: shake {sx1},{sy1} {sx2 - sx1}x{sy2 - sy1} | рыба {fx1},{fy1} "
                f"{fw}x{fy2 - fy1}")
            note(tr("Areas saved", "Зоны сохранены"), OK_C)
            close()
            # треугольники поверх игры стоят над Fish box - переставить
            try:
                if ui.get("ov") is not None:
                    ui["ov"].destroy()
            except Exception:
                pass
            build_overlay()
            CAST_BAR.roi = None
            for p in ("shake", "extra", "cast"):
                rebuild_page(p)

        def defaults(_e=None):
            boxes["shake"] = [_REG["SHAKE_LEFT"], _REG["SHAKE_TOP"],
                              _REG["SHAKE_LEFT"] + _REG["SHAKE_WIDTH"],
                              _REG["SHAKE_TOP"] + _REG["SHAKE_HEIGHT"]]
            boxes["fish"] = [_REG["FISH_LEFT"], _REG["FISH_TOP"],
                             _REG["FISH_LEFT"] + _REG["FISH_WIDTH"],
                             _REG["FISH_TOP"] + _REG["FISH_HEIGHT"]]
            boxes["cast"] = [_REG["CAST_LEFT"], _REG["CAST_TOP"],
                             _REG["CAST_LEFT"] + _REG["CAST_WIDTH"],
                             _REG["CAST_TOP"] + _REG["CAST_HEIGHT"]]
            draw()

        cv.bind("<ButtonPress-1>", press)
        cv.bind("<B1-Motion>", motion)
        cv.bind("<ButtonRelease-1>", lambda e: drag.clear())
        ed.bind("<Return>", save)
        ed.bind("<Escape>", lambda e: close())
        ed.bind("<r>", defaults)
        ed.bind("<R>", defaults)
        draw()
        ed.focus_force()

    # ---------------- треугольники поверх игры ----------------
    def build_overlay():
        """Прозрачное окно поверх игры прямо над полосой миниигры: три
        треугольника - над левым и правым концом блока и над рыбой.

        Стоит СТРОГО ВЫШЕ области, которую снимает детектор (и ниже зоны
        карточки), - иначе макрос увидел бы собственные треугольники.
        Щелчки проходят сквозь него в игру. Прозрачность окна есть только
        в Windows; где её нет, окно не создаётся."""
        ui["ov"] = None
        OW, OH = REGION["width"] + 40, 24
        try:
            ov = tk.Toplevel(root)
            ov.overrideredirect(True)
            ov.attributes("-topmost", True)
            TRANS = "#010203"
            ov.configure(bg=TRANS)
            ov.attributes("-transparentcolor", TRANS)
        except Exception:
            try:
                ov.destroy()
            except Exception:
                pass
            return
        ov.geometry(f"{OW}x{OH}+{REGION['left'] - 20}+{REGION['top'] - OH - 2}")
        oc = tk.Canvas(ov, width=OW, height=OH, bg=TRANS, highlightthickness=0)
        oc.place(x=0, y=0)

        def tri(fill, outline, w):
            return oc.create_polygon(0, 0, 0, 0, 0, 0, fill=fill, outline=outline,
                                     width=2, state="hidden"), w
        ui["ov_l"] = tri(ACC, "#2a0005", 8)
        ui["ov_r"] = tri(ACC, "#2a0005", 8)
        ui["ov_f"] = tri("#ffffff", "#c1121f", 10)
        ov.withdraw()
        try:
            import ctypes
            ov.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(ov.winfo_id())
            GWL_EXSTYLE = -20
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # сквозь окно щёлкается, его нет на панели задач, фокус не берёт
            ex |= 0x80000 | 0x20 | 0x80 | 0x08000000
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass
        ui["ov"], ui["oc"], ui["ov_on"] = ov, oc, False

    def overlay_update(st):
        ov = ui.get("ov")
        if ov is None:
            return
        show = (SHOW_OVERLAY and running and st["state"] == "reel"
                and st["left"] is not None)
        if show != ui["ov_on"]:
            ui["ov_on"] = show
            if show:
                ov.deiconify()
                ov.lift()
            else:
                ov.withdraw()
        if not show:
            return
        oc = ui["oc"]
        H_ = 22
        for key, x in (("ov_l", st["left"]), ("ov_r", st["right"]),
                       ("ov_f", st["fish"])):
            item, w = ui[key]
            if x is None:
                setc(oc, item, state="hidden")
                continue
            x += 20
            setxy(oc, item, x - w, 2, x + w, 2, x, H_)
            setc(oc, item, state="normal")

    # ---------------- окно статуса ----------------
    def build_status():
        SW, SH = 400, 214
        stw = tk.Toplevel(root)
        stw.title(f"{APP_NAME}")
        stw.configure(bg=BG)
        stw.geometry(f"{SW}x{SH}+24+24")
        stw.resizable(False, False)
        stw.attributes("-topmost", True)
        stw.protocol("WM_DELETE_WINDOW", lambda: toggle_running() if running else None)
        style_titlebar(stw)
        if imgs:
            try:
                stw.iconphoto(False, imgs["i64"], imgs["i32"])
            except Exception:
                pass
        sc = tk.Canvas(stw, width=SW, height=SH, bg=BG, highlightthickness=0)
        sc.place(x=0, y=0)
        try:
            from PIL import ImageTk
            st_img = ImageTk.PhotoImage(SceneRenderer(SW, SH, STATUS_FLOWERS).render(99.0),
                                        master=root)
            sc.create_image(0, 0, anchor="nw", image=st_img)
            sc.st_img = st_img
        except Exception:
            pass
        x0 = 16
        if imgs.get("logo") is not None:
            sc.create_image(14, 24, anchor="w", image=imgs["logo"])
            x0 = 74
        sc.create_text(x0, 8, anchor="nw", text=APP_NAME, fill=FG,
                       font=("Segoe UI", 14, "bold"))
        ui["s_phase"] = sc.create_text(x0, 34, anchor="nw", text="", fill=FG,
                                       font=("Segoe UI", 9))
        ui["s_rod"] = sc.create_text(16, 58, anchor="nw", text="", fill=DIM,
                                     font=("Segoe UI", 8))
        TX, TY, TW, TH = 16, 80, 368, 44
        sc.create_polygon(*rrect(TX, TY, TX + TW, TY + TH, 8), smooth=True,
                          fill=PANEL, outline=LINE)
        ui["trk"] = (sc.create_rectangle(0, 0, 0, 0, fill="#3a0a12", outline=ACC,
                                         width=2, state="hidden"),
                     sc.create_line(0, 0, 0, 1, fill="#ff8a95", state="hidden"),
                     sc.create_line(0, 0, 0, 1, fill="#ffffff", width=3, state="hidden"),
                     (TX, TY, TW, TH))
        ui["s_info"] = sc.create_text(16, 134, anchor="nw", text="", fill=FG,
                                      font=("Consolas", 9))
        ui["s_stat"] = sc.create_text(16, 168, anchor="nw", text="", fill=DIM,
                                      font=("Consolas", 9))
        sc.create_text(16, 190, anchor="nw", fill=HINT, font=("Segoe UI", 8),
                       text=tr(f"{key_label(KEY_START)} pause   "
                               f"{key_label(KEY_SNAP)} snapshot   {key_label(KEY_EXIT)} exit",
                               f"{key_label(KEY_START)} пауза   "
                               f"{key_label(KEY_SNAP)} снимок   {key_label(KEY_EXIT)} выход"))
        stw.withdraw()
        ui["stw"], ui["sc"] = stw, sc

    # ---------------- цикл ----------------
    def tick():
        pump_log()
        with STATE_LOCK:
            st = dict(STATE)
        run = st["state"] == "reel"
        if not S["bloomed"] and "scene" in ui:
            t = time.perf_counter() - t_start
            if t - S["t_frame"] > 0.08:
                S["t_frame"] = t
                end = ui["scene"].bloom_end
                try:
                    ui["scene_img"].paste(ui["scene"].render(min(t, end)))
                except Exception:
                    S["bloomed"] = True
                if t >= end:
                    S["bloomed"] = True
        if running != S["st_visible"]:
            S["st_visible"] = running
            if running:
                ui["stw"].deiconify()
                ui["stw"].lift()
            else:
                ui["stw"].withdraw()
        # где сейчас окно статуса: поиск shake эту область пропускает
        # (в окне белый текст - pixel иначе щёлкал бы по нему)
        try:
            if S["st_visible"]:
                w_ = ui["stw"]
                x_, y_ = w_.winfo_rootx(), w_.winfo_rooty()
                SKIP_RECTS[:] = [(x_ - 4, y_ - 34, x_ + w_.winfo_width() + 4,
                                  y_ + w_.winfo_height() + 4)]
            else:
                SKIP_RECTS[:] = []
        except Exception:
            pass
        mins = f"{int(st['runtime']) // 60:02d}:{int(st['runtime']) % 60:02d}"
        head = ui["head"]
        setc(ui["drawer"], ui["dr_state"], fill=ACC if running else DIM,
             text="\u25cf " + (tr("fishing", "ловлю") if running else tr("paused", "пауза")))
        setc(head, ui["pill"], fill=ON_RED if running else PANEL,
             outline="#c1121f" if running else LINE)
        setc(head, ui["pill_t"], fill="#ffffff" if running else DIM,
             text="\u25cf " + (tr("fishing", "ловлю") if running else tr("paused", "пауза")))
        b = ui.get("start_btn")
        if b is not None:
            setc(b.cv, b.label, text=("\u25a0  " + tr("Stop", "Остановить")) if running
                 else ("\u25b6  " + tr("Start fishing", "Начать ловлю")))
            main = ui["pages"]["main"]
            setc(main, ui["stats"], text=tr(
                f"caught {st['caught']}   lost {st['lost']}   {mins}",
                f"взято {st['caught']}   сорвалось {st['lost']}   {mins}"))
            setc(main, ui["main_rod"], text=tr("rod: ", "удочка: ") + rod_name())
            if "perf_fps" in ui and st["fps"]:
                setc(main, ui["perf_fps"], text=tr(f"fight: {st['fps']} fps",
                                                   f"в бою: {st['fps']} к/с"))
        overlay_update(st)
        # нашлась новая версия - значок в шапке (щелчок - страница релиза)
        if UPDATE_INFO and not ui.get("upd_shown"):
            ui["upd_shown"] = True
            h_ = ui["head"]
            h_.create_polygon(*rrect(HW - 312, 26, HW - 222, 52, 13), smooth=True,
                              fill=_mix(PANEL, "#2fbf71", 0.35), outline="#2fbf71",
                              tags="upd")
            h_.create_text(HW - 267, 39, text="\u2b06 " + UPDATE_INFO[0], fill="#ffffff",
                           font=("Segoe UI", 9, "bold"), tags="upd")
            h_.tag_bind("upd", "<Button-1>", lambda e: open_update())
            rebuild_page("extra")
        if area_pending:
            globals()["area_pending"] = False
            open_area_editor()
        if S["st_visible"]:
            sc = ui["sc"]
            blk, mid, fsh, (TX, TY, TW, TH) = ui["trk"]
            rw = REGION["width"]
            if st["left"] is not None:
                x1, x2 = TX + st["left"] * TW / rw, TX + st["right"] * TW / rw
                setxy(sc, blk, x1, TY + 8, x2, TY + TH - 8)
                setxy(sc, mid, (x1 + x2) / 2, TY + 6, (x1 + x2) / 2, TY + TH - 6)
                setc(sc, blk, state="normal")
                setc(sc, mid, state="normal")
            else:
                setc(sc, blk, state="hidden")
                setc(sc, mid, state="hidden")
            if st["fish"] is not None:
                xf = TX + st["fish"] * TW / rw
                setxy(sc, fsh, xf, TY + 2, xf, TY + TH - 2)
                setc(sc, fsh, state="normal")
            else:
                setc(sc, fsh, state="hidden")
            c = st["conf"]
            phase = {"cast": tr("casting", "заброс"), "wait": tr("waiting", "жду поклёвку"),
                     "reel": tr("reeling", "вываживание")}.get(st["state"], st["state"])
            setc(sc, ui["s_phase"], text="\u25cf " + phase, fill=ACC if run else FG)
            setc(sc, ui["s_rod"], text=tr("rod: ", "удочка: ") + rod_name())
            setc(sc, ui["s_info"], fill=FG if run else DIM, text=tr(
                f"error {st['error']:+5d} px     duty {st['duty'] * 100:3.0f}%\n"
                f"bar   {st['width']:4d} px     fps  {st['fps']:3d}",
                f"ошибка {st['error']:+5d} px     нажатие {st['duty'] * 100:3.0f}%\n"
                f"полоса {st['width']:4d} px     кадров {st['fps']:3d}/с"))
            setc(sc, ui["s_stat"], fill=OK_C if c > 0.75 else (WARN_C if c > 0.45 else BAD_C),
                 text=tr(f"confidence {c * 100:3.0f}%   caught {st['caught']}  "
                         f"lost {st['lost']}   {mins}",
                         f"уверенность {c * 100:3.0f}%   взято {st['caught']}  "
                         f"сорв {st['lost']}   {mins}"))
        if not should_exit:
            root.after(50, tick)
        else:
            root.destroy()

    def on_resize(e=None):
        if e is not None and e.widget is not root:
            return
        if ui.get("pages") and S["cur"] in ui["pages"]:
            place_page(ui["pages"][S["cur"]])
            dh = max(200, root.winfo_height() - HEAD)
            ui["drawer"].config(height=dh)
            if "drawer_img" in ui:
                ui["drawer"].coords(ui["drawer_img"], 0, dh - 460)

    def wheel(e):
        cv = ui.get("pages", {}).get(S["cur"])
        if cv is not None and getattr(cv, "page_h", 0) > cv.winfo_height():
            cv.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def on_close():
        global should_exit
        should_exit = True

    root.bind("<Configure>", on_resize)
    root.bind("<Motion>", on_motion)
    root.bind_all("<MouseWheel>", wheel)
    root.bind("<Control-s>", lambda _e: save())
    root.protocol("WM_DELETE_WINDOW", on_close)
    build_ui()
    threading.Thread(target=check_updates, daemon=True).start()
    if not WELCOME_DONE:
        root.after(700, show_welcome)
    tick()
    root.mainloop()


if __name__ == "__main__":
    say(f"{APP_NAME} {VERSION}")
    say(f"цель: сглаживание "
        f"{str(AIM_TAU) + 'с, компенсация ' + str(AIM_LEAD) if AIM_TAU > 0 else 'ВЫКЛ'}")
    say(f"GAIN {GAIN} | LEAD {LEAD} | профиль {PROFILE or 'общий'}")
    _mig = ini_migrate()          # до ini_sync: ему нужно видеть старый файл
    _n = ini_sync()
    if _n:
        say(f"в scarlet.ini дописано настроек: {_n} (раньше их там не было)")
    if _mig:
        say("scarlet.ini: обновлены настройки shake (имена режимов / зона поиска)")
    _gone = ini_drop_obsolete()
    if _gone:
        say("из scarlet.ini убраны устаревшие настройки: " + ", ".join(sorted(set(_gone))))
    _np = ini_seed_profiles()
    if _np:
        say(f"в scarlet.ini не было удочек - добавил известные: "
            + ", ".join(p[0] for p in BUILTIN_PROFILES))
    if _OVERRIDE:
        say(f"scarlet.ini ПЕРЕБИВАЕТ {len(_OVERRIDE)} настроек из кода:")
        for _k in sorted(_OVERRIDE):
            _d, _v = _OVERRIDE[_k]
            say(f"    {_k}: в коде {_d}, в файле {_v}  <- работает файл")
        say("    кнопка \"Рекомендуемые\" во вкладке вернёт значения из кода")
    log_env_check("проверка при запуске")
    threading.Thread(target=pwm_loop, name="ШИМ", daemon=True).start()
    if USE_GUI:
        def guarded():
            try:
                control_loop()
            except Exception:
                import traceback
                txt = traceback.format_exc()
                print("\n!!! ОШИБКА В РАБОЧЕМ ПОТОКЕ !!!")
                print(txt)
                try:
                    with open(os.path.join(APP_DIR, "error.log"), "a",
                              encoding="utf-8") as fh:
                        fh.write(time.strftime("%Y-%m-%d %H:%M:%S\n") + txt + "\n")
                    print("Записал в error.log - пришли этот файл")
                except Exception:
                    pass
                globals()["should_exit"] = True

        worker = threading.Thread(target=guarded, daemon=True)
        worker.start()
        try:
            run_gui()
        except Exception:
            import traceback
            _txt = traceback.format_exc()
            log_crash("запуск окна", _txt)
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, "Scarlet hub: окно не запустилось.\n"
                       "Подробности в error.log рядом с программой.\n\n"
                       + _txt[-600:], "Scarlet hub", 0x10)
            except Exception:
                print(_txt)
        finally:
            should_exit = True
            worker.join(timeout=2.0)
    else:
        try:
            control_loop()
        except Exception:
            import traceback
            traceback.print_exc()
            input("Ошибка. Нажми Enter...")
