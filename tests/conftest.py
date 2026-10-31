"""Synthetic ru/kk/uz/en corpus. NOT Grand Mobile data: written for tests only."""
from __future__ import annotations

import json

import pytest

from locqa.arb import ArbFile
from locqa.context import Context, Options
from locqa.glossary import Glossary, GlossaryEntry

# key: (ru, kk)
PAIRS = {
    "player_profile_title": ("Профиль игрока", "Ойыншы профилі"),
    "player_list_title": ("Список игроков", "Ойыншылар тізімі"),
    "player_online": ("Игроков онлайн: {count}", "Желідегі ойыншылар: {count}"),
    "invite_player_button": ("Пригласить игрока", "Ойыншыны шақыру"),
    "kick_player_button": ("Исключить игрока", "Ойыншыны шығару"),
    "player_not_found": ("Игрок не найден", "Ойыншы табылмады"),
    "send_money_title": ("Перевод денег", "Ақша аудару"),
    "send_money_to": ("Отправить деньги игроку {name}", "{name} ойыншысына ақша жіберу"),
    "money_balance": ("Баланс: {amount}", "Баланс: {amount}"),
    "not_enough_money": ("Недостаточно денег", "Ақша жеткіліксіз"),
    "buy_house_button": ("Купить дом", "Үй сатып алу"),
    "sell_house_button": ("Продать дом", "Үйді сату"),
    "house_title": ("Мой дом", "Менің үйім"),
    "house_rent_button": ("Арендовать дом", "Үйді жалға алу"),
    "garage_rent_button": ("Арендовать", "Жалға алу"),
    "buy_car_button": ("Купить машину", "Көлік сатып алу"),
    "sell_car_button": ("Продать машину", "Көлікті сату"),
    "car_list_title": ("Мои машины", "Менің көліктерім"),
    "faction_join_button": ("Вступить во фракцию", "Фракцияға кіру"),
    "faction_leave_button": ("Покинуть фракцию", "Фракциядан шығу"),
    "faction_title": ("Фракция", "Фракция"),
    "faction_members": ("Участники фракции", "Фракция мүшелері"),
    "family_title": ("Семья", "Отбасы"),
    "family_create_button": ("Создать семью", "Отбасы құру"),
    "family_invite": ("{name} приглашает вас в семью", "{name} сізді отбасына шақырады және күтеді"),
    "business_buy_button": ("Купить бизнес", "Бизнес сатып алу"),
    "business_income": ("Доход бизнеса: {amount}", "Бизнес табысы: {amount}"),
    "confirm_button": ("Подтвердить", "Растау"),
    "cancel_button": ("Отмена", "Бас тарту"),
    "close_button": ("Закрыть", "Жабу"),
    "save_button": ("Сохранить", "Сақтау"),
    "settings_title": ("Настройки", "Баптаулар"),
    "error_title": ("Ошибка", "Қате"),
    "error_network": ("Ошибка сети. Попробуйте ещё раз.", "Желі қатесі. Қайталап көріңіз."),
    "error_unknown": ("Неизвестная ошибка", "Белгісіз қате"),
    "error_server": ("Ошибка сервера", "Сервер қатесі"),
    "error_input": ("Неверный ввод", "Қате енгізу"),
    "error_code": ("Код ошибки: {code}", "Қате коды: {code}"),
    "warn_received": ("Вы получили варн от администратора", "Сіз әкімшіден ескерту алдыңыз"),
    "warn_count": ("Предупреждений: {count}", "Ескертулер: {count}"),
    "job_taxi": ("Таксист", "Такси жүргізушісі"),
    "job_police": ("Полицейский", "Полицей"),
    "job_mayor": ("Мэр", "Әкім"),
    "welcome_message": ("Добро пожаловать в Grand Mobile!", "Grand Mobile әлеміне қош келдіңіз!"),
    "coins_buy_button": ("Купить Grand Coins", "Grand Coins сатып алу"),
    "coins_balance": ("У вас {count} Grand Coins", "Сізде {count} Grand Coins бар және күтеді"),
    "items_count": (
        "{count, plural, one{{count} предмет} few{{count} предмета} many{{count} предметов} other{{count} предмета}}",
        "{count, plural, one{{count} зат} other{{count} зат}}",
    ),
    "level_up": ("Новый уровень: {level}", "Жаңа деңгей: {level}"),
    "daily_bonus": ("Ежедневный бонус", "Күнделікті бонус"),
    "shop_title": ("Магазин", "Дүкен"),
    "inventory_title": ("Инвентарь", "Инвентарь"),
    "chat_title": ("Чат", "Чат"),
    "logout_confirm": ("Вы уверены, что хотите выйти?", "Шығуды қалайсыз ба?"),
    "version_label": ("Версия {version}", "Нұсқа {version}"),
    "server_name": ("Grand Mobile", "Grand Mobile"),
    "year": ("2026", "2026"),
    "house_buy_success": ("Вы купили дом за {price}", "Сіз үйді {price} бағасына сатып алдыңыз"),
    "car_buy_success": ("Вы купили машину за {price}", "Сіз көлікті {price} бағасына сатып алдыңыз"),
    "truck_rent_button": ("Арендовать фуру", "Фураны жалға алу"),
    "player_muted": ("Игрок {name} получил мут", "{name} ойыншысы мут алды"),
    "player_kicked": ("Игрок исключён", "Ойыншы шығарылды"),
    "quest_done": ("Задание выполнено", "Тапсырма орындалды"),
    "quest_title": ("Задания", "Тапсырмалар"),
    "quest_reward": ("Награда за задание", "Тапсырма үшін сыйлық"),
    "quest_new": ("Новое задание", "Жаңа тапсырма"),
    "quest_failed": ("Задание провалено", "Тапсырма орындалмады"),
    "quest_skip": ("Пропустить задание", "Тапсырманы өткізіп жіберу"),
    "quest_daily": ("Ежедневное задание", "Күнделікті тапсырма"),
}
UZ = {
    "confirm_button": "Tasdiqlash",
    "cancel_button": "Bekor qilish",
    "player_profile_title": "Oʻyinchi profili",
    "logout_confirm": "Chiqishni xohlaysizmi?",
    "not_enough_money": "Pul yetarli emas",
}
EN = {
    "confirm_button": "Confirm",
    "cancel_button": "Cancel",
    "player_not_found": "Player not found",
    "logout_confirm": "Are you sure you want to log out?",
}
DESCRIPTIONS = {"garage_rent_button": "Кнопка аренды гаража"}


def ru_data() -> dict:
    d = {"@@locale": "ru"}
    for k, (ru, _) in PAIRS.items():
        d[k] = ru
        if k in DESCRIPTIONS:
            d["@" + k] = {"description": DESCRIPTIONS[k]}
    return d


def kk_data(**overrides) -> dict:
    d = {"@@locale": "kk"}
    for k, (_, kk) in PAIRS.items():
        d[k] = kk
    for k, v in overrides.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def glossary() -> Glossary:
    return Glossary(
        [
            GlossaryEntry("фракция", "фракция", "faction", type="term", status="verified"),
            GlossaryEntry("семья", "отбасы", "family", ru_regex=r"\bсемь(?:я|и|е|ю|ей)\b", status="verified"),
            GlossaryEntry("Grand Coins", "Grand Coins", "Grand Coins", kk_forbidden=["Гранд Коинс"], type="brand", status="verified"),
            GlossaryEntry("варн", "ескерту", "warning", kk_variants=["варн"], ru_regex=r"\bварн\w*", type="slang", status="verified"),
        ]
    )


def make_ctx(
    kk_overrides: dict | None = None, *, gl: bool = True, refs: bool = False, src_overrides: dict | None = None, **opts
) -> Context:
    src = ArbFile.from_dict({**ru_data(), **(src_overrides or {})}, "intl_ru.arb")
    tgt = ArbFile.from_dict(kk_data(**(kk_overrides or {})), "intl_kk.arb")
    r = {"uz": UZ, "en": EN} if refs else {}
    return Context(src, tgt, glossary() if gl else None, r, Options(**opts))


@pytest.fixture
def files(tmp_path):
    """Write corpus to disk; returns dict of paths."""

    def write(name, data):
        p = tmp_path / name
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    gl = tmp_path / "glossary.csv"
    gl.write_text(
        "ru,kk,en,kk_variants,kk_forbidden,ru_regex,type,status,note\n"
        "фракция,фракция,faction,,,,term,verified,\n"
        "семья,отбасы,family,,,\\bсемь(?:я|и|е|ю|ей)\\b,term,verified,игровая сущность\n"
        "Grand Coins,Grand Coins,Grand Coins,,Гранд Коинс,,brand,verified,не переводить\n"
        "варн,ескерту,warning,варн,,\\bварн\\w*,slang,verified,\n",
        encoding="utf-8",
    )
    return {
        "ru": write("intl_ru.arb", ru_data()),
        "kk": write("intl_kk.arb", kk_data()),
        "uz": write("intl_uz.arb", {"@@locale": "uz", **UZ}),
        "en": write("intl_en.arb", {"@@locale": "en", **EN}),
        "glossary": gl,
        "dir": tmp_path,
        "write": write,
    }
