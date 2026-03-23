"""Validator — System Prompt"""


def get_prompt(task: str, response_text: str) -> str:
    """
    Validator takes task and response as arguments since they vary per call.
    Unlike other agents this is not a fixed system prompt but a per-call prompt.
    """
    return f"""You are a response quality checker. Your goal is to determine if a response answers the task 

Task: {task}

Response to evaluate:
{response_text[:2000]}

Does this response adequately address the task?
Answer with ONLY this JSON (no other text):
{{"valid": true or false, "feedback": "brief reason if false, leave it blank if true"}}"""
