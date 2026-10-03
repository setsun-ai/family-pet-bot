"""
What the pet says, independent of the chat platform: help, /status, /check
and /preview texts. Telegram (handlers.py) and Discord (discord_bot.py) only
deliver them. `app` is either platform's App: both have the same services.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .common import ServiceError
from .family import PRAISE_WINDOW, praise_slot, week_key
from .i18n import t, weekday_name
from .mood import MOODS
from .scheduler import news_plan


def help_text(app, platform: str = "telegram") -> str:
    s = app.settings
    lines = [t("help_intro" if platform == "telegram" else "help_intro_discord", name=s.display_name)]
    if s.news_enabled:
        lines.append(t("help_news"))
    for team in app.sports.teams:
        lines.append(t("help_team", command=team.command, name=team.name))
    lines.append(t("help_common" if platform == "telegram" else "help_common_discord"))
    lines.append(t("help_owner" if platform == "telegram" else "help_owner_discord"))
    return "\n".join(lines)


async def status_text(app) -> str:
    s = app.settings
    now = datetime.now(s.tz)
    today = now.date().isoformat()
    slots = news_plan(now, s, await app.db.family() or 0)
    none = t("status_none")
    lines = [
        t("status_title", name=s.display_name),
        t("status_db", ok=t("ok") if await app.db.ping() else t("error_word")),
        t("status_owner", owner=await app.db.owner()),
        t("status_chat", chat=await app.db.family() or t("status_chat_unset")),
        t("status_ai", provider=s.ai_provider, model=s.model),
        t("status_ai_last", value=app.ai.last_success or t("status_not_yet")),
        t("status_ai_error", value=app.ai.last_error or none),
        t("status_ai_usage", used=await app.db.usage(today), limit=s.max_ai_calls_per_day),
        t("status_timezone", tz=s.timezone),
        t("status_quiet", start=s.quiet_start_hour, end=s.quiet_end_hour),
    ]
    if s.news_enabled:
        times = ", ".join(f"{slot.start:%H:%M}–{slot.end:%H:%M}" for slot in slots)
        if s.news_mode == "on_request":
            days = ", ".join(weekday_name(day) for day in s.news_refresh_days)
            lines.append(t("status_news_stock", n=len(await app.db.stock(await app.db.family() or 0)), days=days,
                           hour=f"{s.news_hour:02d}:{s.news_minute:02d}",
                           last=await app.db.get("news_restocked") or t("status_not_yet")))
        lines += [t("status_news", mode=s.news_mode, times=times or "—"),
                  t("status_news_ai", used=await app.db.category_usage(today, "news"), limit=s.max_news_ai_calls_per_day),
                  t("status_rss", value=app.news.last_error or none)]
    else:
        lines.append(t("status_news_off"))
    for team in app.sports.teams:
        state = app.sports.state[team.key]
        when = lambda m: f"{m.kickoff.astimezone(s.tz):%d.%m %H:%M} {m.home}–{m.away}" if m and m.kickoff else "—"  # noqa: E731
        lines.append(t("status_team", name=team.name, next=when(state.upcoming), last=when(state.last),
                       error=state.error or none))
    if not app.sports.teams:
        lines.append(t("status_sports_off"))
    if app.family and app.family.members:
        birthdays = ", ".join(f"{m.name} {m.birthday}" for m in app.family.members if m.birthday) or none
        lines.append(t("status_family", n=len(app.family.members), birthdays=birthdays))
        if s.praise_enabled:
            member, _ = await app.family.choose()
            chat = await app.db.family()
            args = (s.praise_weekday, s.praise_hour, s.praise_until_hour, str(chat))
            slot = praise_slot(now, *args)
            if now >= slot + PRAISE_WINDOW or (chat and await app.db.has_delivery(chat, "praise", week_key(now))):
                slot = praise_slot(now + timedelta(weeks=1), *args)
            lines.append(t("status_praise", day=weekday_name(slot.weekday()), time=f"{slot:%d.%m %H:%M}",
                           name=member.name if member else none))
    if getattr(app, "mood", None):
        mood = await app.mood.current()
        until = app.mood.until()
        lines.append(t("status_mood", mood=mood.label, until=f"{until:%H:%M}" if until else "—"))
    lines += [
        t("status_nicknames", n=len(await app.db.family_names())),
        t("status_delivery", value=app.delivery.last_error or none),
        t("status_uncertain", n=len(await app.db.uncertain_deliveries())),
        t("status_scheduler", value="; ".join(f"{k}: {v}" for k, v in app.scheduler.last_errors.items()) or none),
    ]
    return "\n".join(lines)


async def check_text(app) -> str:
    """/check: real connectivity test (one small, possibly paid AI request)."""
    lines = [t("check_title")]
    app.last_check_ok = await app.db.ping()
    lines.append("SQLite: " + (t("ok") if app.last_check_ok else t("error_word")))
    try:
        await app.ai.check()
        lines.append(t("check_ai_ok"))
    except ServiceError as error:
        app.last_check_ok = False
        lines.append(t("check_ai_failed", error=error))
    if app.settings.news_enabled:
        try:
            items = await app.news.articles(datetime.now(UTC))
            lines.append(t("check_rss_ok", n=len(items)))
            if app.news.last_error:
                lines.append("RSS: " + app.news.last_error)
        except ServiceError as error:
            app.last_check_ok = False
            lines.append("RSS: " + str(error))
    for team in app.sports.teams:
        try:
            state = await app.sports.fetch(team, force=True)
            lines.append(f"{team.name}: " + t("check_sports_ok", n=sum(1 for m in (state.upcoming, state.last) if m)))
        except ServiceError as error:
            app.last_check_ok = False
            lines.append(f"{team.name}: {error}")
    return "\n".join(lines)


async def mood_text(app, args: str) -> str:
    """/mood - the current mood; /mood <name> - set it for 3 hours (owner)."""
    if not getattr(app, "mood", None):
        return t("mood_off")
    names = ", ".join(MOODS)
    wanted = args.strip().lower()
    if wanted:
        if wanted not in MOODS:
            return t("mood_usage", names=names)
        mood = await app.mood.force(wanted)
        return t("mood_set", mood=mood.label)
    mood = await app.mood.current()
    until = app.mood.until()
    return t("mood_now", mood=mood.label, until=f"{until:%H:%M}" if until else "—", names=names)


async def memory_text(app, args: str) -> str:
    """/memory - what the pet remembers about the family chat; /memory del N - forget one fact (owner)."""
    if not app.settings.memory_enabled:
        return t("memory_off")
    chat = await app.db.family()
    if not chat:
        return t("status_chat_unset")
    parts = args.split()
    if len(parts) == 2 and parts[0] in {"del", "delete", "forget"} and parts[1].isdigit():
        return t("memory_deleted") if await app.db.delete_memory(chat, int(parts[1])) else t("memory_missing")
    rows = await app.db.memories(chat, app.settings.memory_days)
    if not rows:
        return t("memory_empty")
    tz = app.settings.tz
    body = "\n".join(f"{r['id']}. {datetime.fromisoformat(r['created_at']).astimezone(tz):%d.%m} {r['fact']}" for r in rows)
    return t("memory_list", facts=body)


async def stats_text(app, days: int = 30) -> str:
    """/stats - which posts the family reacts to (owner). Telegram reports reactions only to group admins."""
    chat = await app.db.family()
    rows = await app.db.feedback_stats(chat, days) if chat else []
    if not rows:
        return t("stats_empty")
    lines = [t("stats_title", days=days)]
    for row in rows:
        top = " ".join(f"{emoji}×{n}" for emoji, n in row["top"])
        lines.append(t("stats_line", kind=t("kind_" + row["kind"]) if t("kind_" + row["kind"]) != "kind_" + row["kind"]
                       else row["kind"], sent=row["sent"], reactions=row["reactions"] or 0, replies=row["replies"] or 0,
                       top=top or "—"))
    lines.append(t("stats_note"))
    return "\n".join(lines)


async def preview_text(app, args: str) -> str | None:
    """
    /preview news | praise [name] | birthday [name] | <team command>
    A post exactly as the family would get it - for the owner's eyes only;
    nothing is sent to the family and nothing is marked as sent. One AI request.
    Returns None for unknown arguments, "" when there is nothing to show.
    """
    what, _, who = args.partition(" ")
    what, who = what.lower().lstrip("/"), who.strip()
    now = datetime.now(app.settings.tz)
    if what == "news":
        for article in (await app.news.articles(datetime.now(UTC)))[:app.settings.news_max_candidates]:
            decision = await app.ai.news(article.title, article.summary)
            if decision.accepted:
                return f"{decision.text}\n\n{t('news_source', date=f'{article.published:%d.%m.%Y}', link=article.link)}"
        return ""
    if what == "spontaneous":
        facts = {"today": f"{weekday_name(now.weekday())} {now:%d.%m %H:%M}", "memory": who or None}
        return await app.ai.post("spontaneous", facts, max_parts=1, maximum=200)
    if what in {"praise", "birthday"} and app.family and app.family.members:
        member = app.family.member(who) if who else (await app.family.choose())[0]
        if not member:
            return ""
        return await (app.family.praise_text(member, now) if what == "praise" else app.family.birthday_text(member, now))
    if (team := app.sports.team(what)) is not None:
        state = await app.sports.fetch(team, force=True)
        if state.last and state.last.status == "finished":
            return await app.sports.result_message(team, state.last, state.upcoming)
        if state.upcoming:
            return await app.sports.preview_message(team, state.upcoming)
        return ""
    return None
