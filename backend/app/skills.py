"""The catalog of skills the assistant can perform on the host.

This catalog is the single source of truth: it is rendered into the LLM's system
prompt (so the model knows what it can do) and used to validate the model's chosen
skill before dispatch to the host agent.

Design note: each skill stays flat and concrete with unambiguous parameters so the model
can compose them reliably into multi-step plans (check state → resolve a conflict → act →
verify). Anything not covered here is handled by `run_command`, whose output is screened
for safety and (for risky / root commands) approved by the user.
"""
from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str
    params: str  # human/LLM-facing description of expected params


SKILLS: list[Skill] = [
    # --- Bluetooth ---
    Skill("bluetooth_status", "List connected Bluetooth devices and how many there are.", "none"),
    Skill("bluetooth_power", "Turn Bluetooth on or off.", '{"state": "on|off"}'),
    # --- Network ---
    Skill("wifi_status", "Report the current Wi-Fi / network connection.", "none"),
    Skill("wifi_power", "Turn Wi-Fi on or off.", '{"state": "on|off"}'),
    # --- Display ---
    Skill("get_brightness", "Report the current screen brightness as a percentage.", "none"),
    Skill("brightness", "Set screen brightness.", '{"level": 0-100}'),
    # --- Audio / media ---
    Skill("set_volume", "Change or mute the system audio output volume.",
          '{"level": 0-100}  OR  {"action": "mute|unmute|up|down"}'),
    Skill("mic", "Mute, unmute, or toggle the microphone (the default input source). Use for "
                 "'mute my mic', 'unmute the microphone', 'am I muted'.",
          '{"action": "mute|unmute|toggle"}'),
    Skill("media_control", "Play/pause/skip the current media player.",
          '{"action": "playpause|play|pause|next|previous|stop"}'),
    Skill("now_playing", "Say what song/media is currently playing.", "none"),
    # --- Local music library (~/Music) — ONLY for an explicit "play my local/library/files"
    #     request. For any ordinary "play X", use play_youtube instead. ---
    Skill("list_music", "Browse the user's LOCAL music under ~/Music like a folder tree. No "
                        "args lists the top level (albums/folders + loose tracks); pass 'path' "
                        "to step into a folder; pass 'query' to search the tree by name. Only "
                        "for explicit 'play my local music / from my library'.",
          '{} (top level)  OR  {"path": "Album Folder"}  OR  {"query": "text"}'),
    Skill("play_music", "Play LOCAL music from ~/Music in a visible player window. Use ONLY for "
                        "an explicit 'play my local music / from my files'; otherwise use "
                        "play_youtube. Give exact track paths from list_music, a folder path "
                        "for a whole album, or a query to play everything matching.",
          '{"paths": ["Album/01 Song.mp3", ...]}  OR  {"paths": ["Album"]}  OR  {"query": "text"}  (+ optional "shuffle": true)'),
    Skill("stop_playback", "Stop the LOCAL music that play_music started.", "none"),
    # --- YouTube / web playback (real Google Chrome over CDP) ---
    Skill("play_youtube", "Open Chrome and play from YouTube: searches the query and plays the "
                          "first result. THE default for any 'play X' — a song, artist, mood, "
                          "video, a channel's latest, a clip, anything. Use a clean query "
                          "(artist+title, or e.g. 'mrbeast latest'). Do not use local music "
                          "unless the user explicitly says 'my local/library/files'.",
          '{"query": "nataka kulewa"}  OR  {"query": "mrbeast latest video"}'),
    Skill("stop_youtube", "End the YouTube playback in Chrome entirely. Only for 'stop the "
                          "video/music' — to switch to a different song/video just call "
                          "play_youtube again (it swaps in the same browser; no need to stop).",
          "none"),
    Skill("youtube_volume", "Set the volume of the YouTube video itself (the Chrome playback), "
                            "separate from the system volume. Use for 'turn the video up/down', "
                            "'make YouTube louder/quieter', 'set the video volume to 40'.",
          '{"level": 0-100}  OR  {"action": "up|down|mute|unmute"}'),
    Skill("youtube_control", "Control the playing YouTube video: pause, resume, skip to the "
                             "next video, restart, jump forward/back N seconds, or toggle the "
                             "video's fullscreen. Use action fullscreen for 'full screen / make "
                             "it full screen / exit fullscreen' — it's done through the player "
                             "(no key simulation), so it never raises the desktop remote-control "
                             "prompt; NEVER use press_keys for fullscreen.",
          '{"action": "play|pause|playpause|next|restart|fullscreen|fullscreen_on|fullscreen_off"}'
          '  OR  {"action": "seek", "seconds": 15 (or -15)}'),
    Skill("youtube_status", "Say what's playing on YouTube right now (title, playing/paused, volume).",
          "none"),
    # --- Projects (~/Projects) ---
    Skill("list_projects", "List the user's code projects (folders under ~/Projects).", "none"),
    # --- Apps ---
    Skill("open_app", "Launch a desktop application.", '{"app": "google-chrome|konsole|dolphin|..."}'),
    Skill("open_url", "Open a website, link, file, or folder in its default app. Use for 'open "
                      "github.com', 'open my downloads folder', 'open this file'.",
          '{"url": "github.com"}  OR  {"url": "~/Downloads"}'),
    Skill("close_app", "Close/quit a running application (e.g. a browser and all its tabs).",
          '{"app": "google-chrome|konsole|..."}'),
    Skill("running_apps", "List applications that currently have open windows.", "none"),
    Skill("is_running", "Check whether a specific app/process is running (windows + processes).",
          '{"name": "konsole|chrome|code|..."}'),
    # --- Windows / tabs / keyboard ---
    Skill("list_windows", "List the titles of open windows.", "none"),
    Skill("count_windows", "Count open windows.", "none"),
    Skill("close_window", "Close a window matching part of its title.", '{"title": "text"}'),
    Skill("focus_window", "Switch to / focus a window matching part of its title.", '{"title": "text"}'),
    Skill("close_tab", "Close the current tab in the focused window (Ctrl+W).", "none"),
    Skill("new_tab", "Open a new tab in the focused window (Ctrl+T).", "none"),
    Skill("press_keys", "Send a keyboard shortcut to the focused window.",
          '{"keys": "ctrl+alt+t"}'),
    Skill("type_text", "Type text into the focused window.", '{"text": "the text"}'),
    # --- Input devices ---
    Skill("list_input_devices", "List input devices (mice, keyboards, touchpad).", "none"),
    Skill("set_input_device", "Enable or disable an input device (e.g. the touchpad).",
          '{"device": "name", "state": "enable|disable"}'),
    # --- System ---
    Skill("system_info", "Report system info: RAM, CPU load, disk, battery, or the machine "
                         "itself (distro, desktop environment, session type, hostname).",
          '{"what": "ram|cpu|disk|battery|system|all"}'),
    Skill("power_profile", "Get or set the power profile.",
          '{"profile": "power-saver|balanced|performance"}  (omit profile to just report it)'),
    Skill("screenshot", "Take a full-screen screenshot.", "none"),
    Skill("lock_screen", "Lock the screen.", "none"),
    Skill("unlock_screen", "Unlock the screen / dismiss the lock screen.", "none"),
    Skill("power_action", "Suspend, hibernate, reboot, shut down, or log out of the session. "
                          "Only use on an explicit request, and never invent it.",
          '{"action": "suspend|hibernate|reboot|shutdown|logout"}'),
    Skill("notifications", "Read recent desktop notifications the agent has captured live "
                           "from the session bus (app, summary, body), plus Do Not Disturb "
                           "status. Use for 'what notifications do I have', 'did I miss anything', "
                           "'read my notifications'.", '{} (recent)  OR  {"limit": 20}'),
    Skill("clear_notifications", "Forget all the notifications the agent has recorded so far.", "none"),
    Skill("notify", "Show a desktop notification.", '{"message": "text"}'),
    Skill("weather", "Current weather and today's outlook for the user's location (read from "
                     "their KDE weather widget) or a named place. Use for 'what's the weather', "
                     "'will it rain', 'do I need a jacket', and as part of a morning briefing.",
          '{} (their location)  OR  {"location": "Nairobi"}'),
    # --- Memory: favourites & preferences (persisted; lets Aether recall what the user likes) ---
    Skill("list_favorites", "List the user's saved favourites (songs/videos/etc.). Call this to "
                            "resolve 'play my favourite song', 'put on a favourite'. If none are "
                            "saved it returns what they play most. Then play the chosen one with "
                            "play_youtube/play_music.",
          '{} (all)  OR  {"kind": "youtube|music"}'),
    Skill("remember_favorite", "Save something as a favourite so it can be recalled by name "
                               "later (e.g. after playing a song the user loved, or on 'remember "
                               "this as a favourite'). 'value' is what to play back (a query or "
                               "track path).",
          '{"kind": "youtube|music", "label": "spoken name", "value": "query or path"}'),
    Skill("forget_favorite", "Remove something from the user's favourites.",
          '{"label": "name"}  (+ optional "kind")'),
    Skill("get_preference", "Recall a remembered setting/preference by key (e.g. their favourite "
                            "volume). Use before applying a 'usual'/'favourite' setting.",
          '{"key": "volume|youtube_volume|..."}'),
    Skill("set_preference", "Remember a setting/preference for later (e.g. 'set my usual volume "
                            "to 30', 'remember I like the video at 60').",
          '{"key": "volume", "value": 30}'),
    Skill("play_history", "What the user has played most (recently/overall). Use for 'what do I "
                          "listen to', 'play something I play a lot'.",
          '{} OR {"source": "youtube|music", "limit": 5}'),
    # --- Discovery & extras ---
    Skill("capabilities", "List what actions actually work on this machine right now (which "
                          "tools are installed). Use when unsure whether something is supported here.",
          "none"),
    Skill("find_tool", "Search the installed programs for ones matching a keyword, to discover a "
                       "tool before using it (e.g. 'pdf', 'screenshot', 'convert').",
          '{"query": "pdf"}'),
    Skill("find_files", "Search the user's files by name (home folder by default).",
          '{"query": "resume"}  (+ optional "dir": "~/Documents")'),
    Skill("clipboard", "Read the clipboard, or copy text to it.",
          '{"action": "get"}  OR  {"action": "set", "text": "the text"}'),
    Skill("camera", "Take a photo with the webcam and save it.", "none"),
    # --- Escape hatches ---
    Skill("run_command", "Run a shell command on the host. Use READ-ONLY commands (ps, pgrep, "
                         "ls, cat, grep, df, uptime, …) freely to investigate; they run "
                         "instantly. Prefix with 'sudo' for admin actions (user approval "
                         "required). Destructive commands are blocked.",
          '{"command": "the shell command"}'),
]

SKILL_NAMES = {s.name for s in SKILLS}


def catalog_for_prompt() -> str:
    return "\n".join(f"- {s.name}: {s.description} params: {s.params}" for s in SKILLS)
