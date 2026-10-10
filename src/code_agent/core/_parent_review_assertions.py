"""Check JSON witness consistency, never execute or prove source behavior."""
import math


def _json_value(value):
    """Accept only bounded ordinary JSON values, with no user-defined equality."""
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 32 or count > 20000:
            return False
        kind = type(item)
        if kind in (type(None), bool, int, str):
            continue
        if kind is float:
            if not math.isfinite(item):
                return False
        elif kind in (list, dict):
            if len(item) + count + len(stack) > 20000:
                return False
            if kind is dict:
                if any(type(key) is not str for key in item):
                    return False
                children = item.values()
            else:
                children = item
            stack.extend((child, depth + 1) for child in children)
        else:
            return False
    return True


def _equal(left, right):
    """Compare validated JSON; booleans are distinct from numeric values."""
    left_kind, right_kind = type(left), type(right)
    if left_kind in (int, float) and right_kind in (int, float):
        return left == right
    if left_kind is not right_kind:
        return False
    if left_kind is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if left_kind is dict:
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    return left == right


def _unknowns(value):
    from ._parent_review_validation import text_value
    return isinstance(value, list) and len(value) <= 32 and all(text_value(x) for x in value)


def _witness(value, expected, *, missed):
    """Check only reported values and witness shape, never the proposed program."""
    from ._parent_review_validation import text_value
    fields = {'actual', 'fault', 'counterexample'} if missed else {'actual', 'fault'}
    if (not isinstance(value, dict) or set(value) != fields
            or not text_value(value['fault']) or not _json_value(value['actual'])):
        return False
    if _equal(value['actual'], expected) is not missed:
        return False
    if missed:
        counterexample = value['counterexample']
        return (isinstance(counterexample, dict)
                and set(counterexample) == {'input', 'expected', 'actual'}
                and all(_json_value(v) for v in counterexample.values())
                and not _equal(counterexample['actual'], counterexample['expected']))
    return True


def _ordinary(record, pointer, errors):
    from ._parent_review_validation import text_value

    def reject(field, message):
        escaped = field.replace('~', '~0').replace('/', '~1')
        errors.append({'path': pointer + '/' + escaped, 'message': message})

    fields = {'id', 'citations', 'input', 'observed', 'expected', 'current',
              'detected', 'missed', 'unknowns', 'scope', 'current_outcome'}
    for field in record.keys() - fields:
        reject(str(field), 'unexpected assertion field; remove it')
    if 'scope' in record and record['scope'] != 'witness_only':
        reject('scope', 'scope must be witness_only; finite witnesses do not prove all or none')
    record['scope'] = 'witness_only'
    # Host-derived values are never accepted as a model fact, even on invalid records.
    record.pop('current_outcome', None)
    valid_values = True
    for field in ('input', 'expected'):
        if field not in record or not _json_value(record[field]):
            reject(field, 'expected a bounded ordinary JSON value')
            valid_values = False
    if not text_value(record.get('observed')):
        reject('observed', 'describe the value compared by the assertion')
    unknowns = record.get('unknowns')
    if not _unknowns(unknowns):
        reject('unknowns', 'expected at most 32 nonempty unknown descriptions or []')
    elif not unknowns and (record.get('detected', False) is None or record.get('missed', False) is None):
        reject('unknowns', 'a null witness requires an explicit unknown reason')
    current = record.get('current')
    valid_current = (isinstance(current, dict) and set(current) == {'actual', 'trace'}
                     and _json_value(current['actual']) and text_value(current['trace']))
    if not valid_current:
        reject('current', 'expected bounded JSON actual and a nonempty static trace')
    elif valid_values:
        record['current_outcome'] = 'pass' if _equal(current['actual'], record['expected']) else 'fail'
    for field in ('detected', 'missed'):
        if field not in record:
            reject(field, 'expected a concrete witness or null with explicit unknowns')
        elif record[field] is not None and (not valid_values or not _witness(
                record[field], record['expected'], missed=field == 'missed')):
            reject(field, ('missed witness must pass the assertion and include a failing counterexample'
                           if field == 'missed' else 'detected witness must fail the assertion'))


def inspect_assertions(parsed, data, errors):
    """Bind citations and derive literal outcomes in place, appending field errors.

    This checks consistency of reported ordinary JSON values, not whether a trace
    follows the source, a proposed behavior violates its contract, or prose is true.
    Unsupported observations require an explicit unknown record. No code is run.
    """
    from ._parent_review_validation import bind_citations, text_value
    table = parsed.get('assertion_checks')
    if not isinstance(table, list) or len(table) > 64:
        errors.append({'path': '/assertion_checks', 'message': 'expected at most 64 assertion records'})
        return
    if not table and (not _unknowns(parsed.get('unknowns')) or not parsed['unknowns']):
        errors.append({'path': '/assertion_checks',
                       'message': 'an empty assertion table requires explicit report unknowns'})
    identifiers = set()
    for i, record in enumerate(table):
        pointer = '/assertion_checks/' + str(i)
        if not isinstance(record, dict):
            errors.append({'path': pointer, 'message': 'expected an assertion record'})
            continue
        identifier = record.get('id')
        if not text_value(identifier) or identifier in identifiers:
            errors.append({'path': pointer + '/id', 'message': 'expected a unique nonempty assertion ID'})
        else:
            identifiers.add(identifier)
        record['citations'] = bind_citations(record.get('citations'), data['sources'],
                                             pointer + '/citations', errors, compact=True)
        if 'unknown' in record:
            if set(record) != {'id', 'citations', 'unknown'} or not text_value(record['unknown']):
                errors.append({'path': pointer,
                               'message': 'unsupported records contain only id, citations and nonempty unknown'})
        else:
            _ordinary(record, pointer, errors)
