"""
Gemma Swarm — Voice Notifier
==============================
Speaks calendar event alerts aloud via the system's text-to-speech engine.
Uses PowerShell's built-in System.Speech.Synthesis.SpeechSynthesizer — no
additional packages required, works offline on any Windows machine.

Called directly by fire_due_notifications() in calendar_reminder.py at the
same moment the Slack message is posted. Runs in the same background thread,
so no extra process or daemon is needed.

Settings in autonomous_settings.json (under "calendar_notify"):
  "voice_alerts": true/false  — enable/disable voice alerts
  "voice_llm":    true/false  — use LLM to generate a natural-sounding phrase
                                instead of the hardcoded template
"""

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


def _get_user_name() -> str:
    """Read the user's name from user_preferences.json. Returns empty string if not set."""
    try:
        import json
        from pathlib import Path
        prefs_file = Path(__file__).parent.parent.parent / "user_preferences.json"
        if prefs_file.exists():
            prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
            return prefs.get("name", "").strip()
    except Exception:
        pass
    return ""


def speak(text: str) -> None:
    """
    Speak the given text aloud using the OS text-to-speech engine.
    Non-blocking — returns immediately while speech plays in background.
    Safe to call from any thread.
    """
    if sys.platform != "win32":
        _speak_unix(text)
        return
    _speak_windows(text)


def speak_calendar_alert(event_title: str, offset_minutes: int, use_llm: bool = False) -> None:
    """
    Build and speak a calendar alert.
    If use_llm=True, asks the LLM to produce a natural phrase first,
    personalised with the user's name if available.
    Falls back to the hardcoded template if the LLM call fails.
    """
    if use_llm:
        spoken_text = _llm_alert(event_title, offset_minutes)
    else:
        spoken_text = build_calendar_alert(event_title, offset_minutes)

    speak(spoken_text)


def build_calendar_alert(event_title: str, offset_minutes: int) -> str:
    """
    Build a short spoken alert using the hardcoded template.

    Examples:
      "Client Meeting in 15 minutes"
      "Team Standup in 1 hour"
      "Project Review in 2 hours and 30 minutes"
    """
    return f"{event_title} in {_format_time_phrase(offset_minutes)}"


def _format_time_phrase(offset_minutes: int) -> str:
    if offset_minutes < 60:
        return f"{offset_minutes} minute{'s' if offset_minutes != 1 else ''}"
    elif offset_minutes % 60 == 0:
        hours = offset_minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        hours   = offset_minutes // 60
        minutes = offset_minutes % 60
        return (
            f"{hours} hour{'s' if hours != 1 else ''} "
            f"and {minutes} minute{'s' if minutes != 1 else ''}"
        )


def _llm_alert(event_title: str, offset_minutes: int) -> str:
    """
    Ask the LLM to produce a short, natural spoken reminder sentence,
    personalised with the user's name if available.
    Falls back to the hardcoded template on any failure.
    """
    try:
        from autonomous import pipeline_agent

        user_name    = _get_user_name()
        time_phrase  = _format_time_phrase(offset_minutes)
        name_line    = f"Address the user by their first name: {user_name}." if user_name else ""

        prompt = (
            f"Write a single short spoken reminder for this calendar event.\n\n"
            f"Event: {event_title}\n"
            f"Time until event: {time_phrase}\n"
            f"{name_line}\n\n"
            f"Rules:\n"
            f"- Maximum 15 words\n"
            f"- Natural, conversational tone — as if a helpful assistant is reminding you\n"
            f"- Must include the event name and the time remaining\n"
            f"- No punctuation that would sound odd when spoken aloud\n"
            f"- Output ONLY the reminder sentence, nothing else"
        )

        response = pipeline_agent.ask(prompt)

        if response and not response.startswith("[LLM error"):
            clean = response.strip().strip('"').strip("'")
            if clean:
                logger.info(f"[voice_notifier] LLM alert: {clean}")
                return clean

    except Exception as e:
        logger.warning(f"[voice_notifier] LLM alert generation failed: {e}")

    # Fallback to hardcoded template
    return build_calendar_alert(event_title, offset_minutes)


def _speak_windows(text: str) -> None:
    """
    Speak via PowerShell's SpeechSynthesizer.
    Spawns a detached PowerShell process — doesn't block the caller thread.
    PowerShell handles its own COM message loop, so no SAPI threading issues.
    """
    safe_text = text.replace("'", " ").replace('"', " ")

    ps_command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Speak('{safe_text}');"
    )

    try:
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        logger.info(f"[voice_notifier] Speaking: {text}")
    except Exception as e:
        logger.warning(f"[voice_notifier] Could not speak alert: {e}")


def _speak_unix(text: str) -> None:
    """Speak via 'say' (macOS) or 'espeak' (Linux)."""
    import shutil
    safe_text = text.replace("'", " ").replace('"', " ")

    if sys.platform == "darwin" and shutil.which("say"):
        try:
            subprocess.Popen(["say", safe_text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.warning(f"[voice_notifier] 'say' failed: {e}")
    elif shutil.which("espeak"):
        try:
            subprocess.Popen(["espeak", safe_text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.warning(f"[voice_notifier] 'espeak' failed: {e}")
    else:
        logger.warning("[voice_notifier] No TTS engine found (tried 'say', 'espeak').")
