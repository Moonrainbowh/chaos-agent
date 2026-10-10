"""Apply only host-identified field corrections, then revalidate the whole review."""
import copy
import json

from ._parent_review_validation import parse_review, inspect_review


def repair_context(data):
    if not data.get('repair'):
        return None
    phase = data.get('phase')
    if data.get('repair_phase', phase) != phase:
        return None
    previous = data.get('repair_response')
    if previous is None:  # Existing persisted v1 reviews remain resumable.
        previous = data.get('rejected_initial' if phase == 'independent' else 'rejected_final')
    if previous is None:
        return None
    return {'previous_response': previous, 'errors': data['repair']}


def evaluate_response(text, snapshot, *, comparison):
    data = snapshot.data
    context = repair_context(data)
    if context and data.get('protocol_version', 1) >= 2:
        value, errors = _repair(text, context, data, comparison)
        effective = json.dumps(value, ensure_ascii=False) if value is not None else text
    else:
        value, errors = parse_review(text)
        effective = text
        if not errors and comparison and data.get('protocol_version', 1) >= 4:
            from ._parent_review_comparison import expand_comparison
            value, errors = expand_comparison(value, data)
            if value is not None:
                effective = json.dumps(value, ensure_ascii=False)
    if errors:
        return None, errors, effective
    if not isinstance(value, dict):
        return None, ({'path': '', 'message': 'expected one JSON review object'},), effective
    parsed, errors = inspect_review(value, data, comparison=comparison)
    return parsed, errors, effective


def _repair(text, context, data, comparison):
    previous, original_errors = parse_review(context['previous_response'])
    delta = (not original_errors and comparison and data.get('protocol_version', 1) >= 4
             and 'findings' not in previous)
    if delta:
        from ._parent_review_comparison import expand_comparison
        _, original_errors = expand_comparison(previous, data)
    if not original_errors:
        if delta:
            expanded, original_errors = expand_comparison(previous, data)
            if not original_errors:
                _, original_errors = inspect_review(expanded, data, comparison=comparison)
        else:
            _, original_errors = inspect_review(previous, data, comparison=comparison)
    # Recompute allowed paths from the actual draft, never trust model-selected scopes.
    allowed = {error['path'] for error in original_errors}
    response, errors = parse_review(text)
    if errors:
        return None, errors
    if not original_errors and response == {'replacements': []}:
        if delta:
            return expand_comparison(previous, data)
        return previous, ()  # Resumed draft's old errors no longer apply; preserve it exactly.
    if '' in allowed and 'replacements' not in response:
        if comparison and data.get('protocol_version', 1) >= 4:
            from ._parent_review_comparison import expand_comparison
            return expand_comparison(response, data)
        return response, ()  # A non-JSON draft has no valid fields to preserve.
    replacements = response.get('replacements')
    if not isinstance(replacements, list) or not 1 <= len(replacements) <= 32 or set(response) != {'replacements'}:
        return None, ({'path': '', 'message': 'repair must contain only 1..32 replacements for the reported paths'},)
    value = copy.deepcopy(previous)
    used = set()
    for item in replacements:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            return None, ({'path': '', 'message': 'each replacement needs a string path'},)
        path = item['path']
        if path not in allowed or (path in used and not path.endswith('/-')):
            return None, ({'path': '', 'message': 'replacement is outside the reported errors or repeats a field'},)
        used.add(path)
        remove = item.get('remove') is True
        if set(item) != ({'path', 'remove'} if remove else {'path', 'value'}):
            return None, ({'path': path, 'message': 'provide either value or remove:true'},)
    # Apply field changes before removals; remove descending original array indexes.
    def order(item):
        last = item['path'].rsplit('/', 1)[-1]
        return (item.get('remove') is True, -int(last) if last.isdigit() else 0)
    for item in sorted(replacements, key=order):
        path, remove = item['path'], item.get('remove') is True
        try:
            value = _replace(value, path, item.get('value'), remove)
        except (KeyError, IndexError, TypeError, ValueError):
            return None, ({'path': path, 'message': 'replacement path does not address the original draft'},)
    if delta:
        return expand_comparison(value, data)
    return value, ()


def _replace(value, pointer, replacement, remove):
    if pointer == '':
        if remove:
            raise ValueError('cannot remove root')
        return replacement
    if not pointer.startswith('/'):
        raise ValueError('expected JSON pointer')
    parts = [part.replace('~1', '/').replace('~0', '~') for part in pointer[1:].split('/')]
    target = value
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    key = parts[-1]
    if isinstance(target, list):
        if key == '-' and not remove:
            target.append(replacement)
        elif remove:
            del target[int(key)]
        else:
            target[int(key)] = replacement
    elif remove:
        del target[key]
    else:
        target[key] = replacement
    return value
