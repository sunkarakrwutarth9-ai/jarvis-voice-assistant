"""Tool router: send the model only the tools a request can need.

All ~90 tool descriptions are ~9,000 tokens; sending them on every request makes the first word slow and
the free models time out. The router keeps a core set (everyday actions) and adds groups whose keywords
appear in what the user said - plus every tool already used in the recent conversation, so follow-ups
(answering a quiz, "save it", "make it blue") keep working.
"""

import re

CORE = {"open_app", "close_app", "open_website", "web_search", "web_lookup", "youtube_play", "youtube_control",
        "media_key", "set_volume", "current_time", "world_time", "get_weather", "system_status", "create",
        "canvas_control", "save_creation", "revise_creation", "remember", "remember_note", "recall", "set_reminder",
        "schedule_task", "show_dashboard", "type_text", "press_keys", "take_screenshot", "read_screen", "deep_think",
        "calculate", "set_timer", "ultron_power", "open_browser", "find_files", "open_file", "do_task", "look", "watch"}

GROUPS = [
    (r"mail|inbox|gmail|draft|e-?mail|ఈమెయిల్|मेल", {"email_triage", "email_search", "email_draft", "connect_gmail"}),
    (r"friday|edith|karen|team|overnight|agent|scan my|security|audit|ఫ్రైడే|फ्राइडे", {"ask_agent", "overnight_shift"}),
    (r"\bac\b|air ?con|light|fan|plug|\btv\b|geyser|smart home|google home|cool(er)?|temperature|ఏసీ|लाइट|पंखा",
     {"smart_home", "connect_google_home"}),
    (r"stud|flash ?card|quiz|teach|class|lesson|exam|learn|tutor|revise|answer|నేర్|పరీక్ష|पढ़|सिखा",
     {"study", "quiz", "teach", "stop_class", "study_mode"}),
    (r"note|lecture|meeting|transcri|record", {"take_notes"}),
    (r"list|shopping|to-?do|remind|alarm|routine|cancel|when i say|grocer|wake me|గుర్తు|याद", {
        "list_add", "list_remove", "list_show", "list_clear", "cancel_reminder", "list_reminders", "save_routine",
        "run_routine", "delete_routine"}),
    (r"api|key|token|theme|ui|look|design|colou?r|dark|light mode|orb|style|sphere|gesture|robot|danc|focus|pomodoro|graph|voice|hologram|avatar",
     {"set_theme", "ui_theme", "api_keys", "orb_style", "gestures", "robot", "focus_mode", "show_memory_graph", "voice_mode", "learn_my_voice"}),
    (r"phone|telegram|\bqr\b|mobile|remote|ఫోన్|फोन", {"phone_remote", "connect_telegram"}),
    (r"screen|click|camera|webcam|look|see|watch|video|what('?s| is) (this|that)|చూడు|देख", {
        "click_on_screen", "look", "screen_copilot", "read_screen", "watch"}),
    (r"file|folder|pdf|doc|downloads|documents|desktop|explain", {"open_folder", "explain_file", "find_files", "open_file"}),
    (r"lock|sleep|shut ?down|restart|bright|window|minimi|maximi|scroll|desktop|close all|power",
     {"lock_pc", "sleep_pc", "power_off", "set_brightness", "window_control", "scroll", "show_desktop"}),
    (r"research|news|brief|morning|my day|today|screen time|journal|vault|yesterday|what did",
     {"research", "news_headlines", "briefing", "my_day", "day_tracking", "journal", "open_vault"}),
    (r"publish|online|go live|deploy|put it (up|live)|website", {"publish_website"}),
    (r"interpret|translat|అనువ|अनुवाद", {"interpreter"}),
    (r"chrome|browser|account|sign ?in|profile|youtube", {"open_browser", "youtube_sign_in", "youtube_control"}),
    (r"break|care|proactive|remind me to (stand|drink)", {"proactive"}),
    (r"code|program|script|python|java|run it", {"write_code", "run_creation", "open_in_editor"}),
    (r"interview|get to know|know me|about me|question", {"interview"}),
    (r"department|command layer|org|organi[sz]ation|ai company|growth|finance|socials|team for my|business", {
        "architect_departments", "activate_departments", "list_departments", "ask_department"}),
    (r"skill|learn (this|how)|the way i|my style|from now on", {"save_skill", "list_skills", "delete_skill"}),
    (r"calendar|meeting|event|appointment|agenda|schedule|task|tomorrow|today", {
        "calendar_agenda", "calendar_add", "tasks_list", "task_add", "connect_google_calendar"}),
    (r"brain|claude|chatgpt|openai|gpt|ollama|local model|open.?source|model", {"connect_brain", "brain_status"}),
]


def select(tools_list, user_text, history):
    """The subset of tool schemas for this request."""
    names = {t["function"]["name"] for t in tools_list}
    want = set(CORE)
    text = (user_text or "").lower()
    for pattern, group in GROUPS:
        if re.search(pattern, text, re.I):
            want |= group
    for m in history[-12:]:                                  # keep whatever the conversation is already using
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {}).get("name")
            if fn:
                want.add(fn)
        if m.get("role") == "tool" and m.get("name"):
            want.add(m["name"])
    if len(want & names) == len(CORE & names) and len(text.split()) > 14:
        return tools_list                                    # long, unusual request: give it everything
    return [t for t in tools_list if t["function"]["name"] in want]
