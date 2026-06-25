"""The catalog of skills the assistant can perform on the host.

This catalog is the single source of truth: it is rendered into the LLM's system
prompt (so the model knows what it can do) and used to validate the model's chosen
skill before dispatch to the host agent.

Design note: each skill stays flat and concrete with unambiguous parameters so the model
can compose them reliably into multi-step plans (check state → resolve a conflict → act →
verify). Each also carries a `category` (so the prompt groups related tools), a few `examples`
of how a user might ask for it (so messy speech-to-text maps to the right tool), and a `risk`
hint (`safe` vs `powerful`) surfaced as "[impactful]" so the model treats high-impact tools with
care. Anything not covered here is handled by `run_command`, whose output is screened for safety
and (for powerful / root commands) approved by the user.
"""
from dataclasses import dataclass


# Category labels (also the section headings in the rendered catalog), in display order.
MEDIA = "Media & sound"
APPS = "Apps, windows & input"
SCREEN = "Screen, power & connectivity"
INFO = "Information"
MEMORY = "Memory: favourites & preferences"
DISCOVERY = "Discovery & files"
SHELL = "Shell (fallback)"


@dataclass
class Skill:
    name: str
    description: str
    params: str  # human/LLM-facing description of expected params
    category: str = INFO
    examples: tuple[str, ...] = ()          # natural phrasings a user might say
    risk: str = "safe"                      # "safe" | "powerful" (advisory hint, marked [impactful])


SKILLS: list[Skill] = [
    # --- Media: system audio ---
    Skill("set_volume", "Change or mute the system audio output volume.",
          '{"level": 0-100}  OR  {"action": "mute|unmute|up|down"}', MEDIA,
          ("turn it up", "set the volume to 30", "mute the sound")),
    Skill("mic", "Mute, unmute, or toggle the microphone (the default input source). Use for "
                 "'mute my mic', 'unmute the microphone', 'am I muted'.",
          '{"action": "mute|unmute|toggle"}', MEDIA,
          ("mute my mic", "am I muted")),
    Skill("media_control", "Play/pause/skip the current media player.",
          '{"action": "playpause|play|pause|next|previous|stop"}', MEDIA,
          ("pause", "next track", "resume")),
    Skill("now_playing", "Say what song/media is currently playing.", "none", MEDIA,
          ("what's playing",)),
    # --- Media: local music library (~/Music) — ONLY for an explicit "play my local/library/files"
    #     request. For any ordinary "play X", use play_youtube instead. ---
    Skill("list_music", "Browse the user's LOCAL music under ~/Music like a folder tree. No "
                        "args lists the top level (albums/folders + loose tracks); pass 'path' "
                        "to step into a folder; pass 'query' to search the tree by name. Only "
                        "for explicit 'play my local music / from my library'.",
          '{} (top level)  OR  {"path": "Album Folder"}  OR  {"query": "text"}', MEDIA,
          ("browse my local music", "what's in my library")),
    Skill("play_music", "Play LOCAL music from ~/Music in a visible player window. Use ONLY for "
                        "an explicit 'play my local music / from my files'; otherwise use "
                        "play_youtube. Give exact track paths from list_music, a folder path "
                        "for a whole album, or a query to play everything matching.",
          '{"paths": ["Album/01 Song.mp3", ...]}  OR  {"paths": ["Album"]}  OR  {"query": "text"}  (+ optional "shuffle": true)',
          MEDIA, ("play my local music", "play that album from my files")),
    Skill("stop_playback", "Stop the LOCAL music that play_music started.", "none", MEDIA,
          ("stop my local music",)),
    # --- Media: YouTube / web playback (real Google Chrome over CDP) ---
    Skill("play_youtube", "Open Chrome and play from YouTube: searches the query and plays the "
                          "first result. THE default for any 'play X' — a song, artist, mood, "
                          "video, a channel's latest, a clip, anything. Use a clean query "
                          "(artist+title, or e.g. 'mrbeast latest'). Do not use local music "
                          "unless the user explicitly says 'my local/library/files'.",
          '{"query": "the weeknd blinding lights"}  OR  {"query": "mrbeast latest video"}', MEDIA,
          ("play blinding lights", "put on some lofi", "play mrbeast's latest")),
    Skill("stop_youtube", "End the YouTube playback in Chrome entirely. Only for 'stop the "
                          "video/music' — to switch to a different song/video just call "
                          "play_youtube again (it swaps in the same browser; no need to stop).",
          "none", MEDIA, ("stop the video", "stop the music")),
    Skill("youtube_volume", "Set the volume of the YouTube video itself (the Chrome playback), "
                            "separate from the system volume. Use for 'turn the video up/down', "
                            "'make YouTube louder/quieter', 'set the video volume to 40'.",
          '{"level": 0-100}  OR  {"action": "up|down|mute|unmute"}', MEDIA,
          ("turn the video down", "set youtube to 40")),
    Skill("youtube_control", "Control the playing YouTube video: pause, resume, skip to the "
                             "next video, restart, jump forward/back N seconds, or toggle the "
                             "video's fullscreen. Use action fullscreen for 'full screen / make "
                             "it full screen / exit fullscreen' — it's done through the player "
                             "(no key simulation), so it never raises the desktop remote-control "
                             "prompt; NEVER use press_keys for fullscreen.",
          '{"action": "play|pause|playpause|next|restart|fullscreen|fullscreen_on|fullscreen_off"}'
          '  OR  {"action": "seek", "seconds": 15 (or -15)}', MEDIA,
          ("pause the video", "skip this", "make it full screen", "skip ahead 30 seconds")),
    Skill("youtube_status", "Say what's playing on YouTube right now (title, playing/paused, volume).",
          "none", MEDIA, ("what's on youtube", "what's playing in the browser")),

    # --- Apps ---
    Skill("open_app", "Launch a desktop application.", '{"app": "google-chrome|konsole|dolphin|..."}',
          APPS, ("open chrome", "launch the terminal", "start the file manager")),
    Skill("open_url", "Open a website, link, file, or folder in its default app. Use for 'open "
                      "github.com', 'open my downloads folder', 'open this file'.",
          '{"url": "github.com"}  OR  {"url": "~/Downloads"}', APPS,
          ("open github.com", "open my downloads folder")),
    Skill("close_app", "Close/quit a running application (e.g. a browser and all its tabs).",
          '{"app": "google-chrome|konsole|..."}', APPS, ("close chrome", "quit the terminal"),
          "powerful"),
    Skill("running_apps", "List applications that currently have open windows.", "none", APPS,
          ("what apps are open",)),
    Skill("is_running", "Check whether a specific app/process is running (windows + processes).",
          '{"name": "konsole|chrome|code|..."}', APPS,
          ("is chrome open", "do I have a terminal open")),
    # --- Windows / tabs / keyboard ---
    Skill("list_windows", "List the titles of open windows.", "none", APPS,
          ("what's open", "what windows do I have")),
    Skill("count_windows", "Count open windows.", "none", APPS, ("how many windows are open",)),
    Skill("close_window", "Close a window matching part of its title.", '{"title": "text"}', APPS,
          ("close the settings window",)),
    Skill("focus_window", "Switch to / focus a window matching part of its title.",
          '{"title": "text"}', APPS, ("switch to my editor", "bring chrome to the front")),
    Skill("close_tab", "Close the current tab in the focused window (Ctrl+W).", "none", APPS,
          ("close this tab",)),
    Skill("new_tab", "Open a new tab in the focused window (Ctrl+T).", "none", APPS,
          ("open a new tab",)),
    Skill("press_keys", "Send a keyboard shortcut to the focused window.", '{"keys": "ctrl+alt+t"}',
          APPS, ("press ctrl+s", "hit escape")),
    Skill("type_text", "Type text into the focused window.", '{"text": "the text"}', APPS,
          ("type my email address",)),
    # --- Input devices ---
    Skill("list_input_devices", "List input devices (mice, keyboards, touchpad).", "none", APPS,
          ("what input devices do I have",)),
    Skill("set_input_device", "Enable or disable an input device (e.g. the touchpad).",
          '{"device": "name", "state": "enable|disable"}', APPS,
          ("disable the touchpad", "turn the touchpad back on"), "powerful"),
    Skill("list_projects", "List the user's code projects (folders under ~/Projects).", "none",
          APPS, ("what projects do I have",)),

    # --- Screen, power & connectivity ---
    Skill("get_brightness", "Report the current screen brightness as a percentage.", "none", SCREEN,
          ("how bright is the screen",)),
    Skill("brightness", "Set screen brightness.", '{"level": 0-100}', SCREEN,
          ("dim the screen", "set brightness to 50")),
    Skill("screenshot", "Take a full-screen screenshot.", "none", SCREEN, ("take a screenshot",)),
    Skill("lock_screen", "Lock the screen.", "none", SCREEN, ("lock the screen",)),
    Skill("unlock_screen", "Unlock the screen / dismiss the lock screen.", "none", SCREEN,
          ("unlock the screen",)),
    Skill("power_action", "Suspend, hibernate, reboot, shut down, or log out of the session. "
                          "Only use on an explicit request, and never invent it.",
          '{"action": "suspend|hibernate|reboot|shutdown|logout"}', SCREEN,
          ("suspend the machine", "reboot", "log out"), "powerful"),
    Skill("power_profile", "Get or set the power profile.",
          '{"profile": "power-saver|balanced|performance"}  (omit profile to just report it)',
          SCREEN, ("switch to power saver", "what's my power profile")),
    Skill("bluetooth_status", "List connected Bluetooth devices and how many there are.", "none",
          SCREEN, ("what bluetooth devices are connected",)),
    Skill("bluetooth_power", "Turn Bluetooth on or off.", '{"state": "on|off"}', SCREEN,
          ("turn on bluetooth", "turn bluetooth off"), "powerful"),
    Skill("wifi_status", "Report the current Wi-Fi / network connection.", "none", SCREEN,
          ("am I connected to wifi", "what network am I on")),
    Skill("wifi_power", "Turn Wi-Fi on or off.", '{"state": "on|off"}', SCREEN,
          ("turn wifi off", "turn the wifi back on"), "powerful"),

    # --- Information ---
    Skill("system_info", "Report system info: RAM, CPU load, disk, battery, or the machine "
                         "itself (distro, desktop environment, session type, hostname).",
          '{"what": "ram|cpu|disk|battery|system|all"}', INFO,
          ("how much RAM is free", "what's my battery at", "what distro is this")),
    Skill("weather", "Current weather and today's outlook for the user's location (read from "
                     "their KDE weather widget) or a named place. Use for 'what's the weather', "
                     "'will it rain', 'do I need a jacket', and as part of a morning briefing.",
          '{} (their location)  OR  {"location": "Nairobi"}', INFO,
          ("what's the weather", "will it rain today", "do I need a jacket")),
    Skill("news", "Top trending news headlines right now — overall top stories by default, "
                  "a topic section (world, business, technology, science, health, sports, "
                  "entertainment, nation), or a free-text search. Use for 'what's in the news', "
                  "'any tech news', 'what's happening in the world', 'latest on <X>'.",
          '{} (top stories)  OR  {"topic": "world|business|technology|science|health|sports|entertainment"}'
          '  OR  {"query": "text"}  (+ optional "limit": 1-10)', INFO,
          ("what's in the news", "any tech news today", "what's happening in the world",
           "latest headlines on the election")),
    Skill("web_search", "Search the web (keyless, via DuckDuckGo) for current facts and look-ups "
                        "the news and weather skills don't cover — definitions, people, places, "
                        "recent events, prices, scores, 'how/what/who is…'. Returns result "
                        "snippets to answer from; rely only on what comes back, and prefer the "
                        "dedicated news/weather skills when the question is squarely theirs.",
          '{"query": "text"}  (+ optional "limit": 1-8)', INFO,
          ("look that up online", "search the web for the fastest land animal",
           "who is the prime minister of Japan", "what's the score of the match")),
    Skill("notifications", "Read recent desktop notifications the agent has captured live "
                           "from the session bus (app, summary, body), plus Do Not Disturb "
                           "status. Use for 'what notifications do I have', 'did I miss anything', "
                           "'read my notifications'.", '{} (recent)  OR  {"limit": 20}', INFO,
          ("did I miss anything", "read my notifications")),
    Skill("clear_notifications", "Forget all the notifications the agent has recorded so far.",
          "none", INFO, ("clear my notifications",)),
    Skill("notify", "Show a desktop notification.", '{"message": "text"}', INFO,
          ("remind me on screen to stretch",)),

    # --- Memory: favourites & preferences (persisted; lets Aether recall what the user likes) ---
    Skill("list_favorites", "List the user's saved favourites (songs/videos/etc.). Call this to "
                            "resolve 'play my favourite song', 'put on a favourite'. If none are "
                            "saved it returns what they play most. Then play the chosen one with "
                            "play_youtube/play_music.",
          '{} (all)  OR  {"kind": "youtube|music"}', MEMORY,
          ("play my favourite", "put on a favourite song")),
    Skill("remember_favorite", "Save something as a favourite so it can be recalled by name "
                               "later (e.g. after playing a song the user loved, or on 'remember "
                               "this as a favourite'). 'value' is what to play back (a query or "
                               "track path).",
          '{"kind": "youtube|music", "label": "spoken name", "value": "query or path"}', MEMORY,
          ("remember this as a favourite", "save this song")),
    Skill("forget_favorite", "Remove something from the user's favourites.",
          '{"label": "name"}  (+ optional "kind")', MEMORY, ("forget that favourite",)),
    Skill("get_preference", "Recall a remembered setting/preference by key (e.g. their favourite "
                            "volume). Use before applying a 'usual'/'favourite' setting.",
          '{"key": "volume|youtube_volume|..."}', MEMORY, ("play it at my usual volume",)),
    Skill("set_preference", "Remember a setting/preference for later (e.g. 'set my usual volume "
                            "to 30', 'remember I like the video at 60').",
          '{"key": "volume", "value": 30}', MEMORY,
          ("set my usual volume to 30", "remember I like the video at 60")),
    Skill("play_history", "What the user has played most (recently/overall). Use for 'what do I "
                          "listen to', 'play something I play a lot'.",
          '{} OR {"source": "youtube|music", "limit": 5}', MEMORY,
          ("what do I listen to most",)),

    # --- Discovery & files ---
    Skill("capabilities", "List what actions actually work on this machine right now (which "
                          "tools are installed). Use when unsure whether something is supported here.",
          "none", DISCOVERY, ("what can you do on this machine",)),
    Skill("find_tool", "Search the installed programs for ones matching a keyword, to discover a "
                       "tool before using it (e.g. 'pdf', 'screenshot', 'convert').",
          '{"query": "pdf"}', DISCOVERY, ("do I have something for pdfs", "find a screenshot tool")),
    Skill("find_files", "Search the user's files by name (home folder by default).",
          '{"query": "resume"}  (+ optional "dir": "~/Documents")', DISCOVERY,
          ("find my resume", "where's my budget spreadsheet")),
    Skill("clipboard", "Read the clipboard, or copy text to it.",
          '{"action": "get"}  OR  {"action": "set", "text": "the text"}', DISCOVERY,
          ("what's on my clipboard", "copy this to the clipboard")),
    Skill("camera", "Take a photo with the webcam and save it.", "none", DISCOVERY,
          ("take a webcam photo",)),

    # --- Escape hatch: arbitrary shell ---
    Skill("run_command", "Run a shell command on the host. Use READ-ONLY commands (ps, pgrep, "
                         "ls, cat, grep, df, uptime, …) freely to investigate; they run "
                         "instantly. Powerful/destructive commands (and anything with sudo) are "
                         "screened and routed to the user for one-tap approval; only truly "
                         "catastrophic ones are refused outright.",
          '{"command": "the shell command"}', SHELL,
          ("how much disk space is left", "what's using my CPU", "free up some memory"), "powerful"),
]

SKILL_NAMES = {s.name for s in SKILLS}


def catalog_for_prompt(names: "set[str] | None" = None) -> str:
    """Render the catalog for the system prompt, grouped by category, with params and example
    phrasings so the model maps a user's words to the right tool reliably.

    Pass ``names`` to render only a subset (used for a sub-agent's focused toolset); the grouping
    and examples are preserved so specialists still get the rich hints.
    """
    chosen = SKILLS if names is None else [s for s in SKILLS if s.name in names]
    order: list[str] = []
    for s in chosen:
        if s.category not in order:
            order.append(s.category)
    out: list[str] = []
    for cat in order:
        out.append(f"\n## {cat}")
        for s in chosen:
            if s.category != cat:
                continue
            line = f"- {s.name}: {s.description} params: {s.params}"
            if s.risk == "powerful":
                line += "  [impactful]"
            if s.examples:
                line += "\n    e.g. " + " · ".join(f'"{e}"' for e in s.examples)
            out.append(line)
    return "\n".join(out).strip()
