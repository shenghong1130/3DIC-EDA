#!/usr/bin/env python3
"""Unified dispatch only: no Router, no scoring formulas, no shell strings."""
import argparse
import json
import math
import os
from common import PROJECT, EDA, existing_run_dir
from scoring import (REFERENCE, REFERENCE_LATEST, REFERENCES, OFFICIAL, SCORERS, ORIGINAL_SHA256, ScoringConflict,
                     reference_directory, official_directory, file_hash, summary, write_index)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--scorer', choices=list(SCORERS), default=REFERENCE_LATEST)
    parser.add_argument('--backend', choices=['wsl', 'native'], default='wsl' if os.name == 'nt' else 'native')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--memory-mb', type=int)
    parser.add_argument('--attempt')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    if args.scorer in REFERENCES and args.attempt:
        parser.error('--attempt is only supported by official_original')
    if args.scorer == OFFICIAL and args.memory_mb is not None:
        parser.error('--memory-mb is only supported by reference scorers')
    if args.scorer in REFERENCES and (args.timeout > 7200 or args.memory_mb is not None and not 256 <= args.memory_mb <= 8192):
        parser.error('Reference timeout must be <=7200 s and memory 256..8192 MiB')
    try:
        record = summary(args.case, args.label, args.scorer, attempt=args.attempt)
        if not record['router_complete']:
            raise ValueError('Router is incomplete or published Guide is missing; scoring is not started')
        print('Scorer         : ' + SCORERS[args.scorer], flush=True)
        if args.scorer in REFERENCES:
            import importlib
            evaluate_reference=importlib.import_module('evaluate_reference_20261103' if args.scorer==REFERENCE_LATEST else 'evaluate_reference')
            print('Source SHA256  : ' + evaluate_reference.source_identity()[0], flush=True)
            if record['status'] in ('REFERENCE_VALID', 'REFERENCE_INVALID') and record['scorer_source_sha256'] != evaluate_reference.source_identity()[0]:
                raise ScoringConflict('Existing reference report uses different/unknown source; preserve it without re-evaluation')
            existing = reference_directory(args.case, args.label, scorer=args.scorer)
            target = existing or PROJECT / 'outputs/reports/scoring' / args.scorer / args.case / args.label
            command = ['--case', args.case, '--label', args.label, '--backend', args.backend,
                       '--timeout', str(args.timeout), '--memory-mb', str(args.memory_mb or 1536)]
            call = evaluate_reference.main
        else:
            import evaluate
            digest = file_hash(EDA / '\u8bc4\u4f30\u7a0b\u5e8f' / 'score')
            if digest != ORIGINAL_SHA256:
                raise ScoringConflict('official_original requires the registered original official ELF SHA256')
            print('Binary SHA256  : ' + digest, flush=True)
            target = official_directory(existing_run_dir(args.case, args.label), args.attempt)
            command = ['--case', args.case, '--label', args.label, '--engine', 'latest',
                       '--backend', args.backend, '--timeout', str(args.timeout)]
            if args.attempt:
                command += ['--attempt', args.attempt]
            call = evaluate.main
        print('Result path    : ' + str(target), flush=True)
        if record['status'] in ('REFERENCE_VALID', 'REFERENCE_INVALID', 'PASS'):
            if record['identity_warnings']:
                raise ScoringConflict('Cannot reuse a report with UNKNOWN identity: ' + str(record['identity_warnings']))
            print('Reusing existing verified report; no scorer was launched.', flush=True)
            code = 0
        else:
            if target.exists():
                raise ScoringConflict('Existing incomplete/failed scoring directory is protected: ' + str(target))
            try:
                code = call(command)
            except SystemExit as error:
                # Existing CLIs use argparse.error for analysis/setup failures.
                # Keep their exit status and index any receipt they actually saved.
                code = error.code if isinstance(error.code, int) else 2
        record = summary(args.case, args.label, args.scorer, attempt=args.attempt)
        index = write_index(record, attempt=args.attempt) if record['status'] != 'NOT_EVALUATED' else None
        print(json.dumps({'scorer_id': record['scorer_id'], 'scorer_source_sha256': record['scorer_source_sha256'],
                          'scorer_binary_sha256': record['scorer_binary_sha256'], 'status': record['status'],
                          'report_path': record['report_path'], 'index_path': str(index)}, ensure_ascii=False, indent=2))
        return code
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
