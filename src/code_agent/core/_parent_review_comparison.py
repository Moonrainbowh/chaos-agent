"""Expand explicit comparison confirmations and updates into a full review."""
import copy

from ._parent_review_validation import parse_review, inspect_review

FIELDS = {'confirmed_findings', 'finding_updates', 'confirmed_assertions',
          'assertion_updates', 'unknowns', 'comparisons'}


def is_comparison_delta(value):
    """Distinguish saved deltas from already expanded repair drafts."""
    return isinstance(value, dict) and 'findings' not in value


def inspect_comparison_draft(value, data):
    """Revalidate a saved delta or a complete effective draft for repair prompts."""
    if is_comparison_delta(value):
        value, errors = expand_comparison(value, data)
        if errors:
            return value, errors
    return inspect_review(value, data, comparison=True)


def expand_comparison(value, data):
    """Preserve each initial ID exactly once; full validation follows expansion.

    Confirmations bind only the saved independent review in this snapshot.
    Updates replace entire records by ID. Newly required findings and new
    assertion records can be added; an unknown confirmation cannot add a record.
    """
    errors = []
    def reject(path, message):
        errors.append({'path': path, 'message': message})
    if not isinstance(value, dict) or set(value) != FIELDS:
        return None, ({'path': '', 'message': 'expected only the six comparison delta fields'},)
    initial, initial_errors = parse_review(data.get('initial', ''))
    if not initial_errors:
        initial, initial_errors = inspect_review(initial, data, comparison=False)
    if initial_errors:
        return None, ({'path': '', 'message': 'saved independent review does not pass validation'},)
    merged = copy.deepcopy(initial)
    for confirm_key, update_key, target_key, id_key in (
        ('confirmed_findings', 'finding_updates', 'findings', 'requirement_id'),
        ('confirmed_assertions', 'assertion_updates', 'assertion_checks', 'id')):
        previous = {r[id_key]: r for r in initial[target_key]}
        confirmed, updates = value[confirm_key], value[update_key]
        if not isinstance(confirmed, list) or len(confirmed) > 64:
            reject('/' + confirm_key, 'expected at most 64 initial IDs')
            confirmed = []
        if not isinstance(updates, list) or len(updates) > 64:
            reject('/' + update_key, 'expected at most 64 complete records')
            updates = []
        seen, replacements = set(), {}
        for index, identifier in enumerate(confirmed):
            path = '/' + confirm_key + '/' + str(index)
            if not isinstance(identifier, str) or identifier not in previous:
                reject(path, 'confirmation must name an initial ID')
            elif identifier in seen:
                reject(path, 'duplicate confirmation')
            else:
                seen.add(identifier)
        for index, record in enumerate(updates):
            path = '/' + update_key + '/' + str(index)
            identifier = record.get(id_key) if isinstance(record, dict) else None
            if not isinstance(identifier, str) or not identifier.strip():
                reject(path, 'update must contain a complete record with its ID')
            elif identifier in seen:
                reject(path, 'ID is confirmed and updated, or updated more than once')
            else:
                seen.add(identifier)
                replacements[identifier] = record
        missing = previous.keys() - seen
        if missing:
            reject('/' + confirm_key + '/-',
                   'confirm or update every initial ID: ' + ', '.join(sorted(missing)))
        merged[target_key] = [copy.deepcopy(replacements.get(identifier, record))
            for identifier, record in previous.items()]
        merged[target_key] += [copy.deepcopy(record) for identifier, record in replacements.items()
                               if identifier not in previous]
    if errors:
        return None, tuple(errors[:32])
    merged['unknowns'] = copy.deepcopy(value['unknowns'])
    merged['comparisons'] = copy.deepcopy(value['comparisons'])
    return merged, ()
