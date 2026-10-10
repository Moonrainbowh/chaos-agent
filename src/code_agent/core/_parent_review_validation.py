"""Bounded review validation and host-bound citations, never semantic proof."""
import copy
import json
import math
import re


def _reject_constant(value):
    raise ValueError('non-finite JSON constant')


def text_value(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096


def parse_review(text):
    fenced = re.fullmatch(r'\s*```json\s*\n([\s\S]*?)\n```\s*', text)
    try:
        value = json.loads(fenced.group(1) if fenced else text, parse_constant=_reject_constant)
        if not isinstance(value, dict):
            raise ValueError()
        stack, count = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            count += 1
            if depth > 32 or count > 20000:
                raise ValueError('review JSON exceeds bounded structure')
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError('non-finite JSON number')
            children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
            stack.extend((child, depth + 1) for child in children)
        return value, ()
    except (ValueError, TypeError, RecursionError):
        return None, ({'path': '', 'message': 'expected one JSON review object'},)


def visible_runtime(data, comparison):
    return [r for r in data.get('runtime_evidence', ())
            if r.get('phase') == 'independent' or (comparison and r.get('phase') == 'comparison')]


def paragraphs(text):
    return [{'id': i, 'text': p.strip()} for i, p in enumerate(
        (p for p in text.split('\n\n') if p.strip()), 1)]


def _heading_blocks(text):
    """Classify ATX-only blocks, retaining fence state and source indentation."""
    fence = None
    blocks = (p for p in text.replace('\r\n', '\n').split('\n\n') if p.strip())
    for identifier, block in enumerate(blocks, 1):
        heading_only = True
        for line in block.splitlines():
            if not line.strip():
                continue
            marker = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', line)
            if fence is not None:
                heading_only = False
                if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                        and not marker[2].strip()):
                    fence = None
            elif marker:
                heading_only = False
                # Backtick fence info cannot itself contain a backtick.
                if marker[1][0] != '`' or '`' not in marker[2]:
                    fence = (marker[1][0], len(marker[1]))
            elif not re.match(r' {0,3}#{1,6}(?:[ \t]|$)', line):
                heading_only = False
        yield {'id': identifier, 'text': block.strip()}, heading_only


def comparison_paragraphs(text, *, compact=True):
    """Return required comparison groups with original body IDs and title text.

    Compact reviews attach ATX-only blocks to the next body (trailing ones to
    the last body). Heading-only reports retain every original required ID.
    Legacy reviews retain their original paragraph protocol unchanged.
    """
    if not compact:
        return paragraphs(text)
    grouped, pending = [], []
    for paragraph, heading_only in _heading_blocks(text):
        if heading_only:
            pending.append(paragraph)
        else:
            grouped.append({'id': paragraph['id'], 'text': '\n\n'.join(
                [p['text'] for p in pending] + [paragraph['text']])})
            pending = []
    if grouped and pending:
        grouped[-1]['text'] += '\n\n' + '\n\n'.join(p['text'] for p in pending)
    return grouped or pending


def bind_citations(citations, sources, pointer, errors, *, compact=False, optional=False):
    if not isinstance(citations, list) or not (0 if optional else 1) <= len(citations) <= 32:
        errors.append({'path': pointer, 'message': 'expected ' + ('0' if optional else '1') + '..32 source citations'})
        return []
    bound = []
    by_path = {s['path']: s for s in sources}
    for i, citation in enumerate(citations):
        path = pointer + '/' + str(i)
        if not isinstance(citation, dict):
            errors.append({'path': path, 'message': 'expected a source citation object'})
            continue
        source = by_path.get(citation.get('path')) if isinstance(citation.get('path'), str) else None
        if source is None:
            errors.append({'path': path + '/path', 'message': 'expected a supplied source path'})
            continue
        if ('version' in citation or not compact) and citation.get('version') != source['version']:
            errors.append({'path': path + '/version', 'message': 'version differs from the frozen source; omit it for host binding' if compact else 'expected frozen source version'})
        lines = source['text'].splitlines(keepends=True)
        start, end = citation.get('start_line'), citation.get('end_line')
        lower = 1 if lines else 0
        if type(start) is not int or not lower <= start <= len(lines):
            errors.append({'path': path + '/start_line', 'message': f'expected integer physical line {lower}..{len(lines)}'})
            continue
        if type(end) is not int or not start <= end <= len(lines):
            errors.append({'path': path + '/end_line', 'message': f'expected integer physical line {start}..{len(lines)}'})
            continue
        excerpt = ''.join(lines[start - 1:end]) if lines else ''
        if 'quote' in citation or not compact:
            quote = citation.get('quote')
            if ((lines and (not text_value(quote) or quote not in excerpt)) or (not lines and quote != '')):
                errors.append({'path': path + '/quote', 'message': 'quote must occur exactly in the selected frozen lines; omit it for host extraction' if compact else 'quote must occur exactly in selected lines'})
        bound.append({'path': source['path'], 'version': source['version'], 'start_line': start,
                      'end_line': end, 'quote': excerpt if compact else citation.get('quote')})
    return bound


def inspect_review(value, data, *, comparison):
    parsed = copy.deepcopy(value)
    errors = []
    compact = data.get('protocol_version', 1) >= 2
    requirements = {r['id']: r for r in data['requirements'] if comparison or r['id'] != 'child_objective'}
    runtime = {r['id']: r for r in visible_runtime(data, comparison)}
    findings = parsed.get('findings')
    if not isinstance(findings, list) or len(findings) > 64:
        return parsed, ({'path': '/findings', 'message': 'expected at most 64 findings covering supplied requirements'},)
    seen = set()
    for i, finding in enumerate(findings):
        pointer = '/findings/' + str(i)
        if not isinstance(finding, dict):
            errors.append({'path': pointer, 'message': 'expected a finding object'})
            continue
        identifier = finding.get('requirement_id')
        if not isinstance(identifier, str) or identifier not in requirements:
            errors.append({'path': pointer, 'message': 'unknown requirement; remove this finding'})
            continue
        if identifier in seen:
            errors.append({'path': pointer, 'message': 'duplicate requirement; remove this finding'})
            continue
        seen.add(identifier)
        if not text_value(finding.get('judgment')):
            errors.append({'path': pointer + '/judgment', 'message': 'expected nonempty judgment of at most 4096 characters'})
        refs = finding.get('runtime_refs', [])
        eligible = compact and requirements[identifier].get('evidence_kind') == 'source_or_runtime'
        valid_refs = isinstance(refs, list) and len(refs) <= 32 and all(isinstance(r, str) and r in runtime for r in refs)
        if not valid_refs or (refs and not eligible):
            errors.append({'path': pointer + '/runtime_refs', 'message': 'runtime references must be available in this phase and permitted for this requirement'})
        elif eligible and not refs and not finding.get('citations'):
            errors.append({'path': pointer + '/runtime_refs', 'message': 'provide a supplied runtime ID for process facts, or add source citations for source judgments'})
        finding['citations'] = bind_citations(finding.get('citations', []), data['sources'], pointer + '/citations', errors,
                                             compact=compact, optional=eligible and valid_refs and bool(refs))
        if eligible and valid_refs:
            finding['runtime_evidence'] = [runtime[r] for r in refs]
        else:
            finding.pop('runtime_evidence', None)
    for identifier in requirements.keys() - seen:
        errors.append({'path': '/findings/-', 'message': 'append missing requirement: ' + identifier})
    unknowns = parsed.get('unknowns')
    if not isinstance(unknowns, list) or len(unknowns) > 32 or any(not text_value(x) for x in unknowns):
        errors.append({'path': '/unknowns', 'message': 'explicit unknowns: expected at most 32 nonempty strings or []'})
    if comparison:
        _comparisons(parsed, data, errors, compact)
    if data.get('protocol_version', 1) >= 3:
        from ._parent_review_assertions import inspect_assertions
        inspect_assertions(parsed, data, errors)
    else:
        parsed.pop('assertion_checks', None)  # Older protocols never validated this field.
    return parsed, tuple(errors[:32])


def _comparisons(parsed, data, errors, compact):
    comparisons = parsed.get('comparisons')
    if not isinstance(comparisons, list) or not 1 <= len(comparisons) <= 64:
        errors.append({'path': '/comparisons', 'message': 'expected 1..64 advisory comparisons'})
        return
    expected = {p['id'] for p in comparison_paragraphs(data['advisory'], compact=compact)}
    supplied = {p['id'] for p in paragraphs(
        data['advisory'].replace('\r\n', '\n') if compact else data['advisory'])}
    covered = set()
    for i, item in enumerate(comparisons):
        pointer = '/comparisons/' + str(i)
        if not isinstance(item, dict):
            errors.append({'path': pointer, 'message': 'expected comparison object'})
            continue
        for field in ('child_claim', 'assessment', 'rationale'):
            if not text_value(item.get(field)):
                errors.append({'path': pointer + '/' + field, 'message': 'expected nonempty text of at most 4096 characters'})
        ids = item.get('paragraph_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 64 or any(type(p) is not int or p not in supplied for p in ids):
            errors.append({'path': pointer + '/paragraph_ids', 'message': 'expected supplied advisory paragraph IDs'})
        else:
            covered.update(ids)
        if item.get('runtime_refs'):
            errors.append({'path': pointer + '/runtime_refs', 'message': 'advisory comparisons require source citations, not runtime references'})
        item.pop('runtime_evidence', None)
        item['citations'] = bind_citations(item.get('citations'), data['sources'], pointer + '/citations', errors, compact=compact)
    if expected - covered:
        errors.append({'path': '/comparisons/-', 'message': 'append comparison for uncovered paragraphs: ' + str(sorted(expected - covered))})
