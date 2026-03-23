"""
Gemma Swarm — Slack Utilities
================================
Sub-modules split from slack_app.py for maintainability.

    thread_state      — ThreadState dataclass, registry, status helpers
    rate_callbacks    — Rate limit countdown callbacks per agent
    workspace         — Workspace selection blocks, modal, activation
    handlers_confirm  — confirm_approve / confirm_reject button handlers
    handlers_email    — email_approve / email_reject_feedback / email_feedback_modal
    handlers_interrupt — interrupt_combine / interrupt_fresh / interrupt_queue
"""
