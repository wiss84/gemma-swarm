import json
import re

def _try_parse_json(candidate: str) -> dict | None:
    """Try to parse JSON string with progressive error recovery."""
    if not candidate:
        return None
        
    # Try direct parsing first
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    
    # Try common fixes
    fixes = [
        # Fix 1: Safely strip outer double braces (leaves single braces intact)
        lambda s: re.sub(r'^\{\{(.*)\}\}$', r'{\1}', s.strip()),

        # Fix 2 & 4: Fix quotes on keys and replace single quotes around strings
        # Better to do this together to avoid breaking apostrophes inside words
        lambda s: re.sub(r"([{,]\s*)'?(\w+)'?(\s*:)", r'\1"\2"\3', s).replace("'", '"'),

        # Fix 3: Remove ALL trailing commas before any closing bracket (global)
        lambda s: re.sub(r',\s*([\]}])', r'\1', s),

        # Fix 5: Ensure commas between objects/arrays/quotes
        lambda s: re.sub(r'([\}\]" \d])\s*(?=[\{\["\w])', r'\1, ', s),

        # Fix 6: Clean up any double commas caused by the previous steps
        lambda s: re.sub(r',\s*,', ',', s)
    ]

    
    for fix in fixes:
        try:
            fixed = fix(candidate)
            return json.loads(fixed)
        except (json.JSONDecodeError, Exception):
            continue
    
    return None

def _extract_balanced_json(text: str, open_char: str, close_char: str) -> str | None:
    """
    Extract a balanced JSON object or array from text by tracking braces/brackets.
    Properly handles nested structures and ignores characters inside strings.
    """
    start_idx = text.find(open_char)
    if start_idx == -1:
        return None
    
    char_count = 0
    in_string = False
    escape_next = False
    
    for i in range(start_idx, len(text)):
        char = text[i]
        
        if escape_next:
            escape_next = False
            continue
            
        if char == '\\':
            escape_next = True
            continue
            
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
            
        if in_string:
            continue
            
        if char == open_char:
            char_count += 1
        elif char == close_char:
            char_count -= 1
            if char_count == 0:
                # Found the matching closing brace/bracket
                return text[start_idx:i+1]
    
    # No balanced closing found
    return None



def _extract_json(text: str) -> dict | None:
    """
    Extract JSON from LLM response text with explicit fixing of common malformations.
    Specifically handles double braces, missing quotes, trailing commas, etc.
    """
    if not text or not isinstance(text, str):
        return None
    
    # Clean up text
    original_text = text.strip()
    text = original_text
    
    # Strategy 1: Look for JSON in markdown code blocks
    code_block_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    code_block_match = re.search(code_block_pattern, text, re.DOTALL | re.IGNORECASE)
    if code_block_match:
        candidate = code_block_match.group(1).strip()
        result = _try_parse_json(candidate)
        if result is not None:
            return result
    
    # Strategy 2: Look for balanced braces/brackets in the text
    obj_candidate = _extract_balanced_json(text, '{', '}')
    if obj_candidate is not None:
        result = _try_parse_json(obj_candidate)
        if result is not None:
            return result
    
    arr_candidate = _extract_balanced_json(text, '[', ']')
    if arr_candidate is not None:
        result = _try_parse_json(arr_candidate)
        if result is not None:
            return result
    
    # Strategy 3: Try to parse the entire text as JSON
    if text.startswith('{') and text.endswith('}'):
        result = _try_parse_json(text)
        if result is not None:
            return result
    elif text.startswith('[') and text.endswith(']'):
        result = _try_parse_json(text)
        if result is not None:
            return result
    
    # If all extraction strategies fail, return None
    return None